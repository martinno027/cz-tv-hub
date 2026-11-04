package cz.cztvhub.tv.ui

import cz.cztvhub.core.CtException
import cz.cztvhub.core.archive.ArchiveCard
import cz.cztvhub.core.archive.ArchiveGrid
import cz.cztvhub.core.archive.ArchiveLoading
import cz.cztvhub.core.archive.ArchivePage
import cz.cztvhub.core.archive.ArchiveRules
import cz.cztvhub.core.archive.ArchiveShow
import cz.cztvhub.core.archive.ArchiveSort
import cz.cztvhub.core.archive.Wait
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlin.coroutines.CoroutineContext

/** The slug, sort and sub-genre the Archiv grid asks the gateway for. */
data class GridQuery(val slug: String, val sort: ArchiveSort, val genre: String?)

/** What the archive loaders ask the gateway (blocking calls that throw [CtException]; `DvbClient` in the app). */
interface ArchiveSource {
    fun category(query: GridQuery, page: Int): ArchivePage
    fun show(id: String, season: String? = null, page: Int = 1, year: Int? = null, month: Int? = null): ArchiveShow
}

/**
 * The Archiv grid's requests (T52; T57 checked the sequences): one at a time, new content cancels what the grid
 * waited for and the newest request wins (its number in [ArchiveGrid.request]). A cancelled request changes nothing:
 * it is never a failure, and the newer request it gave way to ends the wait.
 */
class GridLoader(private val scope: CoroutineScope, private val io: CoroutineContext = Dispatchers.IO) {
    private val _grid = MutableStateFlow(ArchiveGrid())
    val grid: StateFlow<ArchiveGrid> = _grid.asStateFlow()

    /** The running request (visible to tests, which wait for a cancelled one to end). */
    internal var job: Job? = null
        private set

    /** New content that needs no request ("Pokračovat", the search): what the grid waited for is dropped. */
    fun cancel() {
        job?.cancel()
    }

    /**
     * New content ([reset]: page 1, the old cards stay dimmed under the loader until it answers) or the next page of
     * [query]. The answer is taken only while [wanted] still asks [query]; [onPage] sees a page before the grid does.
     */
    fun load(
        source: ArchiveSource,
        query: GridQuery,
        reset: Boolean,
        wanted: () -> GridQuery?,
        onPage: (ArchivePage) -> Unit = {},
    ) {
        if (reset) job?.cancel()
        val grid = (if (reset) _grid.value.replacing() else _grid.value.more()) ?: return
        val page = grid.pageAsked
        val request = grid.request
        _grid.value = grid
        job = scope.launch {
            val result = withContext(io) {
                try {
                    Result.success(source.category(query, page))
                } catch (e: CtException) {
                    Result.failure(e)
                }
            }
            if (wanted() != query) return@launch
            result.getOrNull()?.let(onPage)
            _grid.update { current ->
                result.fold(
                    onSuccess = { current.answered(request, it) },
                    onFailure = { current.failed(request, (it as CtException).userMessage) },
                )
            }
        }
    }
}

/**
 * An open show page and its requests (T52; T57 checked the sequences): opening it, a season / year / month chip and
 * the next page of episodes, one at a time; a newer one cancels the older (never a failure).
 */
class ShowLoader(private val scope: CoroutineScope, private val io: CoroutineContext = Dispatchers.IO) {
    private val _show = MutableStateFlow<ShowState?>(null)
    val show: StateFlow<ShowState?> = _show.asStateFlow()

    /** The running request (visible to tests, which wait for a cancelled one to end). */
    internal var job: Job? = null
        private set

    fun open(card: ArchiveCard, source: ArchiveSource?, noSource: String) {
        job?.cancel()
        _show.value = ShowState(card)
        if (source == null) {
            _show.value = ShowState(card, Load.Failed(noSource))
            return
        }
        job = scope.launch {
            val result = withContext(io) {
                try {
                    Load.Ready(source.show(card.id))
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

    fun close() {
        job?.cancel()
        _show.value = null
    }

    /** A season chip: that season's episodes, page 1, in the season's default period (its newest month, T32). */
    fun season(source: ArchiveSource?, season: String) {
        val state = _show.value ?: return
        if (state.season == season) return
        episodes(source, state, season, year = null, month = null, more = false)
    }

    /** A year chip (T32): `?year=` alone, the gateway picks its newest month or lists it whole (T51), page 1. */
    fun year(source: ArchiveSource?, year: Int) {
        val state = _show.value ?: return
        if (state.year == year) return
        episodes(source, state, state.season, year, month = null, more = false)
    }

    /** A month chip of the year shown (T32), page 1. */
    fun month(source: ArchiveSource?, month: Int) {
        val state = _show.value ?: return
        val year = state.year ?: return
        if (state.month == month) return
        episodes(source, state, state.season, year, month, more = false)
    }

    /** The next page of the iVysílání episodes shown (season and period as they are). */
    fun more(source: ArchiveSource?) {
        val state = _show.value ?: return
        if (state.seasonLoading || state.seasonNextPage == null) return
        episodes(source, state, state.season, state.year, state.month, more = true)
    }

    private fun episodes(source: ArchiveSource?, state: ShowState, season: String?, year: Int?, month: Int?, more: Boolean) {
        if (state.show !is Load.Ready) return
        val page = if (more) state.seasonNextPage ?: return else 1
        source ?: return
        job?.cancel()
        // T52: the chips show the new choice at once; a failure returns to the choice whose episodes are listed.
        val listed = ArchiveLoading.listedChoice(state.seasonLoading, state.listed, state.choice)
        _show.value = state.copy(
            season = season, year = year, month = month,
            episodesWait = if (more) Wait.MORE else Wait.REPLACE, listed = listed,
        )
        job = scope.launch {
            val result = withContext(io) {
                try {
                    source.show(state.card.id, season, page, year, month)
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
}
