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
import cz.cztvhub.core.archive.ArchiveGrid
import cz.cztvhub.core.archive.ArchiveLoading
import cz.cztvhub.core.archive.ArchiveMenu
import cz.cztvhub.core.archive.ArchiveMessages
import cz.cztvhub.core.archive.ArchivePeriods
import cz.cztvhub.core.archive.ArchiveParser
import cz.cztvhub.core.archive.ArchiveRules
import cz.cztvhub.core.archive.ArchiveShow
import cz.cztvhub.core.archive.ArchiveSort
import cz.cztvhub.core.archive.EpisodeChoice
import cz.cztvhub.core.archive.GridContent
import cz.cztvhub.core.archive.MenuItem
import cz.cztvhub.core.archive.MenuState
import cz.cztvhub.core.archive.MenuStep
import cz.cztvhub.core.archive.Wait
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

/**
 * An open show page; [season] the season chip shown, its episodes replace the show's first group. [year]/[month] the
 * period chips chosen (T32; [month] null while a year chosen alone loads, and for a year shown whole, T51), kept while
 * the page is open. [episodesWait] how the episode list waits (T52: a chip = new episodes over the dimmed list, the
 * next page below it); [listed] the choice whose episodes are listed while it waits (a failure returns there).
 */
data class ShowState(
    val card: ArchiveCard,
    val show: Load<ArchiveShow> = Load.Loading,
    val season: String? = null,
    val episodesWait: Wait = Wait.NONE,
    val seasonNextPage: Int? = null,
    val year: Int? = null,
    val month: Int? = null,
    val listed: EpisodeChoice? = null,
) {
    val seasonLoading: Boolean get() = episodesWait != Wait.NONE
    val choice: EpisodeChoice get() = EpisodeChoice(season, year, month)

    /** The periods of the iVysílání group shown, with the selection made meanwhile; null = no chips. */
    val periods: ArchivePeriods?
        get() {
            val shown = (show as? Load.Ready)?.value?.groups?.firstOrNull(ArchiveRules::isCtGroup)?.periods ?: return null
            if (year == null || year == shown.year) return shown
            // Another year is loading: its months are not known yet.
            return shown.copy(year = year, months = emptyList())
        }
}

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
        if (_grid.value.more() == null) return
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

    /**
     * New content ([reset]: page 1, the old cards stay dimmed under the loader until it answers, T52) or the next
     * page; the newest request wins (its number in [ArchiveGrid.request]).
     */
    private fun loadGrid(reset: Boolean) {
        val menu = _menu.value ?: return
        // New content cancels what the grid waited for (also when it is "Pokračovat" or the search now).
        if (reset) gridJob?.cancel()
        val query = gridQuery(menu) ?: return
        val client = client() ?: return
        val grid = (if (reset) _grid.value.replacing() else _grid.value.more()) ?: return
        val page = grid.pageAsked
        val request = grid.request
        _grid.value = grid
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
            _grid.update { current ->
                result.fold(
                    onSuccess = { current.answered(request, it) },
                    onFailure = { current.failed(request, (it as CtException).userMessage) },
                )
            }
        }
    }

    /**
     * Typing in the search field: results after [ArchiveRules.SEARCH_DEBOUNCE_MS] without typing, from 2 characters.
     * The wait (and its loader, T52) starts with the request; earlier results stay listed until the new ones arrive.
     */
    fun setQuery(text: String) {
        _query.value = text
        searchJob?.cancel()
        val query = ArchiveRules.searchQuery(text) ?: run {
            _search.value = null
            return
        }
        searchJob = viewModelScope.launch {
            delay(ArchiveRules.SEARCH_DEBOUNCE_MS)
            val grid = (_search.value ?: ArchiveGrid()).replacing()
            _search.value = grid
            searchPage(query, grid)
        }
    }

    fun moreSearch() {
        val query = ArchiveRules.searchQuery(_query.value) ?: return
        if (searchJob?.isActive == true) return
        val grid = _search.value?.more() ?: return
        _search.value = grid
        searchJob = viewModelScope.launch { searchPage(query, grid) }
    }

    /** Asks [grid]'s page; the answer is taken only while it is still the newest request. */
    private suspend fun searchPage(query: String, grid: ArchiveGrid) {
        val client = client() ?: return run { _search.update { it?.failed(grid.request, ArchiveMessages.NO_GATEWAY)?.copy(nextPage = null) } }
        val result = withContext(Dispatchers.IO) {
            try {
                Result.success(client.archiveSearch(query, grid.pageAsked))
            } catch (e: CtException) {
                Result.failure(e)
            }
        }
        _search.update { current ->
            current?.let {
                result.fold(
                    onSuccess = { page -> it.answered(grid.request, page) },
                    onFailure = { e -> it.failed(grid.request, (e as CtException).userMessage) },
                )
            }
        }
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
                    year = group?.periods?.year,
                    month = group?.periods?.month,
                )
            }
        }
    }

    fun closeShow() {
        showJob?.cancel()
        _show.value = null
    }

    /** A season chip: that season's episodes, page 1, in the season's default period (its newest month, T32). */
    fun loadSeason(season: String) {
        val state = _show.value ?: return
        if (state.season == season) return
        loadEpisodes(state, season, year = null, month = null, more = false)
    }

    /** A year chip (T32): `?year=` alone, the gateway picks its newest month or lists it whole (T51), page 1. */
    fun loadYear(year: Int) {
        val state = _show.value ?: return
        if (state.year == year) return
        loadEpisodes(state, state.season, year, month = null, more = false)
    }

    /** A month chip of the year shown (T32), page 1. */
    fun loadMonth(month: Int) {
        val state = _show.value ?: return
        val year = state.year ?: return
        if (state.month == month) return
        loadEpisodes(state, state.season, year, month, more = false)
    }

    /** The next page of the iVysílání episodes shown (season and period as they are). */
    fun loadMore() {
        val state = _show.value ?: return
        if (state.seasonLoading || state.seasonNextPage == null) return
        loadEpisodes(state, state.season, state.year, state.month, more = true)
    }

    private fun loadEpisodes(state: ShowState, season: String?, year: Int?, month: Int?, more: Boolean) {
        if (state.show !is Load.Ready) return
        val page = if (more) state.seasonNextPage ?: return else 1
        val client = client() ?: return
        showJob?.cancel()
        // T52: the chips show the new choice at once; a failure returns to the choice whose episodes are listed.
        val listed = ArchiveLoading.listedChoice(state.seasonLoading, state.listed, state.choice)
        _show.value = state.copy(
            season = season, year = year, month = month,
            episodesWait = if (more) Wait.MORE else Wait.REPLACE, listed = listed,
        )
        showJob = viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                try {
                    client.archiveShow(state.card.id, season, page, year, month)
                } catch (_: CtException) {
                    null
                }
            }
            _show.update { current ->
                val same = current?.card?.id == state.card.id && current.season == season &&
                    current.year == year && current.month == month
                if (!same) return@update current
                val shown = (current.show as? Load.Ready)?.value
                // A failed load keeps what is listed and its chips (the chip can be chosen again).
                if (result == null || shown == null) {
                    return@update current.copy(
                        episodesWait = Wait.NONE, listed = null, season = listed.season, year = listed.year, month = listed.month,
                    )
                }
                val merged = ArchiveRules.withSeasonPage(shown, result, season, append = more)
                val group = merged.groups.firstOrNull(ArchiveRules::isCtGroup)
                current.copy(
                    show = Load.Ready(merged),
                    episodesWait = Wait.NONE,
                    listed = null,
                    seasonNextPage = group?.nextPage,
                    year = group?.periods?.year,
                    month = group?.periods?.month,
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
