package cz.cztvhub.tv.ui

import cz.cztvhub.core.Channels
import cz.cztvhub.core.PageButton
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.Programme
import cz.cztvhub.core.dvb.DvbChannel
import cz.cztvhub.core.dvb.DvbEvent
import cz.cztvhub.core.dvb.DvbRecording
import cz.cztvhub.core.dvb.RecordingState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/** The programme page's buttons and what they play (spec/apps.md "Programme page (T15)"). */
class EntriesTest {
    private val now = Instant.parse("2026-10-17T18:00:00Z")
    private val ct1 = Channels.byId("CH_1")!!

    private fun ct(start: String, end: String, playable: Boolean = true) = CtEntry(
        ct1,
        Programme("226411010101016", "Pořad", Instant.parse(start), Instant.parse(end), isPlayableNow = playable),
    )

    private fun dvb(start: String, end: String, state: RecordingState?) = DvbEntry(
        DvbChannel("c1", 1, "ČT1 HD", null),
        DvbEvent("e1", "c1", "Film", Instant.parse(start), Instant.parse(end), recording = state?.let { DvbRecording("r1", it) }),
    )

    private val offers: ResumeOffers = { key -> if (key == "ct:226411010101016" || key == "dvb:r1") 1390.0 else null }

    private fun ProgrammePageModel.buttons() = actions.map { it.button }
    private fun ProgrammePageModel.request(button: PageButton) = actions.single { it.button == button }.request

    @Test fun airingCtWithOfferResumesFirst() {
        val page = ct("2026-10-17T17:30:00Z", "2026-10-17T19:00:00Z").page(now, offers)!!
        assertEquals(listOf(PageButton.LIVE, PageButton.RESUME, PageButton.START_OVER, PageButton.OPEN_OFFICIAL_APP), page.buttons())
        assertEquals("23:10", page.resumeLabel)
        val resume = page.request(PageButton.RESUME) as PlayRequest.Startover
        assertEquals(1390.0, resume.resumeSeconds!!, 0.0)
        assertEquals("226411010101016", resume.idec)
        assertEquals(Instant.parse("2026-10-17T19:00:00Z"), resume.end)
        assertNull((page.request(PageButton.START_OVER) as PlayRequest.Startover).resumeSeconds)
        assertTrue(page.request(PageButton.LIVE) is PlayRequest.Live)
        assertNull(page.request(PageButton.OPEN_OFFICIAL_APP))
    }

    @Test fun airingCtWithoutOffer() {
        val page = ct("2026-10-17T17:30:00Z", "2026-10-17T19:00:00Z").page(now)!!
        assertEquals(listOf(PageButton.LIVE, PageButton.START_OVER, PageButton.OPEN_OFFICIAL_APP), page.buttons())
        assertNull(page.resumeLabel)
    }

    @Test fun endedCtWithOfferOffersResumeOrFromStart() {
        val page = ct("2026-10-17T08:00:00Z", "2026-10-17T09:00:00Z").page(now, offers)!!
        assertEquals(listOf(PageButton.RESUME, PageButton.START_OVER, PageButton.OPEN_OFFICIAL_APP), page.buttons())
        assertEquals(1390.0, (page.request(PageButton.RESUME) as PlayRequest.Programme).resumeSeconds!!, 0.0)
        assertNull((page.request(PageButton.START_OVER) as PlayRequest.Programme).resumeSeconds)
    }

    @Test fun endedCtNotMarkedPlayableIsStillOffered() {
        // Playing it shows CT's own text (DRM, licence), as before T15.
        val page = ct("2026-10-17T08:00:00Z", "2026-10-17T09:00:00Z", playable = false).page(now)!!
        assertEquals(listOf(PageButton.START_OVER, PageButton.OPEN_OFFICIAL_APP), page.buttons())
        assertEquals(false, (page.request(PageButton.START_OVER) as PlayRequest.Programme).isPlayableNow)
    }

