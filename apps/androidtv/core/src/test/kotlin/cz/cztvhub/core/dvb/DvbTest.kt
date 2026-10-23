package cz.cztvhub.core.dvb

import cz.cztvhub.core.CtException
import cz.cztvhub.core.Fixtures
import cz.cztvhub.core.HttpResponse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.IOException
import java.time.Instant
import java.time.LocalDate

/** Fixtures captured from the dev gateway (fake TVHeadend) into spec/fixtures/dvb/. */
class DvbTest {
    private val config = DvbConfig("http://10.0.2.2:8098", "dev-key-dev-key-dev-key-dev-key-0000")
    private val ct1 = "cb2de4250e92ae9a4c549f7e48885ff8"
    private val captured = Instant.parse("2026-10-18T06:45:00Z")

    private fun fixture(name: String) = Fixtures.response("dvb/$name")

    private fun client(vararg routes: Pair<String, String>, seen: MutableList<Pair<String, Map<String, String>>> = ArrayList()) =
        DvbClient(config) { url, headers ->
            seen += url to headers
            val name = routes.firstOrNull { url.contains(it.first) }?.second ?: throw IOException("no route $url")
            fixture(name)
        }

    private fun failure(block: () -> Unit): CtException {
        try {
            block()
        } catch (e: CtException) {
            return e
        }
        fail("expected CtException")
        throw AssertionError()
    }

    @Test fun channelsSortedWithOptionalIcon() {
        val channels = client("/dvb/channels" to "dvb_channels.json").channels()
        assertEquals(listOf("ČT1 HD", "ČT2 HD", "Nova HD", "Prima HD"), channels.map { it.name })
        assertEquals("/dvb/icon/$ct1", channels[0].iconPath)
        assertNull(channels[1].iconPath)
    }

    @Test fun everyRequestCarriesTheBearerKey() {
        val seen = ArrayList<Pair<String, Map<String, String>>>()
        client("/dvb/status" to "dvb_status.json", seen = seen).status()
        assertEquals("http://10.0.2.2:8098/dvb/status", seen.single().first)
        assertEquals("Bearer dev-key-dev-key-dev-key-dev-key-0000", seen.single().second["Authorization"])
    }

    @Test fun statusParses() {
        val status = client("/dvb/status" to "dvb_status.json").status()
        assertTrue(status.enabled)
        assertTrue(status.tvheadendReachable)
        assertTrue(status.retentionDays in 1..14)
    }

    @Test fun statusThumbnailsFlag() {
        val on = DvbParser.parseStatus(200, """{"enabled": true, "retentionDays": 3, "tuners": [], "tvheadendReachable": true, "thumbnails": true}""")
        assertTrue(on.thumbnails)
        val off = DvbParser.parseStatus(200, """{"enabled": true, "retentionDays": 3, "tuners": [], "thumbnails": false}""")
        assertFalse(off.thumbnails)
        // older gateways without the field: no thumbnails
        assertFalse(client("/dvb/status" to "dvb_status.json").status().thumbnails)
    }

    @Test fun thumbnailOnlyForRecordedEventsWhenBothAllow() {
        fun event(state: RecordingState?) = DvbEvent("e", ct1, "t", captured, captured.plusSeconds(1800),
            recording = state?.let { DvbRecording("rec-${it.name}", it) })
        assertEquals("rec-COMPLETED", DvbThumbnails.recordingId(event(RecordingState.COMPLETED), true, true))
        assertEquals("rec-RECORDING", DvbThumbnails.recordingId(event(RecordingState.RECORDING), true, true))
        assertNull(DvbThumbnails.recordingId(event(RecordingState.SCHEDULED), true, true))
        assertNull(DvbThumbnails.recordingId(event(RecordingState.FAILED), true, true))
        assertNull(DvbThumbnails.recordingId(event(null), true, true))
        assertNull(DvbThumbnails.recordingId(event(RecordingState.COMPLETED), false, true))
        assertNull(DvbThumbnails.recordingId(event(RecordingState.COMPLETED), true, false))
        assertEquals("http://10.0.2.2:8098/dvb/recording/abc/thumb.jpg", DvbClient(config).thumbnailUrl("abc"))
    }

    @Test fun epgParsesRecordingStatesAndOpaqueIds() {
        val events = client("/dvb/epg?date=2026-10-18" to "dvb_epg_today.json").epg(LocalDate.of(2026, 10, 18))
        assertTrue(events.size > 100)
        val states = events.mapNotNull { it.recording?.state }.toSet()
        assertEquals(RecordingState.entries.toSet(), states)
        val past = events.first { it.id.startsWith("dvr:") }
        assertEquals(RecordingState.COMPLETED, past.recording?.state)
        assertTrue(events.any { it.recording == null && it.genre.isNotEmpty() })
    }

    @Test fun unauthorizedAndBadDateShowCzechText() {
        assertEquals(DvbMessages.BAD_KEY, failure { client("/dvb/" to "dvb_unauthorized.json").channels() }.userMessage)
        val bad = failure { client("/dvb/epg" to "dvb_epg_bad_date.json").epg(LocalDate.of(2020, 1, 1)) }
        assertEquals("400", bad.status)
        assertTrue(bad.userMessage.isNotBlank())
    }

