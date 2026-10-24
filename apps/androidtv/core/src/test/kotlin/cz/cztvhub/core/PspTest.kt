package cz.cztvhub.core

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.time.Duration
import java.time.Instant

/** spec/fixtures/psp_cases.json; synthetic responses only, nothing here contacts psp.cz or its CDN. */
class PspTest {
    private val root = Fixtures.json("psp_cases.json")

    private fun strings(element: JsonElement?): List<String> =
        element?.jsonArray.orEmpty().map { it.jsonPrimitive.content }

    @Test fun catalogueMatchesFixture() {
        val expected = root.getValue("streams").jsonArray.map { it.jsonObject }
        assertEquals(expected.size, PspStreams.all.size)
        expected.zip(PspStreams.all).forEach { (e, stream) ->
            assertEquals(e.getValue("key").jsonPrimitive.content, stream.key)
            assertEquals(e.getValue("name").jsonPrimitive.content, stream.name)
            assertEquals(e.getValue("url").jsonPrimitive.content, stream.url)
            assertEquals(stream, PspStreams.byKey(stream.key))
            assertEquals(stream, PspStreams.byId(stream.id))
        }
        assertNull(PspStreams.byKey("psp:stream6"))
    }

    @Test fun intervalsMatchFixture() {
        assertEquals(root.getValue("checkIntervalSeconds").jsonPrimitive.long, PspLive.CHECK_INTERVAL.seconds)
        assertEquals(root.getValue("errorIntervalSeconds").jsonPrimitive.long, PspLive.ERROR_INTERVAL.seconds)
        assertEquals(root.getValue("staleLiveSeconds").jsonPrimitive.long, PspLive.STALE_AFTER.seconds)
    }

    @Test fun goldenLiveCases() {
        val cases = root.getValue("live").jsonArray
        assertTrue(cases.size >= 6)
        for (case in cases) {
            val c = case.jsonObject
            val name = c.getValue("name").jsonPrimitive.content
            val live = PspLive.isLive(c.getValue("status").jsonPrimitive.int, c.getValue("body").jsonPrimitive.content)
            assertEquals(name, c.getValue("live").jsonPrimitive.boolean, live)
        }
    }

    @Test fun liveEdgeCases() {
        assertTrue(PspLive.isLive(200, "﻿\r\n#EXTM3U\r\n#EXTINF:6.0,\r\nseg.ts\r\n"))
        assertFalse(PspLive.isLive(200, null))
        assertFalse(PspLive.isLive(206, "#EXTM3U\n#EXTINF:6.0,\nseg.ts\n"))
        assertFalse(PspLive.isLive(200, "seg.ts\n#EXTM3U\n#EXTINF:6.0,\n"))
    }

    @Test fun goldenArrangeCases() {
        val cases = root.getValue("arrange").jsonArray
        assertTrue(cases.size >= 2)
        for (case in cases) {
            val c = case.jsonObject
            val name = c.getValue("name").jsonPrimitive.content
            val p = c.getValue("prefs").jsonObject
            val prefs = ChannelPrefs(strings(p["order"]), strings(p["hidden"]).toSet(), strings(p["sourcesOff"]).toSet())
            val arranged = prefs.arrange(strings(c["channels"]))
            assertEquals(name, strings(c["all"]), arranged.all)
            assertEquals(name, strings(c["visible"]), arranged.visible)
        }
    }

    @Test fun goldenSourceCases() {
        val cases = root.getValue("sources").jsonArray
        assertTrue(cases.size >= 2)
        val ct = listOf("ct:CH_1")
        val dvb = listOf("dvb:a")
        val psp = PspStreams.all.map { it.key }
        for (case in cases) {
            val c = case.jsonObject
            val name = c.getValue("name").jsonPrimitive.content
            // Through the stored form, as the app reads it.
            val off = c["sourcesOff"]
            val stored = if (off == null || off is JsonNull) JsonObject(emptyMap()) else JsonObject(mapOf("sourcesOff" to off))
            val prefs = ChannelPrefs.decode(stored.toString())
            val on = strings(c["on"])
            assertEquals(name, on, prefs.sourcesOn)
            val expected = (if ("ct" in on) ct else emptyList()) + (if ("dvb" in on) dvb else emptyList()) +
                (if ("psp" in on) psp else emptyList())
            assertEquals(name, expected, prefs.sourceKeys(ct, dvb, psp))
            assertEquals(name, prefs, ChannelPrefs.decode(prefs.encode()))
        }
        assertEquals(setOf("psp"), ChannelPrefs.DEFAULT.toggleSource(ChannelPrefs.SOURCE_PSP).sourcesOff)
    }

