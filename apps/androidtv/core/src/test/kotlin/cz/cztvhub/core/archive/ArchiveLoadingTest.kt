package cz.cztvhub.core.archive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** spec/apps.md "Loading is always visible" (T52): the loader rule and the grid's wait. */
class ArchiveLoadingTest {
    private fun card(id: String) = ArchiveCard("ct:$id", CardSource.CT, "Pořad $id", CardKind.SERIES)
    private fun page(vararg ids: String, next: Int? = null, error: String? = null, attribution: String? = null) =
        ArchivePage("Filmy", emptyList(), ids.map(::card), next, error, attribution)

    @Test fun theLoaderShowsOnlyAfter150Ms() {
        assertEquals(150L, ArchiveLoading.DELAY_MS)
        for (wait in Wait.entries) assertEquals("$wait at once", LoaderPlace.NONE, ArchiveLoading.place(wait, 0))
        assertEquals("a fast answer shows none", LoaderPlace.NONE, ArchiveLoading.place(Wait.REPLACE, 149))
        assertEquals(LoaderPlace.OVER, ArchiveLoading.place(Wait.REPLACE, 150))
        assertEquals("the next page: small, below", LoaderPlace.BELOW, ArchiveLoading.place(Wait.MORE, 150))
        assertEquals(LoaderPlace.NONE, ArchiveLoading.place(Wait.NONE, 10_000))
        assertNotEquals(ArchiveLoading.place(Wait.REPLACE, 150), ArchiveLoading.place(Wait.MORE, 150))
    }

    @Test fun onlyTheLoaderOverThePartDimsIt() {
        assertTrue(ArchiveLoading.dimmed(LoaderPlace.OVER))
        assertFalse("a next page keeps what is listed bright", ArchiveLoading.dimmed(LoaderPlace.BELOW))
        assertFalse("no flash before the delay", ArchiveLoading.dimmed(LoaderPlace.NONE))
        assertEquals(0.35f, ArchiveLoading.DIM_ALPHA)
        assertEquals("the spec's 64 px of 1080p in Android TV's ~960×540 dp", 32, ArchiveLoading.SIZE_DP)
        assertEquals("half size below the last row", 16, ArchiveLoading.SMALL_SIZE_DP)
    }

    @Test fun newContentKeepsTheOldCardsUntilItArrives() {
        val shown = ArchiveGrid().replacing().let { it.answered(it.request, page("1", "2", next = 2)) }
        assertEquals(listOf("ct:1", "ct:2"), shown.cards.map { it.id })
        assertTrue(shown.loaded)
        val waiting = shown.replacing()
        assertEquals(Wait.REPLACE, waiting.wait)
        assertEquals("the old cards stay (dimmed by the UI)", shown.cards, waiting.cards)
        assertEquals("new content asks page 1", 1, waiting.pageAsked)
        val replaced = waiting.answered(waiting.request, page("3", next = null, attribution = "TMDb"))
        assertEquals("then they are replaced", listOf("ct:3"), replaced.cards.map { it.id })
        assertEquals(Wait.NONE, replaced.wait)
        assertNull(replaced.nextPage)
        assertEquals("TMDb", replaced.attribution)
        assertEquals("a replacement starts the grid from its top", shown.listing + 1, replaced.listing)
    }

    @Test fun theNewestChoiceWins() {
        val shown = ArchiveGrid().replacing().let { it.answered(it.request, page("1", next = 2)) }
        val first = shown.replacing()
        val second = first.replacing()
        assertEquals("a newer choice keeps waiting", Wait.REPLACE, second.wait)
        assertNotEquals(first.request, second.request)
        val stale = second.answered(first.request, page("old"))
        assertEquals("the waited answer is dropped", second, stale)
        assertEquals(second, second.failed(first.request, "Archiv se nepodařilo načíst"))
        assertEquals(listOf("ct:new"), second.answered(second.request, page("new")).cards.map { it.id })
        // A next page asked before a new choice is dropped too.
        val paging = shown.more()!!
        val chosen = paging.replacing()
        assertNull("a waiting grid asks no next page", chosen.more())
        assertEquals(chosen, chosen.answered(paging.request, page("2")))
    }

    @Test fun theNextPageAppendsBelow() {
        val shown = ArchiveGrid().replacing().let { it.answered(it.request, page("1", "2", next = 2, attribution = "TMDb")) }
        val more = shown.more()!!
        assertEquals(Wait.MORE, more.wait)
        assertEquals(2, more.pageAsked)
        assertNull("one page at a time", more.more())
        val appended = more.answered(more.request, page("2", "3", next = null, error = "iVysílání teď neodpovídá"))
        assertEquals("appended, duplicates dropped", listOf("ct:1", "ct:2", "ct:3"), appended.cards.map { it.id })
        assertEquals("a page keeps the grid's position", shown.listing, appended.listing)
        assertEquals("TMDb", appended.attribution)
        assertEquals("iVysílání teď neodpovídá", appended.notice)
        assertNull("no page after the last", appended.more())
    }

    @Test fun failuresSayItWithoutEmptyText() {
        assertFalse("before the first answer an empty grid is not 'Nic nenalezeno'", ArchiveGrid().replacing().loaded)
        val shown = ArchiveGrid().replacing().let { it.answered(it.request, page("1", next = 2)) }
        val waiting = shown.replacing()
        val failed = waiting.failed(waiting.request, "Archiv se nepodařilo načíst")
        assertTrue("new content that failed lists only the message", failed.cards.isEmpty())
        assertEquals("Archiv se nepodařilo načíst", failed.error)
        assertEquals(Wait.NONE, failed.wait)
        assertEquals("page 1 can be asked again", 1, failed.nextPage)
        val retrying = failed.replacing()
        assertEquals("a retry keeps the message (dimmed) until it answers", failed.error, retrying.error)
        val more = shown.more()!!
        val pageFailed = more.failed(more.request, "Archiv se nepodařilo načíst")
        assertEquals("a failed next page keeps what is listed", shown.cards, pageFailed.cards)
        assertEquals(2, pageFailed.nextPage)
        assertEquals(Wait.NONE, pageFailed.wait)
    }

    @Test fun aFailedChipReturnsToWhatIsListed() {
        val listed = EpisodeChoice("s1", 2026, 11)
        val waited = EpisodeChoice("s1", 2025, null)
        assertEquals("nothing waits: the chips are what is listed", listed, ArchiveLoading.listedChoice(false, null, listed))
        assertEquals("nothing waits, a stale value is ignored", listed, ArchiveLoading.listedChoice(false, waited, listed))
        // A newer chip while 2025 waits: the chips show 2025, the list still 2026/11.
        assertEquals(listed, ArchiveLoading.listedChoice(true, listed, waited))
        assertEquals("no record = the chips", waited, ArchiveLoading.listedChoice(true, null, waited))
    }
}
