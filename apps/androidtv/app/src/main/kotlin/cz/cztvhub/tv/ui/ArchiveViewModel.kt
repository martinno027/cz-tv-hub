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
    private val grids = GridLoader(viewModelScope)
    val grid: StateFlow<ArchiveGrid> = grids.grid

    /** "Pokračovat ve sledování", read from this TV's resume entries. */
    private val _continueRow = MutableStateFlow<List<ContinueItem>>(emptyList())
    val continueRow: StateFlow<List<ContinueItem>> = _continueRow.asStateFlow()

    private val _query = MutableStateFlow("")
    val query: StateFlow<String> = _query.asStateFlow()

    /** Search results for [query]; null while the query is shorter than 2 characters. */
    private val _search = MutableStateFlow<ArchiveGrid?>(null)
    val search: StateFlow<ArchiveGrid?> = _search.asStateFlow()

    private val shows = ShowLoader(viewModelScope)
    val show: StateFlow<ShowState?> = shows.show

    /** `/dvb/archive/categories` failed: the menu has only its own items, the tab says so above the grid. */
    private val _categoriesFailed = MutableStateFlow(false)
    val categoriesFailed: StateFlow<Boolean> = _categoriesFailed.asStateFlow()

    /** The gateway's category list, once it answered; asked again on the next opening after a failure. */
    private var categories: List<ArchiveCategory>? = null

    /** Sub-genres by category slug, as the first answer of that category listed them (ČT's order). */
    private val genresBySlug = HashMap<String, List<ArchiveGenre>>()

    private var categoriesJob: Job? = null
    private var searchJob: Job? = null

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

    /** The gateway's archive for the grid and show loaders; null without a gateway. */
    private fun source(): ArchiveSource? = client()?.let { c ->
        object : ArchiveSource {
            override fun category(query: GridQuery, page: Int) = c.archiveCategory(query.slug, query.sort, query.genre, page)
            override fun show(id: String, season: String?, page: Int, year: Int?, month: Int?) =
                c.archiveShow(id, season, page, year, month)
        }
    }

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
        if (grids.grid.value.more() == null) return
        loadGrid(reset = false)
    }

    private fun apply(next: MenuState) {
        val old = _menu.value
        val withGenres = next.category?.let { c -> genresBySlug[c.id]?.let { ArchiveMenu.withGenres(next, c.id, it) } } ?: next
        _menu.value = withGenres
        if (old == null || old.grid != withGenres.grid || old.sort != withGenres.sort) loadGrid(reset = true)
    }

    /** The slug, sort and sub-genre the grid asks the gateway for; null for "Pokračovat" and the search. */
    private fun gridQuery(menu: MenuState): GridQuery? = when (val g = menu.grid) {
        GridContent.Recordings -> GridQuery("recordings", menu.sort, null)
        is GridContent.Category -> GridQuery(g.slug, menu.sort, g.genre)
        GridContent.Continue, GridContent.Search -> null
    }

    /**
     * New content ([reset]: page 1, the old cards stay dimmed under the loader until it answers, T52) or the next
     * page; the newest request wins ([GridLoader]).
     */
    private fun loadGrid(reset: Boolean) {
        val menu = _menu.value ?: return
        // New content cancels what the grid waited for (also when it is "Pokračovat" or the search now).
        if (reset) grids.cancel()
        val query = gridQuery(menu) ?: return
        val source = source() ?: return
        grids.load(source, query, reset, wanted = { _menu.value?.let(::gridQuery) }) { answer ->
            val slug = query.slug
            if (slug != "recordings" && answer.genres.isNotEmpty() && slug !in genresBySlug) {
                genresBySlug[slug] = answer.genres
                _menu.value?.let { _menu.value = ArchiveMenu.withGenres(it, slug, answer.genres) }
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

    fun openShow(card: ArchiveCard) = shows.open(card, source(), ArchiveMessages.NO_GATEWAY)

    fun closeShow() = shows.close()

    /** A season chip ([ShowLoader.season]). */
    fun loadSeason(season: String) = shows.season(source(), season)

    /** A year chip ([ShowLoader.year]). */
    fun loadYear(year: Int) = shows.year(source(), year)

    /** A month chip of the year shown ([ShowLoader.month]). */
    fun loadMonth(month: Int) = shows.month(source(), month)

    /** The next page of the iVysílání episodes shown ([ShowLoader.more]). */
    fun loadMore() = shows.more(source())

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