    @Test fun plannerPlaysThePublishedUrlWithoutHeaders() {
        val api = CtApi { _, _ -> throw IOException("no CT call expected") }
        val target = Planner(api).plan(PlayRequest.PspLive("stream2", "Tiskové konference"))
        assertEquals(PspStreams.byId("stream2")!!.url, target.url)
        assertEquals(PlayKind.LIVE, target.kind)
        assertTrue(target.headers.isEmpty())
        assertNull(target.startPositionMs)
        assertNull(ResumeRules.spot(PlayRequest.PspLive("stream2", "x")))
        val unknown = runCatching { Planner(api).plan(PlayRequest.PspLive("stream9", "x")) }.exceptionOrNull()
        assertEquals(PspMessages.NOT_ON, (unknown as CtException).userMessage)
    }

    /** Fake clock and transport: answers per URL, records every request. */
    private class Harness {
        var now: Instant = Instant.parse("2026-10-24T08:00:00Z")
        val answers = mutableMapOf<String, () -> HttpResponse>()
        val requests = mutableListOf<String>()
        val monitor = PspMonitor({ url, body ->
            assertNull(body)
            requests += url
            (answers[url] ?: { HttpResponse(404, "NOT_FOUND") })()
        }, { now })

        fun url(id: String) = PspStreams.byId(id)!!.url
        fun advance(seconds: Long) { now = now.plusSeconds(seconds) }
    }

    private val livePlaylist = HttpResponse(200, "#EXTM3U\n#EXTINF:6.0,\nseg1.ts\n")
    private val all = PspStreams.all.map { it.id }

    @Test fun checksOneAfterAnotherAtMostOncePerMinute() {
        val h = Harness()
        h.answers[h.url("stream1")] = { livePlaylist }
        assertEquals(PspState.UNKNOWN, h.monitor.state("stream1"))
        // One check per call, the streams in catalogue order.
        repeat(5) { h.monitor.runDue(all) }
        assertEquals(PspStreams.all.map { it.url }, h.requests)
        assertEquals(PspState.LIVE, h.monitor.state("stream1"))
        assertEquals(PspState.OFF, h.monitor.state("stream2"))
        // Nothing is due again before 60 s.
        val nextDue = h.monitor.runDue(all)
        assertEquals(5, h.requests.size)
        assertEquals(h.now.plus(PspLive.CHECK_INTERVAL), nextDue)
        h.advance(59)
        h.monitor.runDue(all)
        assertEquals(5, h.requests.size)
        h.advance(1)
        h.monitor.runDue(all)
        assertEquals(h.url("stream1"), h.requests.last())
        assertEquals(6, h.requests.size)
        // Only playlists of the catalogue, never a segment.
        assertTrue(h.requests.all { url -> PspStreams.all.any { it.url == url } })
    }

    @Test fun onlyTheGivenStreams() {
        val h = Harness()
        repeat(4) { h.monitor.runDue(listOf("stream3", "nonsense")) }
        assertEquals(listOf(h.url("stream3")), h.requests)
        assertNull(h.monitor.runDue(emptyList()))
    }

    @Test fun networkErrorKeepsStateButNotLiveBeyondThreeMinutes() {
        val h = Harness()
        h.answers[h.url("stream1")] = { livePlaylist }
        h.monitor.runDue(listOf("stream1"))
        assertEquals(PspState.LIVE, h.monitor.state("stream1"))
        h.answers[h.url("stream1")] = { throw IOException("offline") }
        h.advance(60)
        val next = h.monitor.runDue(listOf("stream1"))
        assertEquals(PspState.LIVE, h.monitor.state("stream1")) // kept
        assertEquals(h.now.plus(PspLive.ERROR_INTERVAL), next) // 120 s after an error
        h.advance(119)
        h.monitor.runDue(listOf("stream1"))
        assertEquals(2, h.requests.size)
        assertEquals(PspState.LIVE, h.monitor.state("stream1", h.now.minusSeconds(1))) // 178 s after the last answer
        h.advance(1) // 180 s
        assertEquals(PspState.LIVE, h.monitor.state("stream1"))
        assertEquals(PspState.UNKNOWN, h.monitor.state("stream1", h.now.plusSeconds(1))) // stale: no longer live
        h.monitor.runDue(listOf("stream1")) // the error again, 180 s after the last answer
        assertEquals(3, h.requests.size)
        h.advance(1)
        assertEquals(PspState.UNKNOWN, h.monitor.state("stream1"))
        // A confirming answer shows it again.
        h.answers[h.url("stream1")] = { livePlaylist }
        h.advance(120)
        h.monitor.runDue(listOf("stream1"))
        assertEquals(PspState.LIVE, h.monitor.state("stream1"))
    }

