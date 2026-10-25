package cz.cztvhub.tv.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import cz.cztvhub.core.ContinueItem
import cz.cztvhub.core.CtException
import cz.cztvhub.core.ImageRequest
import cz.cztvhub.core.archive.ArchiveCard
import cz.cztvhub.core.archive.ArchiveCategory
import cz.cztvhub.core.archive.ArchiveGenre
import cz.cztvhub.core.archive.ArchiveMenu
import cz.cztvhub.core.archive.ArchiveMessages
import cz.cztvhub.core.archive.ArchiveParser
import cz.cztvhub.core.archive.ArchiveRules
import cz.cztvhub.core.archive.ArchiveShow
import cz.cztvhub.core.archive.ArchiveSort
import cz.cztvhub.core.archive.GridContent
import cz.cztvhub.core.archive.MenuItem
import cz.cztvhub.core.archive.MenuState
import cz.cztvhub.core.archive.MenuStep
import cz.cztvhub.core.dvb.DvbClient
import cz.cztvhub.tv.tvApp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** A paged grid (Naše nahrávky, a category or the search results); [error] = the last page failed. */
data class ArchiveGrid(
    val title: String = "",
    val cards: List<ArchiveCard> = emptyList(),
    val nextPage: Int? = 1,
    val loading: Boolean = false,
    val error: String? = null,
    /** iVysílání did not answer (`200` with an `error`): said above what is listed (our recordings). */
    val notice: String? = null,
    val attribution: String? = null,
)

/** An open show page; [season] the season chip shown, its episodes replace the show's first group. */
data class ShowState(
    val card: ArchiveCard,
    val show: Load<ArchiveShow> = Load.Loading,
    val season: String? = null,
    val seasonLoading: Boolean = false,
    val seasonNextPage: Int? = null,
)

/**
 * The Archiv tab (spec/apps.md "Archiv", T23 layout): a menu on the left ([menu], the core's [ArchiveMenu]) and a grid
 * of what its selected item filters on the right; everything from the gateway's `/dvb/archive…`, nothing without one.
 * Requests run one at a time per screen; a newer one replaces an older one of the same screen.
 */
class ArchiveViewModel(application: Application) : AndroidViewModel(application) {
    private val app = application.tvApp

    /** A gateway is saved in Nastavení → Server; without one the tab only says so. */
    private val _configured = MutableStateFlow(app.dvbSettings.config.value != null)
    val configured: StateFlow<Boolean> = _configured.asStateFlow()

    /** The menu; null until the categories answered (or failed) after the tab opened. */
    private val _menu = MutableStateFlow<MenuState?>(null)
    val menu: StateFlow<MenuState?> = _menu.asStateFlow()

    /** The grid of "Naše nahrávky" or a category / sub-genre, as [menu] says. */
    private val _grid = MutableStateFlow(ArchiveGrid())
    val grid: StateFlow<ArchiveGrid> = _grid.asStateFlow()

    /** "Pokračovat ve sledování", read from this TV's resume entries. */
    private val _continueRow = MutableStateFlow<List<ContinueItem>>(emptyList())
    val continueRow: StateFlow<List<ContinueItem>> = _continueRow.asStateFlow()

    private val _query = MutableStateFlow("")
    val query: StateFlow<String> = _query.asStateFlow()

    /** Search results for [query]; null while the query is shorter than 2 characters. */
    private val _search = MutableStateFlow<ArchiveGrid?>(null)
    val search: StateFlow<ArchiveGrid?> = _search.asStateFlow()

    private val _show = MutableStateFlow<ShowState?>(null)
    val show: StateFlow<ShowState?> = _show.asStateFlow()

    /** `/dvb/archive/categories` failed: the menu has only its own items, the tab says so above the grid. */
    private val _categoriesFailed = MutableStateFlow(false)
    val categoriesFailed: StateFlow<Boolean> = _categoriesFailed.asStateFlow()

    /** The gateway's category list, once it answered; asked again on the next opening after a failure. */
    private var categories: List<ArchiveCategory>? = null

    /** Sub-genres by category slug, as the first answer of that category listed them (ČT's order). */
    private val genresBySlug = HashMap<String, List<ArchiveGenre>>()

    private var categoriesJob: Job? = null
    private var gridJob: Job? = null
    private var searchJob: Job? = null
    private var showJob: Job? = null

    init {
        viewModelScope.launch {
            app.gateway.drop(1).collect {
                _configured.value = app.dvbSettings.config.value != null
                categories = null
                genresBySlug.clear()
                entered()
            }
        }
        viewModelScope.launch {
            app.dvbSettings.config.collect { _configured.value = it != null }
        }
    }

    private fun client(): DvbClient? = app.dvb()?.client