    @Test fun tunerBusyErrorTextIsReadFromBody() {
        assertEquals("Tuner je obsazený", DvbParser.errorText("""{"error": "Tuner je obsazený"}"""))
        val e = failure { DvbParser.parseChannels(503, """{"error": "Tuner je obsazený"}""") }
        assertEquals("Tuner je obsazený", e.userMessage)
        assertNull(DvbParser.errorText("<html>"))
    }

    @Test fun unreachableGatewayIsCzech() {
        assertEquals(DvbMessages.UNREACHABLE, failure { client().channels() }.userMessage)
    }

    @Test fun garbageDoesNotCrash() {
        assertEquals(emptyList<DvbEvent>(), DvbParser.parseEpg(200, """{"events":[1,{"id":"x"},null]}"""))
        failure { DvbParser.parseEpg(200, "not json") }
    }

    @Test fun repositoryDeduplicatesEventsAndFindsOnAir() {
        val repo = DvbRepository(client("/dvb/epg" to "dvb_epg_today.json"), clock = { captured })
        val onAir = repo.onAir(captured)
        val ct1Now = onAir.getValue(ct1).current!!
        assertEquals(RecordingState.RECORDING, ct1Now.recording?.state)
        val day = repo.day(LocalDate.of(2026, 10, 18))
        day.values.forEach { list -> assertEquals(list.size, list.map { it.id }.toSet().size) }
    }

    @Test fun decisionsFollowDocsDvb() {
        val events = client("/dvb/epg" to "dvb_epg_today.json").epg(LocalDate.of(2026, 10, 18))
        fun action(state: RecordingState?, airing: Boolean) = events.first {
            it.recording?.state == state && it.isAiring(captured) == airing && (airing || it.end <= captured)
        }.let { DvbDecisions.decide(it, captured) }
        assertEquals(DvbAction.LIVE_OR_STARTOVER, action(RecordingState.RECORDING, true))
        assertEquals(DvbAction.RECORDING, action(RecordingState.COMPLETED, false))
        assertEquals(DvbAction.NOT_RECORDED, action(RecordingState.FAILED, false))
        assertEquals(DvbAction.LIVE, action(null, true))
        val future = events.first { it.recording?.state == RecordingState.SCHEDULED }
        assertEquals(DvbAction.FUTURE, DvbDecisions.decide(future, captured))
    }

    @Test fun decisionEdges() {
        val start = Instant.parse("2026-10-18T10:00:00Z")
        val end = Instant.parse("2026-10-18T11:00:00Z")
        fun e(state: RecordingState?) = DvbEvent("1", "c", "t", start, end, recording = state?.let { DvbRecording("r", it) })
        assertEquals(DvbAction.LIVE, DvbDecisions.decide(e(RecordingState.COMPLETED), start))
        assertEquals(DvbAction.NOT_RECORDED, DvbDecisions.decide(e(null), end))
        assertEquals(DvbAction.NOT_RECORDED, DvbDecisions.decide(e(RecordingState.RECORDING), end))
        assertEquals(DvbAction.NOT_RECORDED, DvbDecisions.decide(e(RecordingState.SCHEDULED), end))
    }

    @Test fun addressNormalisationAllowsOnlyPrivateCleartext() {
        assertEquals("http://pi.tail1234.ts.net:8098", DvbConfig.normalizeAddress("pi.tail1234.ts.net"))
        assertEquals("http://pi:8098", DvbConfig.normalizeAddress("pi"))
        assertEquals("http://192.168.1.5:9000", DvbConfig.normalizeAddress("http://192.168.1.5:9000/"))
        assertEquals("http://100.101.102.103:8098", DvbConfig.normalizeAddress("100.101.102.103"))
        assertEquals("http://10.0.2.2:8098", DvbConfig.normalizeAddress("10.0.2.2:8098"))
        assertEquals("http://cztvhub.local:8098", DvbConfig.normalizeAddress("cztvhub.local"))
        assertNull(DvbConfig.normalizeAddress("example.com"))
        assertNull(DvbConfig.normalizeAddress("8.8.8.8"))
        assertNull(DvbConfig.normalizeAddress("100.200.1.1"))
        assertNull(DvbConfig.normalizeAddress(""))
        assertNull(DvbConfig.normalizeAddress("https://example.com"))
        assertEquals("https://pi.tail1234.ts.net:443", DvbConfig.normalizeAddress("https://pi.tail1234.ts.net"))
        assertFalse(DvbConfig.isPrivateHost("172.32.0.1"))
    }

    @Test fun playbackUrls() {
        val c = DvbClient(config) { _, _ -> HttpResponse(200, "{}") }
        assertEquals("http://10.0.2.2:8098/dvb/live/$ct1.m3u8", c.liveUrl(ct1))
        assertEquals("http://10.0.2.2:8098/dvb/recording/abc.m3u8", c.recordingUrl("abc"))
    }
}
