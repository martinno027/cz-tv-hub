package cz.cztvhub.tv.ui

import cz.cztvhub.core.CtException
import cz.cztvhub.core.archive.ArchiveCard
import cz.cztvhub.core.archive.ArchiveEpisode
import cz.cztvhub.core.archive.ArchiveGrid
import cz.cztvhub.core.archive.ArchiveMessages
import cz.cztvhub.core.archive.ArchivePage
import cz.cztvhub.core.archive.ArchivePeriods
import cz.cztvhub.core.archive.ArchiveSeason
import cz.cztvhub.core.archive.ArchiveShow
import cz.cztvhub.core.archive.ArchiveSort
import cz.cztvhub.core.archive.CardKind
import cz.cztvhub.core.archive.CardSource
import cz.cztvhub.core.archive.EpisodeGroup
import cz.cztvhub.core.archive.EpisodeSource
import cz.cztvhub.core.archive.Wait
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Collections
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ExecutionException
import java.util.concurrent.TimeUnit

/**
 * T57 (a T52 regression found on tvOS): a choice changed while the gateway answers slowly, then back to what is
 * listed. The grid / episode list must not wait forever, the next page must still load, and a cancelled request is
 * never "Archiv se nepodařilo načíst". The fake gateway answers only when the test says so.
 */
class ArchiveLoadersTest {
    /** One request the fake got: what was asked and the answer it waits for. */
    private class Call(val asked: String) {
        val answer = CompletableFuture<Any>()
        fun answer(value: Any) = answer.complete(value)
        fun fail() = answer.completeExceptionally(CtException("cancelled or failed", ArchiveMessages.GENERIC))
    }

    /** A gateway that answers each request only when [Call.answer] is called (a slow answer). */
    private class SlowGateway : ArchiveSource {
        val calls: MutableList<Call> = Collections.synchronizedList(mutableListOf())

        private fun ask(asked: String): Any {
            val call = Call(asked)
            calls += call
            return try {
                call.answer.get(5, TimeUnit.SECONDS)
            } catch (e: ExecutionException) {
                throw e.cause!!
            }
        }

        override fun category(query: GridQuery, page: Int) = ask("${query.slug}#$page") as ArchivePage
        override fun show(id: String, season: String?, page: Int, year: Int?, month: Int?) =
            ask("$id s=$season y=$year m=$month #$page") as ArchiveShow

        /** Waits until the fake got its [n]th request. */
        suspend fun call(n: Int): Call {
            val until = System.currentTimeMillis() + 5_000
            while (calls.size < n) {
                check(System.currentTimeMillis() < until) { "request $n never came: ${calls.map { it.asked }}" }
                delay(2)
            }
            return calls[n - 1]
        }
    }

    private fun card(id: String) = ArchiveCard("ct:$id", CardSource.CT, "Pořad $id", CardKind.SERIES)
    private fun page(vararg ids: String, next: Int?) = ArchivePage("Archiv", emptyList(), ids.map(::card), next)

    private val filmy = GridQuery("3947-filmy", ArchiveSort.POPULAR, null)
    private val serialy = GridQuery("3949-serialy", ArchiveSort.POPULAR, null)

    private suspend fun waitFor(loader: GridLoader, until: (ArchiveGrid) -> Boolean) =
        withTimeout(5_000) { loader.grid.first(until) }

    /** Filmy listed with a next page, then Seriály asked and its answer slow. */
    private suspend fun CoroutineScope.filmyThenSlowSerialy(gateway: SlowGateway): Pair<GridLoader, () -> GridQuery?> {
        val loader = GridLoader(this, Dispatchers.IO)
        var menu: GridQuery? = filmy
        val wanted = { menu }
        loader.load(gateway, filmy, reset = true, wanted = wanted)
        gateway.call(1).answer(page("f1", "f2", next = 2))
        waitFor(loader) { it.loaded && it.wait == Wait.NONE }
        menu = serialy
        loader.load(gateway, serialy, reset = true, wanted = wanted)
        assertEquals("3949-serialy#1", gateway.call(2).asked)
        assertEquals(Wait.REPLACE, loader.grid.value.wait)
        menu = filmy
        return loader to { menu }
    }