    /** The tab was entered: the menu opens afresh (on "Pokračovat ve sledování" when it has an entry). */
    fun entered() {
        refreshContinueRow()
        searchJob?.cancel()
        _query.value = ""
        _search.value = null
        _menu.value = null
        val known = categories
        if (known != null) {
            apply(ArchiveMenu.open(known, _continueRow.value.size))
            return
        }
        categoriesJob?.cancel()
        val client = client() ?: return
        categoriesJob = viewModelScope.launch {
            val list = withContext(Dispatchers.IO) {
                try {
                    client.archiveCategories()
                } catch (_: CtException) {
                    null
                }
            }
            categories = list
            _categoriesFailed.value = list == null
            // Without the list the menu still offers Hledat, Pokračovat and Naše nahrávky (which says if it fails).
            apply(ArchiveMenu.open(list.orEmpty(), _continueRow.value.size))
        }
    }

    /** Back from the player: the continue grid and the page's "Pokračovat od …" from what the player saved. */
    fun resumed() = refreshContinueRow()

    fun refreshContinueRow() {
        _continueRow.value = app.resume.continueRow()
    }

    /** "Odebrat z řady": deletes the resume entry. */
    fun removeFromRow(item: ContinueItem) {
        app.resume.remove(item.key)
        refreshContinueRow()
    }

    /** The focus rested on a menu item for [ArchiveMenu.SETTLE_MS] (or Right left it); true = the grid changed. */
    fun settle(item: MenuItem): Boolean {
        val menu = _menu.value ?: return false
        val next = ArchiveMenu.settle(menu, item)
        if (next.grid == menu.grid && next.selected == menu.selected) return false
        apply(next)
        return true
    }

    /** OK on a menu item; true = the focus goes into the search field. */
    fun ok(item: MenuItem): Boolean {
        val step = ArchiveMenu.ok(_menu.value ?: return false, item) as? MenuStep.Show ?: return false
        apply(step.state)
        return step.focusSearchField
    }

    /** Back in the menu; false = not handled here (the main menu: the tab is left as elsewhere). */
    fun back(): Boolean {
        val step = ArchiveMenu.back(_menu.value ?: return false) as? MenuStep.Show ?: return false
        apply(step.state)
        return true
    }

    fun setSort(sort: ArchiveSort) {
        _menu.value?.let { apply(ArchiveMenu.withSort(it, sort)) }
    }

    /** The failed grid's "Zkusit znovu": the category list too when it had failed. */
    fun retry() {
        if (categories.isNullOrEmpty()) {
            categories = null
            entered()
        } else {
            loadGrid(reset = true)
        }
    }

    /** The focus neared the grid's end: the next page, if any. */
    fun moreGrid() {
        val grid = _grid.value
        if (grid.nextPage == null || grid.loading) return
        loadGrid(reset = false)
    }

    private fun apply(next: MenuState) {
        val old = _menu.value
        val withGenres = next.category?.let { c -> genresBySlug[c.id]?.let { ArchiveMenu.withGenres(next, c.id, it) } } ?: next
        _menu.value = withGenres
        if (old == null || old.grid != withGenres.grid || old.sort != withGenres.sort) loadGrid(reset = true)
    }

    /** The slug, sort and sub-genre the grid asks the gateway for; null for "Pokračovat" and the search. */
    private fun gridQuery(menu: MenuState): Triple<String, ArchiveSort, String?>? = when (val g = menu.grid) {
        GridContent.Recordings -> Triple("recordings", menu.sort, null)
        is GridContent.Category -> Triple(g.slug, menu.sort, g.genre)
        GridContent.Continue, GridContent.Search -> null
    }

