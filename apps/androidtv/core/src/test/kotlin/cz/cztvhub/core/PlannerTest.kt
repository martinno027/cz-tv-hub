package cz.cztvhub.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.time.Instant

class PlannerTest {
    private val now = Instant.parse("2026-10-17T18:00:00Z")

    private fun planner(vararg routes: Pair<String, String>): Pair<Planner, FakeTransport> {
        val transport = FakeTransport(routes.toMap())
        val api = CtApi(transport)
        return Planner(api, LiveResolver(api, clock = { now }), clock = { now }) to transport
    }

    private fun programme(start: String, end: String, playable: Boolean) = PlayRequest.Programme(
        "CH_1", "Pořad", "226411010101016", Instant.parse(start), Instant.parse(end), playable,
    )

    private fun failure(block: () -> Unit): CtException {
        try {
            block()
        } catch (e: CtException) {
            return e
        }
        fail("expected CtException")
        throw AssertionError()
    }

    @Test fun liveUsesMainUrl() {
        val (p, t) = planner("channel/CH_1" to "live_CH_1.json")
        val target = p.plan(PlayRequest.Live("CH_1", "ČT1"))
        assertEquals(PlayKind.LIVE, target.kind)
        assertTrue(target.url.contains("contentType=live"))
        val (url, body) = t.requests.single()
        assertTrue(url.contains("canPlayDrm=false"))
        assertTrue(!url.contains("skipIpAddressCheck"))
        assertEquals(null, body)
    }

    @Test fun liveIsReusedWithinTtlUnlessForced() {
        val (p, t) = planner("channel/CH_1" to "live_CH_1.json")
        p.plan(PlayRequest.Live("CH_1", "ČT1"))
        p.plan(PlayRequest.Live("CH_1", "ČT1"))
        assertEquals(1, t.requests.size)
        p.plan(PlayRequest.Live("CH_1", "ČT1"), forceRefresh = true)
        assertEquals(2, t.requests.size)
    }

    @Test fun airingProgrammeStartsOverWithWallClockAndOffset() {
        val (p, _) = planner("channel/CH_1" to "live_CH_1.json")
        val target = p.plan(programme("2026-10-17T17:30:00Z", "2026-10-17T19:00:00Z", true))
        assertEquals(PlayKind.TIMESHIFT, target.kind)
        assertTrue(target.url.contains("contentType=timeshift"))
        assertEquals(Instant.parse("2026-10-17T17:29:30Z"), target.seekToWallClock)
        assertEquals(8970_000L, target.fallbackOffsetMs)
    }

    @Test fun archivedProgrammeUsesVod() {
        val (p, t) = planner("media/external" to "vod_ok.json")
        val target = p.plan(programme("2026-10-17T08:00:00Z", "2026-10-17T09:00:00Z", true))
        assertEquals(PlayKind.VOD, target.kind)
        assertTrue(t.requests.single().first.contains("/media/external/226411010101016?"))
    }

    @Test fun stillLiveFallsBackToStartoverOnReportedChannel() {
        val (p, t) = planner("media/external" to "vod_still_live.json", "channel/CH_24" to "live_CH_1.json")
        val target = p.plan(programme("2026-10-17T13:00:00Z", "2026-10-17T14:00:00Z", true))
        assertEquals(PlayKind.TIMESHIFT, target.kind)
        assertEquals(0L, target.fallbackOffsetMs)
        assertTrue(t.requests.last().first.contains("channel/CH_24"))
    }

    @Test fun recentArchivedProgrammeUsesBufferWithoutAskingVod() {
        val (p, t) = planner("channel/CH_1" to "live_CH_1.json")
        val target = p.plan(programme("2026-10-17T16:00:00Z", "2026-10-17T17:00:00Z", true))
        assertEquals(PlayKind.TIMESHIFT, target.kind)
        assertEquals(3570_000L, target.fallbackOffsetMs)
        assertTrue(t.requests.none { it.first.contains("media/external") })
    }

    @Test fun unavailableStillAsksApiForCtReason() {
        val (p, _) = planner("media/external" to "vod_no_licence.json")
        val e = failure { p.plan(programme("2026-10-17T09:00:00Z", "2026-10-17T10:00:00Z", false)) }
        assertEquals("ČT nemá práva pro\u00A0internet", e.userMessage)
    }

    @Test fun drmOnlyArchiveItemIsNotRetriedThroughAnotherStream() {
        val (p, t) = planner("media/external" to "vod_drm.json")
        val e = failure { p.plan(programme("2026-10-17T09:00:00Z", "2026-10-17T10:00:00Z", true)) }
        assertTrue(e.userMessage.endsWith(Messages.DRM_HINT))
        assertEquals(1, t.requests.size)
    }

    @Test fun futureIsNotPlayableAndMakesNoRequest() {
        val (p, t) = planner()
        val e = failure { p.plan(programme("2026-10-17T20:00:00Z", "2026-10-17T21:00:00Z", false)) }
        assertEquals(Messages.FUTURE, e.userMessage)
        assertEquals(0, t.requests.size)
    }

    @Test fun missingTimeshiftHasCzechMessage() {
        val transport = HttpTransport { _, _ -> HttpResponse(200, """{"streamUrls":{"main":"https://m"}}""") }
        val api = CtApi(transport)
        val p = Planner(api, LiveResolver(api, clock = { now }), clock = { now })
        val e = failure { p.plan(PlayRequest.Startover("CH_1", "x", now.minusSeconds(600))) }
        assertEquals(Messages.NO_TIMESHIFT, e.userMessage)
    }

    @Test fun blockedLiveSurfacesMessage() {
        val (p, _) = planner("channel/CH_1" to "live_blocked.json")
        assertEquals(Messages.BLOCKED, failure { p.plan(PlayRequest.Live("CH_1", "ČT1")) }.userMessage)
    }

    @Test fun dvbTargetsCarryTheBearerKeyAndStartRecordingsAtZero() {
        val dvb = cz.cztvhub.core.dvb.DvbClient(cz.cztvhub.core.dvb.DvbConfig("http://10.0.2.2:8098", "k".repeat(32))) { _, _ ->
            throw java.io.IOException("no network needed")
        }
        val api = CtApi { _, _ -> throw java.io.IOException("no CT call expected") }
        val p = Planner(api, clock = { now }, dvb = { dvb })
        val live = p.plan(PlayRequest.DvbLive("c1", "ČT1 HD"))
        assertEquals("http://10.0.2.2:8098/dvb/live/c1.m3u8", live.url)
        assertEquals("Bearer ${"k".repeat(32)}", live.headers["Authorization"])
        val rec = p.plan(PlayRequest.DvbRecorded("c1", "x", "r1", fromStart = true))
        assertEquals("http://10.0.2.2:8098/dvb/recording/r1.m3u8", rec.url)
        assertEquals(0L, rec.startPositionMs)
        assertEquals(null, p.plan(PlayRequest.DvbRecorded("c1", "x", "r1", fromStart = false)).startPositionMs)
    }

    @Test fun dvbWithoutConfigurationFails() {
        val (p, _) = planner()
        failure { p.plan(PlayRequest.DvbLive("c1", "x")) }
    }

    @Test fun networkFailureIsGeneric() {
        val (p, _) = planner()
        assertEquals(Messages.GENERIC, failure { p.plan(PlayRequest.Live("CH_1", "ČT1")) }.userMessage)
    }
}
