package cz.cztvhub.tv.ui

import cz.cztvhub.core.Channels
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

class EntriesTest {
    private val now = Instant.parse("2026-10-17T18:00:00Z")
    private val ct1 = Channels.byId("CH_1")!!

    private fun ct(start: String, end: String) = CtEntry(
        ct1,
        Programme("226411010101016", "Pořad", Instant.parse(start), Instant.parse(end), isPlayableNow = true),
    )

    private fun dvb(start: String, end: String, state: RecordingState) = DvbEntry(
        DvbChannel("c1", 1, "ČT1 HD", null),
        DvbEvent("e1", "c1", "Film", Instant.parse(start), Instant.parse(end), recording = DvbRecording("r1", state)),
    )

    private val offers: ResumeOffers = { key -> if (key == "ct:226411010101016" || key == "dvb:r1") 1390.0 else null }

    @Test fun airingCtWithOfferResumesFirst() {
        val choice = ct("2026-10-17T17:30:00Z", "2026-10-17T19:00:00Z").select(now, offers) as Selection.Choice
        assertEquals("23:10", choice.resumeLabel)
        val resume = choice.resume as PlayRequest.Startover
        assertEquals(1390.0, resume.resumeSeconds!!, 0.0)
        assertEquals("226411010101016", resume.idec)
        assertEquals(Instant.parse("2026-10-17T19:00:00Z"), resume.end)
        assertNull((choice.fromStart as PlayRequest.Startover).resumeSeconds)
        assertTrue(choice.live is PlayRequest.Live)
    }

    @Test fun airingCtWithoutOfferIsUnchanged() {
        val choice = ct("2026-10-17T17:30:00Z", "2026-10-17T19:00:00Z").select(now) as Selection.Choice
        assertNull(choice.resume)
        assertNull(choice.resumeLabel)
        assertTrue(choice.fromStart is PlayRequest.Startover)
    }

    @Test fun endedCtWithOfferOffersResumeOrFromStart() {
        val choice = ct("2026-10-17T08:00:00Z", "2026-10-17T09:00:00Z").select(now, offers) as Selection.Choice
        assertNull(choice.live)
        assertEquals(1390.0, (choice.resume as PlayRequest.Programme).resumeSeconds!!, 0.0)
        assertNull((choice.fromStart as PlayRequest.Programme).resumeSeconds)
    }

    @Test fun endedCtWithoutOfferPlaysDirectly() {
        val play = ct("2026-10-17T08:00:00Z", "2026-10-17T09:00:00Z").select(now) as Selection.Play
        assertNull((play.request as PlayRequest.Programme).resumeSeconds)
    }

    @Test fun finishedRecordingWithOfferOffersResumeOrFromStart() {
        val choice = dvb("2026-10-17T08:00:00Z", "2026-10-17T09:00:00Z", RecordingState.COMPLETED)
            .select(now, offers) as Selection.Choice
        assertNull(choice.live)
        val resume = choice.resume as PlayRequest.DvbRecorded
        assertEquals(1390.0, resume.resumeSeconds!!, 0.0)
        assertEquals(3600.0, resume.lengthSeconds!!, 0.0)
        assertEquals(false, resume.fromStart)
        assertNull((choice.fromStart as PlayRequest.DvbRecorded).resumeSeconds)
    }

    @Test fun finishedRecordingWithoutOfferPlaysAndCarriesLength() {
        val play = dvb("2026-10-17T08:00:00Z", "2026-10-17T09:00:00Z", RecordingState.COMPLETED).select(now) as Selection.Play
        assertEquals(3600.0, (play.request as PlayRequest.DvbRecorded).lengthSeconds!!, 0.0)
    }

    @Test fun growingRecordingWithOfferHasAllThreeOptions() {
        val choice = dvb("2026-10-17T17:30:00Z", "2026-10-17T19:00:00Z", RecordingState.RECORDING)
            .select(now, offers) as Selection.Choice
        assertTrue(choice.live is PlayRequest.DvbLive)
        assertEquals(1390.0, (choice.resume as PlayRequest.DvbRecorded).resumeSeconds!!, 0.0)
        assertEquals("23:10", choice.resumeLabel)
    }
}