    private fun loadGrid(reset: Boolean) {
        val menu = _menu.value ?: return
        gridJob?.cancel()
        val query = gridQuery(menu) ?: return
        val client = client() ?: return
        val grid = if (reset) ArchiveGrid() else _grid.value
        val page = grid.nextPage ?: return
        _grid.value = grid.copy(loading = true, error = null)
        gridJob = viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                try {
                    Result.success(client.archiveCategory(query.first, query.second, query.third, page))
                } catch (e: CtException) {
                    Result.failure(e)
                }
            }
            val now = _menu.value ?: return@launch
            if (gridQuery(now) != query) return@launch
            result.getOrNull()?.let { answer ->
                if (query.first != "recordings" && answer.genres.isNotEmpty() && query.first !in genresBySlug) {
                    genresBySlug[query.first] = answer.genres
                    _menu.value = ArchiveMenu.withGenres(now, query.first, answer.genres)
                }
            }
            val current = _grid.value
            _grid.value = result.fold(
                onSuccess = {
                    current.copy(
                        title = it.title,
                        cards = (current.cards + it.cards).distinctBy(ArchiveCard::id),
                        nextPage = it.nextPage,
                        loading = false,
                        notice = it.error,
                        attribution = it.attribution ?: current.attribution,
                    )
                },
                onFailure = { current.copy(loading = false, error = (it as CtException).userMessage) },
            )
        }
    }

    /** Typing in the search field: results after [ArchiveRules.SEARCH_DEBOUNCE_MS] without typing, from 2 characters. */
    fun setQuery(text: String) {
        _query.value = text
        searchJob?.cancel()
        val query = ArchiveRules.searchQuery(text) ?: run {
            _search.value = null
            return
        }
        _search.value = (_search.value ?: ArchiveGrid()).copy(loading = true, error = null)
        searchJob = viewModelScope.launch {
            delay(ArchiveRules.SEARCH_DEBOUNCE_MS)
            _search.value = ArchiveGrid(loading = true)
            searchPage(query, 1)
        }
    }

    fun moreSearch() {
        val grid = _search.value ?: return
        val page = grid.nextPage ?: return
        val query = ArchiveRules.searchQuery(_query.value) ?: return
        if (grid.loading || searchJob?.isActive == true) return
        _search.value = grid.copy(loading = true, error = null)
        searchJob = viewModelScope.launch { searchPage(query, page) }
    }

    private suspend fun searchPage(query: String, page: Int) {
        val client = client() ?: return run { _search.value = ArchiveGrid(nextPage = null, error = ArchiveMessages.NO_GATEWAY) }
        val result = withContext(Dispatchers.IO) {
            try {
                Result.success(client.archiveSearch(query, page))
            } catch (e: CtException) {
                Result.failure(e)
            }
        }
        val grid = _search.value ?: return
        _search.value = result.fold(
            onSuccess = {
                grid.copy(
                    cards = (grid.cards + it.cards).distinctBy(ArchiveCard::id), nextPage = it.nextPage, loading = false,
                    notice = it.error, attribution = it.attribution ?: grid.attribution,
                )
            },
            onFailure = { grid.copy(loading = false, error = (it as CtException).userMessage) },
        )
    }

    fun openShow(card: ArchiveCard) {
        showJob?.cancel()
        _show.value = ShowState(card)
        val client = client() ?: return run { _show.value = ShowState(card, Load.Failed(ArchiveMessages.NO_GATEWAY)) }
        showJob = viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                try {
                    Load.Ready(client.archiveShow(card.id))
                } catch (e: CtException) {
                    Load.Failed(e.userMessage)
                }
            }
            _show.update { current ->
                val show = (result as? Load.Ready)?.value
                val group = show?.groups?.firstOrNull(ArchiveRules::isCtGroup)
                if (current?.card?.id != card.id) current
                else current.copy(
                    show = result,
                    season = group?.season ?: show?.seasons?.firstOrNull()?.id,
                    seasonNextPage = group?.nextPage,
                )
            }
        }
    }

    fun closeShow() {
        showJob?.cancel()
        _show.value = null
    }

    /** A season chip: that season's episodes, page 1; [more] = the next page of the season shown. */
    fun loadSeason(season: String, more: Boolean = false) {
        val state = _show.value ?: return
        if (state.show !is Load.Ready) return
        if (more && (state.seasonLoading || state.seasonNextPage == null)) return
        if (!more && state.season == season) return
        val page = if (more) state.seasonNextPage ?: return else 1
        val client = client() ?: return
        showJob?.cancel()
        _show.value = state.copy(season = season, seasonLoading = true)
        showJob = viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                try {
                    client.archiveShow(state.card.id, season, page)
                } catch (_: CtException) {
                    null
                }
            }
            _show.update { current ->
                if (current?.card?.id != state.card.id || current.season != season) return@update current
                val shown = (current.show as? Load.Ready)?.value
                if (result == null || shown == null) return@update current.copy(seasonLoading = false)
                val merged = ArchiveRules.withSeasonPage(shown, result, season, append = more)
                current.copy(
                    show = Load.Ready(merged),
                    seasonLoading = false,
                    seasonNextPage = merged.groups.firstOrNull { it.season == season }?.nextPage,
                )
            }
        }
    }

    /**
     * A card image: ČT's `https://` URL as is, a gateway path with the key; a recording thumbnail only while the
     * gateway and this TV allow thumbnails ([thumbnails]). Memory only, like the guide's.
     */
    fun image(path: String?, thumbnails: Boolean): ImageRequest? {
        if (path == null || !ArchiveParser.isImage(path)) return null
        if (path.startsWith("https://")) return ImageRequest(path)
        if (ArchiveParser.isThumbnail(path) && !(thumbnails && app.thumbnailPrefs.on.value)) return null
        val client = client() ?: return null
        return client.archiveImageUrl(path)?.let { ImageRequest(it, client.config.authHeaders, gateway = true) }
    }

}