    @Test fun backToTheListedMenuItemEndsTheWaitAndKeepsPaging() = runBlocking {
        val gateway = SlowGateway()
        val (loader, wanted) = filmyThenSlowSerialy(gateway)
        val waited = loader.job!!
        loader.load(gateway, filmy, reset = true, wanted = wanted)
        assertTrue("going back cancels the waited Seriály", waited.isCancelled)
        assertEquals("3947-filmy#1", gateway.call(3).asked)
        gateway.call(3).answer(page("f1", "f2", next = 2))
        val back = waitFor(loader) { it.wait == Wait.NONE }
        assertEquals(listOf("ct:f1", "ct:f2"), back.cards.map { it.id })
        assertEquals("the next page is still known", 2, back.nextPage)
        // Seriály answers late: nothing changes.
        gateway.call(2).answer(page("s1", next = null))
        waited.join()
        assertEquals(back, loader.grid.value)
        // The next pages load.
        loader.load(gateway, filmy, reset = false, wanted = wanted)
        assertEquals("3947-filmy#2", gateway.call(4).asked)
        gateway.call(4).answer(page("f3", next = 3))
        val paged = waitFor(loader) { it.wait == Wait.NONE && it.cards.size == 3 }
        assertEquals(3, paged.nextPage)
    }

    @Test fun aCancelledGridRequestIsNeverAFailure() = runBlocking {
        val gateway = SlowGateway()
        val (loader, wanted) = filmyThenSlowSerialy(gateway)
        val waited = loader.job!!
        loader.load(gateway, filmy, reset = true, wanted = wanted)
        // The cancelled request ends first, and with an error (as a cancelled connection would).
        gateway.call(2).fail()
        waited.join()
        val still = loader.grid.value
        assertNull("a cancelled request says nothing", still.error)
        assertEquals("the newest request still waits", Wait.REPLACE, still.wait)
        assertEquals(listOf("ct:f1", "ct:f2"), still.cards.map { it.id })
        gateway.call(3).answer(page("f1", "f2", next = 2))
        assertEquals(2, waitFor(loader) { it.wait == Wait.NONE }.nextPage)
        // The same failure on the request waited for is said (the two cases differ).
        loader.load(gateway, serialy, reset = true, wanted = { serialy })
        gateway.call(4).fail()
        assertEquals(ArchiveMessages.GENERIC, waitFor(loader) { it.wait == Wait.NONE }.error)
    }

    // -- the show page --------------------------------------------------------------------------------------------

    private fun episode(id: String) =
        ArchiveEpisode(EpisodeSource.CT, id, null, "Díl $id", "", 1500.0, null, "ČT1", playable = true)

    private val showCard = card("show")

    private fun showPage(season: String?, vararg ids: String, next: Int?, periods: ArchivePeriods? = null) = ArchiveShow(
        // A show with periods has no season chips here (the year chips are tested on their own).
        showCard, "", "", emptyList(), if (periods != null) emptyList() else listOf(ArchiveSeason("a", "1. řada"), ArchiveSeason("b", "2. řada")),
        listOf(EpisodeGroup("Celé díly", ids.map(::episode), season, next, periods)),
    )

    private suspend fun ShowLoader.waitFor(until: (ShowState) -> Boolean) =
        withTimeout(5_000) { show.first { it != null && until(it) } }!!

    private fun episodes(state: ShowState) =
        (state.show as Load.Ready).value.groups.first().episodes.map { it.idec }