    @Test fun futureProgrammesHaveNoButtons() {
        assertEquals(emptyList<PageButton>(), ct("2026-10-17T19:00:00Z", "2026-10-17T20:00:00Z").page(now, offers)!!.buttons())
        assertEquals(emptyList<PageButton>(),
            dvb("2026-10-17T19:00:00Z", "2026-10-17T20:00:00Z", RecordingState.SCHEDULED).page(now, offers)!!.buttons())
    }

    @Test fun finishedRecordingWithOfferOffersResumeOrFromStart() {
        val page = dvb("2026-10-17T08:00:00Z", "2026-10-17T09:00:00Z", RecordingState.COMPLETED).page(now, offers)!!
        assertEquals(listOf(PageButton.RESUME, PageButton.START_OVER), page.buttons())
        val resume = page.request(PageButton.RESUME) as PlayRequest.DvbRecorded
        assertEquals(1390.0, resume.resumeSeconds!!, 0.0)
        assertEquals(3600.0, resume.lengthSeconds!!, 0.0)
        assertEquals(false, resume.fromStart)
        assertNull((page.request(PageButton.START_OVER) as PlayRequest.DvbRecorded).resumeSeconds)
    }

    @Test fun notRecordedHasNoButtons() {
        val page = dvb("2026-10-17T08:00:00Z", "2026-10-17T09:00:00Z", null).page(now, offers)!!
        assertEquals(emptyList<PageButton>(), page.buttons())
    }

    @Test fun growingRecordingWithOfferHasAllThreeOptions() {
        val page = dvb("2026-10-17T17:30:00Z", "2026-10-17T19:00:00Z", RecordingState.RECORDING).page(now, offers)!!
        assertEquals(listOf(PageButton.LIVE, PageButton.RESUME, PageButton.START_OVER), page.buttons())
        assertTrue(page.request(PageButton.LIVE) is PlayRequest.DvbLive)
        assertEquals(1390.0, (page.request(PageButton.RESUME) as PlayRequest.DvbRecorded).resumeSeconds!!, 0.0)
        assertEquals(true, (page.request(PageButton.START_OVER) as PlayRequest.DvbRecorded).fromStart)
        assertEquals("23:10", page.resumeLabel)
    }

    @Test fun airingWithoutRecordingIsLiveOnly() {
        val page = dvb("2026-10-17T17:30:00Z", "2026-10-17T19:00:00Z", null).page(now, offers)!!
        assertEquals(listOf(PageButton.LIVE), page.buttons())
    }

    // T13: what "Pokračovat ve sledování" keeps with an entry played from the guide.
    @Test fun guideEntriesKeepACardForTheContinueRow() {
        val withEpisode = CtEntry(
            ct1,
            Programme("226411010101016", "Most!", Instant.parse("2026-10-17T17:30:00Z"), Instant.parse("2026-10-17T19:00:00Z"),
                episodeTitle = "Kovboj", isPlayableNow = true),
        )
        val card = withEpisode.resumeCard("https://img.example.invalid/a.jpg")!!
        assertEquals("Kovboj", card.title)
        assertEquals("Most!", card.showTitle)
        assertEquals("https://img.example.invalid/a.jpg", card.image)
        assertEquals("ct", card.source)
        assertEquals(Instant.parse("2026-10-17T17:30:00Z"), card.start)
        assertEquals("CH_1", card.channelId)
        val plain = ct("2026-10-17T17:30:00Z", "2026-10-17T19:00:00Z").resumeCard("http://img.example.invalid/a.jpg")!!
        assertEquals("Pořad", plain.title)
        assertNull(plain.showTitle)
        assertNull("only https:// images", plain.image)
        val recorded = dvb("2026-10-17T16:00:00Z", "2026-10-17T17:00:00Z", RecordingState.COMPLETED)
            .resumeCard("http://10.0.2.2:18098/dvb/recording/r1/thumb.jpg")!!
        assertEquals("a gateway path, never the gateway's URL", "/dvb/recording/r1/thumb.jpg", recorded.image)
        assertEquals("dvb", recorded.source)
        assertNull(dvb("2026-10-17T16:00:00Z", "2026-10-17T17:00:00Z", null).resumeCard(null)!!.image)
    }
}
