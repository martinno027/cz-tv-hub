package cz.cztvhub.tv.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import cz.cztvhub.core.ContinueItem
import cz.cztvhub.core.CtException
import cz.cztvhub.core.ImageRequest
import cz.cztvhub.core.archive.ArchiveCard
import cz.cztvhub.core.archive.ArchiveGenre
import cz.cztvhub.core.archive.ArchiveHome
import cz.cztvhub.core.archive.ArchiveMessages
import cz.cztvhub.core.archive.ArchiveParser
import cz.cztvhub.core.archive.ArchiveRules
import cz.cztvhub.core.archive.ArchiveShow
import cz.cztvhub.core.archive.ArchiveSort
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

/** A paged grid (a category or the search results); [error] = the last page failed. */
data class ArchiveGrid(
    val title: String = "",
    val genres: List<ArchiveGenre> = emptyList(),
    val cards: List<ArchiveCard> = emptyList(),
    val nextPage: Int? = 1,
    val loading: Boolean = false,
    val error: String? = null,
    /** iVysílání did not answer (`200` with an `error`): said above what is listed (our recordings). */
    val notice: String? = null,
    val attribution: String? = null,
)

/** An open category: its slug, the chosen sort and sub-genre, and the grid. */
data class CategoryState(
    val slug: String,
    val title: String,
    val sort: ArchiveSort = ArchiveSort.POPULAR,
    val genre: String? = null,
    val grid: ArchiveGrid = ArchiveGrid(title = title),
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
 * The Archiv tab (spec/apps.md "Archiv (T13)"): everything from the gateway's `/dvb/archive…`, nothing without one.
 * Requests run one at a time per screen; a newer one replaces an older one of the same screen.
 */
class ArchiveViewModel(application: Application) : AndroidViewModel(application) {
    private val app = application.tvApp

    /** A gateway is saved in Nastavení → Server; without one the tab only says so. */
    private val _configured = MutableStateFlow(app.dvbSettings.config.value != null)
    val configured: StateFlow<Boolean> = _configured.asStateFlow()

    private val _home = MutableStateFlow<Load<ArchiveHome>>(Load.Loading)
    val home: StateFlow<Load<ArchiveHome>> = _home.asStateFlow()

    /** "Pokračovat ve sledování", read from this TV's resume entries. */
    private val _continueRow = MutableStateFlow<List<ContinueItem>>(emptyList())
    val continueRow: StateFlow<List<ContinueItem>> = _continueRow.asStateFlow()

    private val _query = MutableStateFlow("")
    val query: StateFlow<String> = _query.asStateFlow()

    /** Search results for [query]; null while the query is shorter than 2 characters (the home shows). */
    private val _search = MutableStateFlow<ArchiveGrid?>(null)
    val search: StateFlow<ArchiveGrid?> = _search.asStateFlow()

    private val _category = MutableStateFlow<CategoryState?>(null)
    val category: StateFlow<CategoryState?> = _category.asStateFlow()

    private val _show = MutableStateFlow<ShowState?>(null)
    val show: StateFlow<ShowState?> = _show.asStateFlow()

    private var homeJob: Job? = null
    private var searchJob: Job? = null
    private var categoryJob: Job? = null
    private var showJob: Job? = null

    init {
        viewModelScope.launch {
            app.gateway.drop(1).collect {
                _configured.value = app.dvbSettings.config.value != null
                loadHome()
            }
        }
        viewModelScope.launch {
            app.dvbSettings.config.collect { _configured.value = it != null }
        }
    }

    private fun client(): DvbClient? = app.dvb()?.client

    /** The tab is shown (also back from the player): the home once, the continue row every time. */
    fun opened() {
        refreshContinueRow()
        if (_home.value !is Load.Ready && homeJob?.isActive != true) loadHome()
    }

    fun refreshContinueRow() {
        _continueRow.value = app.resume.continueRow()
    }

    /** "Odebrat z řady": deletes the resume entry. */
    fun removeFromRow(item: ContinueItem) {
        app.resume.remove(item.key)
        refreshContinueRow()
    }

    fun loadHome() {
        homeJob?.cancel()
        val client = client() ?: run {
            _home.value = if (_configured.value) Load.Loading else Load.Failed(ArchiveMessages.NO_GATEWAY)
            return
        }
        if (_home.value !is Load.Ready) _home.value = Load.Loading
        homeJob = viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                try {
                    Load.Ready(client.archiveHome())
                } catch (e: CtException) {
                    Load.Failed(e.userMessage)
                }
            }
            // Keep the rows shown if a later refresh fails.
            if (result is Load.Ready || _home.value !is Load.Ready) _home.value = result
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

    fun openCategory(slug: String, title: String) {
        _category.value = CategoryState(slug, title)
        loadCategoryPage(reset = true)
    }

    fun closeCategory() {
        categoryJob?.cancel()
        _category.value = null
    }

    fun setSort(sort: ArchiveSort) {
        val state = _category.value ?: return
        if (state.sort == sort) return
        _category.value = state.copy(sort = sort)
        loadCategoryPage(reset = true)
    }

    /** A sub-genre chip; null = all. */
    fun setGenre(genre: String?) {
        val state = _category.value ?: return
        if (state.genre == genre) return
        _category.value = state.copy(genre = genre)
        loadCategoryPage(reset = true)
    }

    /** The focus neared the grid's end: the next page, if any. */
    fun moreCategory() {
        val state = _category.value ?: return
        if (state.grid.nextPage == null || state.grid.loading) return
        loadCategoryPage(reset = false)
    }

    private fun loadCategoryPage(reset: Boolean) {
        val state = _category.value ?: return
        val client = client() ?: return
        categoryJob?.cancel()
        val grid = if (reset) ArchiveGrid(title = state.title, genres = state.grid.genres) else state.grid
        val page = grid.nextPage ?: return
        _category.value = state.copy(grid = grid.copy(loading = true, error = null))
        categoryJob = viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                try {
                    Result.success(client.archiveCategory(state.slug, state.sort, state.genre, page))
                } catch (e: CtException) {
                    Result.failure(e)
                }
            }
            val now = _category.value ?: return@launch
            if (now.slug != state.slug || now.sort != state.sort || now.genre != state.genre) return@launch
            _category.value = now.copy(grid = result.fold(
                onSuccess = {
                    now.grid.copy(
                        title = it.title.ifBlank { now.title },
                        // The chips stay as the unfiltered category listed them.
                        genres = now.grid.genres.ifEmpty { it.genres },
                        cards = (now.grid.cards + it.cards).distinctBy(ArchiveCard::id),
                        nextPage = it.nextPage,
                        loading = false,
                        notice = it.error,
                        attribution = it.attribution ?: now.grid.attribution,
                    )
                },
                onFailure = { now.grid.copy(loading = false, error = (it as CtException).userMessage) },
            ))
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