    @Test fun backToTheListedSeasonEndsTheWaitAndKeepsPaging() = runBlocking {
        val gateway = SlowGateway()
        val loader = ShowLoader(this, Dispatchers.IO)
        loader.open(showCard, gateway, ArchiveMessages.NO_GATEWAY)
        gateway.call(1).answer(showPage("a", "a1", "a2", next = 2))
        loader.waitFor { it.show is Load.Ready }
        loader.season(gateway, "b")
        assertEquals("ct:show s=b y=null m=null #1", gateway.call(2).asked)
        val waited = loader.job!!
        loader.season(gateway, "a")
        assertTrue(waited.isCancelled)
        gateway.call(3).answer(showPage("a", "a1", "a2", next = 2))
        val back = loader.waitFor { it.episodesWait == Wait.NONE }
        assertEquals("a", back.season)
        assertEquals(listOf("a1", "a2"), episodes(back))
        assertEquals("the next page is still known", 2, back.seasonNextPage)
        // The cancelled B fails late: not a failure, nothing changes.
        gateway.call(2).fail()
        waited.join()
        assertEquals(back, loader.show.value)
        loader.more(gateway)
        assertEquals("ct:show s=a y=null m=null #2", gateway.call(4).asked)
        gateway.call(4).answer(showPage("a", "a3", next = null))
        val paged = loader.waitFor { it.episodesWait == Wait.NONE && episodes(it).size == 3 }
        assertNull(paged.seasonNextPage)
    }

    @Test fun backToTheListedYearEndsTheWaitAndKeepsPaging() = runBlocking {
        val gateway = SlowGateway()
        val loader = ShowLoader(this, Dispatchers.IO)
        val y2026 = ArchivePeriods(listOf(2026, 2025), 2026, listOf(11, 10), 11)
        val y2025 = ArchivePeriods(listOf(2026, 2025), 2025, listOf(12), 12)
        loader.open(showCard, gateway, ArchiveMessages.NO_GATEWAY)
        gateway.call(1).answer(showPage(null, "n1", next = 2, periods = y2026))
        loader.waitFor { it.show is Load.Ready }
        loader.year(gateway, 2025)
        assertEquals("ct:show s=null y=2025 m=null #1", gateway.call(2).asked)
        val waited = loader.job!!
        assertEquals("the chip shows the waited year", 2025, loader.show.value!!.year)
        loader.year(gateway, 2026)
        assertTrue(waited.isCancelled)
        assertEquals("ct:show s=null y=2026 m=null #1", gateway.call(3).asked)
        // The cancelled 2025 answers first: still waiting for 2026, no failure.
        gateway.call(2).answer(showPage(null, "o1", next = null, periods = y2025))
        waited.join()
        assertEquals(Wait.REPLACE, loader.show.value!!.episodesWait)
        gateway.call(3).answer(showPage(null, "n1", next = 2, periods = y2026))
        val back = loader.waitFor { it.episodesWait == Wait.NONE }
        assertEquals(2026, back.year)
        assertEquals(11, back.month)
        assertEquals(listOf("n1"), episodes(back))
        assertEquals(2, back.seasonNextPage)
        loader.more(gateway)
        assertEquals("ct:show s=null y=2026 m=11 #2", gateway.call(4).asked)
        gateway.call(4).answer(showPage(null, "n2", next = null, periods = y2026))
        assertEquals(listOf("n1", "n2"), episodes(loader.waitFor { it.episodesWait == Wait.NONE && episodes(it).size == 2 }))
    }

    @Test fun aFailedChipStillReturnsToWhatIsListed() = runBlocking {
        // The guard above must not hide a real failure: the waited request failing returns the chips to "a".
        val gateway = SlowGateway()
        val loader = ShowLoader(this, Dispatchers.IO)
        loader.open(showCard, gateway, ArchiveMessages.NO_GATEWAY)
        gateway.call(1).answer(showPage("a", "a1", next = 2))
        loader.waitFor { it.show is Load.Ready }
        loader.season(gateway, "b")
        gateway.call(2).fail()
        val failed = loader.waitFor { it.episodesWait == Wait.NONE }
        assertEquals("a", failed.season)
        assertEquals(listOf("a1"), episodes(failed))
        assertNotEquals("b", failed.season)
    }
}