    @Test fun serverErrorsWaitLongerNotFoundDoesNot() {
        val h = Harness()
        h.answers[h.url("stream4")] = { HttpResponse(503, "") }
        val next = h.monitor.runDue(listOf("stream4"))
        assertEquals(PspState.OFF, h.monitor.state("stream4"))
        assertEquals(h.now.plus(PspLive.ERROR_INTERVAL), next)
        // A stream that stopped: live → off at the next check; the 404 "not processed now" is a normal answer (60 s).
        h.answers[h.url("stream5")] = { livePlaylist }
        assertEquals(h.now.plus(PspLive.CHECK_INTERVAL), h.monitor.runDue(listOf("stream5")))
        h.answers[h.url("stream5")] = { HttpResponse(404, "NOT_FOUND: channel ps-stream5 is not processed now") }
        h.advance(60)
        assertEquals(h.now.plus(PspLive.CHECK_INTERVAL), h.monitor.runDue(listOf("stream5")))
        assertEquals(PspState.OFF, h.monitor.state("stream5"))
        // Only "live" goes stale; "off" stays the last known state.
        assertEquals(PspState.OFF, h.monitor.state("stream5", h.now.plus(Duration.ofHours(1))))
        // Any other 4xx: not on, a normal answer too.
        h.answers[h.url("stream1")] = { HttpResponse(403, "") }
        assertEquals(h.now.plus(PspLive.CHECK_INTERVAL), h.monitor.runDue(listOf("stream1")))
        assertEquals(PspState.OFF, h.monitor.state("stream1"))
        // A 200 that is no playlist: off, normal interval.
        h.answers[h.url("stream3")] = { HttpResponse(200, "<html>maintenance</html>") }
        assertEquals(h.now.plus(PspLive.CHECK_INTERVAL), h.monitor.runDue(listOf("stream3")))
        assertEquals(PspState.OFF, h.monitor.state("stream3"))
        assertEquals(PspStreams.all.size, h.monitor.states().size)
    }

    @Test fun networkErrorKeepsOff() {
        val h = Harness()
        assertEquals(h.now.plus(PspLive.CHECK_INTERVAL), h.monitor.runDue(listOf("stream2"))) // 404: off, 60 s
        h.answers[h.url("stream2")] = { throw IOException("offline") }
        h.advance(60)
        assertEquals(h.now.plus(PspLive.ERROR_INTERVAL), h.monitor.runDue(listOf("stream2")))
        assertEquals(2, h.requests.size)
        assertEquals(PspState.OFF, h.monitor.state("stream2"))
    }

    @Test fun beforePlaybackReusesAnAnswerYoungerThanAMinute() {
        val h = Harness()
        // Never checked: one check now.
        assertEquals(PspState.OFF, h.monitor.stateForPlayback("stream1"))
        assertEquals(1, h.requests.size)
        // Younger than 60 s: reused, no request.
        h.answers[h.url("stream1")] = { livePlaylist }
        h.advance(59)
        assertEquals(PspState.OFF, h.monitor.stateForPlayback("stream1"))
        assertEquals(1, h.requests.size)
        // 60 s: one more check of the same playlist.
        h.advance(1)
        assertEquals(PspState.LIVE, h.monitor.stateForPlayback("stream1"))
        assertEquals(listOf(h.url("stream1"), h.url("stream1")), h.requests)
        // That check also counts for the screens' schedule.
        assertEquals(h.now.plus(PspLive.CHECK_INTERVAL), h.monitor.runDue(listOf("stream1")))
        assertEquals(2, h.requests.size)
        // No answer: the last known state; an unknown id is never fetched.
        h.answers[h.url("stream1")] = { throw IOException("offline") }
        h.advance(60)
        assertEquals(PspState.LIVE, h.monitor.stateForPlayback("stream1"))
        assertEquals(PspState.UNKNOWN, h.monitor.stateForPlayback("stream9"))
        assertEquals(3, h.requests.size)
    }

    @Test fun neverChecksWithoutBeingAsked() {
        val h = Harness()
        h.advance(3600)
        assertEquals(PspState.UNKNOWN, h.monitor.state("stream1"))
        assertTrue(h.requests.isEmpty())
    }
}
