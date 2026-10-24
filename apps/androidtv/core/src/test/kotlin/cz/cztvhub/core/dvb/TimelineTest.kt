package cz.cztvhub.core.dvb

import cz.cztvhub.core.CtException
import cz.cztvhub.core.Fixtures
import cz.cztvhub.core.HttpResponse
import cz.cztvhub.core.PlayKind
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.Planner
import cz.cztvhub.core.CtApi
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.double
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.IOException
import java.time.Instant

/** spec/apps.md "Anténa timeline player": golden spec/fixtures/timeline_cases.json and dvb/dvb_timeline.json. */
class TimelineTest {
    private val cases = Fixtures.json("timeline_cases.json")
    private val sample = Fixtures.text("dvb/dvb_timeline.json")
    private val config = DvbConfig("http://10.0.2.2:8098", "dev-key-dev-key-dev-key-dev-key-0000")

    private fun group(name: String): List<JsonObject> =
        cases.getValue(name).jsonArray.map { it.jsonObject }.also { assertTrue("$name is empty", it.isNotEmpty()) }

    private fun JsonObject.text(key: String) = getValue(key).jsonPrimitive.content
    private fun JsonObject.time(key: String): Instant = Instant.parse(text(key))
    private fun JsonObject.timeOrNull(key: String): Instant? = getValue(key).let { if (it is JsonNull) null else Instant.parse(it.jsonPrimitive.content) }
    private fun JsonObject.num(key: String) = getValue(key).jsonPrimitive.double
    private fun JsonObject.flag(key: String) = getValue(key).jsonPrimitive.boolean

    private val map = cases.getValue("map").jsonObject
    private val pieces = map.getValue("pieces").jsonArray.map {
        val o = it.jsonObject
        TimelinePiece(o.num("position"), o.time("time"), o.num("duration"))
    }
    private val programmes = group("programmes").map {
        TimelineProgramme(null, it.text("title"), "", it.time("start"), it.time("end"))
    }

    private fun parse(body: String, status: Int = 200) = DvbParser.parseTimeline(status, body)

    private fun failure(block: () -> Unit): CtException {
        try {
            block()
        } catch (e: CtException) {
            return e
        }
        fail("expected CtException")
        throw AssertionError()
    }

    // -- golden cases ------------------------------------------------------------------------

    @Test fun mapToTime() {
        val all = map.getValue("toTime").jsonArray.map { it.jsonObject }
        assertTrue(all.isNotEmpty())
        all.forEach { assertEquals(it.text("name"), it.time("time"), TimelineRules.toTime(pieces, it.num("position"))) }
    }

    @Test fun mapToPosition() {
        val all = map.getValue("toPosition").jsonArray.map { it.jsonObject }
        assertTrue(all.isNotEmpty())
        all.forEach { assertEquals(it.text("name"), it.num("position"), TimelineRules.toPosition(pieces, it.time("time")), 0.001) }
    }

    @Test fun edge() {
        group("edge").forEach {
            val result = TimelineRules.edge(it.num("target"), it.num("duration"), it.flag("complete"), it.flag("previous"), it.flag("next"))
            assertEquals(it.text("name"), it.text("result"), result.name.lowercase())
        }
    }

    @Test fun hold() {
        group("hold").forEach { assertEquals(it.text("name"), it.num("offset"), TimelineRules.holdOffset(it.num("held")), 0.001) }
    }

    @Test fun labels() {
        group("labels").forEach { assertEquals(it.text("name"), it.text("label"), TimelineRules.label(it.time("time"), it.time("now"))) }
    }

    @Test fun edges() {
        group("edges").forEach {
            val now = it.time("now")
            val playhead = it.time("playhead")
            assertEquals(it.text("name"), it.text("startLabel"), TimelineRules.edgeLabel(it.time("start"), playhead, now))
            assertEquals(it.text("name"), it.text("endLabel"), TimelineRules.endLabel(it.time("end"), complete = true, playhead, now))
        }
    }

    @Test fun programmeAt() {
        group("programmeAt").forEach {
            val expected = it.getValue("index").let { v -> if (v is JsonNull) null else v.jsonPrimitive.int }
            assertEquals(it.text("name"), expected, TimelineRules.programmeAt(programmes, it.time("time")))
        }
    }

    @Test fun jump() {
        group("jump").forEach {
            val up = when (it.text("direction")) {
                "up" -> true
                "down" -> false
                else -> error("direction ${it.text("direction")}")
            }
            assertEquals(it.text("name"), it.timeOrNull("target"), TimelineRules.jump(programmes, it.time("time"), up))
        }
    }

    @Test fun resume() {
        group("resume").forEach {
            val programme = it.getValue("programme").jsonObject
            assertEquals(it.text("name"), it.num("progress"), TimelineRules.resumeProgress(programme.time("start"), it.time("time")), 0.001)
        }
    }

    // -- rules beyond the golden cases -------------------------------------------------------

    @Test fun programmeEndFollowedByAGapIsNoProgramme() {
        // Film ends 11:00, Počasí starts 11:10; Počasí ends 11:20 with nothing after.
        assertNull(TimelineRules.programmeAt(programmes, Instant.parse("2026-10-24T11:00:00Z")))
        assertNull(TimelineRules.programmeAt(programmes, Instant.parse("2026-10-24T11:20:00Z")))
        assertEquals(1, TimelineRules.programmeAt(programmes, Instant.parse("2026-10-24T10:59:59Z")))
    }

    @Test fun upJumpSkipsAStartInsideARecordingGap() {
        // dvb_timeline.json: "Zprávy" starts 09:00, inside the gap 09:00–09:05, so it maps to 10800 (09:05).
        val t = parse(sample)
        val playhead = 10805.0 // 09:05:05, 5 min 5 s into "Zprávy"
        assertEquals(Instant.parse("2026-10-24T09:00:00Z"), TimelineRules.jump(t.programmes, TimelineRules.toTime(t.pieces, playhead), up = true))
        // Mapped, that start is 5 s back: ▲ takes "Ranní show" (06:00 = 0) instead of getting stuck.
        assertEquals(0.0, TimelineRules.jumpTarget(t, playhead, up = true)!!, 0.001)
        // Further in, ▲ goes to the (mapped) start of "Zprávy".
        assertEquals(10800.0, TimelineRules.jumpTarget(t, 11400.0, up = true)!!, 0.001)
        // ▼ from "Zprávy" goes to "Teleshopping" 09:30 = 10800 + 1500.
        assertEquals(12300.0, TimelineRules.jumpTarget(t, 11400.0, up = false)!!, 0.001)
        // Nothing before "Ranní show".
        assertNull(TimelineRules.jumpTarget(t, 5.0, up = true))
    }

    @Test fun jumpTargetsOutsideTheWindowFollowTheEdgeRule() {
        val t = parse(sample).let { it.copy(programmes = it.programmes + TimelineProgramme("bnova1-1792850000-1792853600", "Late", "", Instant.parse("2026-10-24T13:00:00Z"), Instant.parse("2026-10-24T14:00:00Z"))) }
        // The window ends 09:05 + 10500.5 s = 12:00:00.5; "Late" at 13:00 is ~3600 s past the end.
        val target = TimelineRules.jumpTarget(t, 20000.0, up = false)!!
        assertEquals(t.duration + 3599.5, target, 0.001)
        assertEquals(TimelineEdge.CLAMP, TimelineRules.edge(target, t.duration, t.complete, t.previous != null, t.next != null))
        assertEquals(TimelineEdge.NEXT, TimelineRules.edge(target, t.duration, true, false, true))
        // Before the window: negative, so a ▲ there opens `previous`.
        assertEquals(-60.0, TimelineRules.targetPosition(t.pieces, Instant.parse("2026-10-24T05:59:00Z")), 0.001)
    }

    @Test fun adBreaksOnlyForAnEndedProgrammeWithBreaks() {
        val t = parse(sample)
        val inNews = Instant.parse("2026-10-24T09:10:00Z")
        assertEquals("Zprávy", TimelineRules.breaksProgramme(t.programmes, inNews, now = Instant.parse("2026-10-24T09:30:00Z"))?.title)
        // Still airing (also inside a growing window): no button.
        assertNull(TimelineRules.breaksProgramme(t.programmes, inNews, now = Instant.parse("2026-10-24T09:29:59Z")))
        // "Ranní show" has no breaks stored, "Teleshopping" no id.
        val late = Instant.parse("2026-10-24T20:00:00Z")
        assertNull(TimelineRules.breaksProgramme(t.programmes, Instant.parse("2026-10-24T08:00:00Z"), late))
        assertNull(TimelineRules.breaksProgramme(t.programmes, Instant.parse("2026-10-24T09:40:00Z"), late))
    }

    @Test fun mediaKeysCountAtLeastOneSecond() {
        val tap = TimelineScrub(from = 600.0, forward = true, downAtMs = 1_000, minHeldSeconds = TimelineRules.MEDIA_MIN_HELD_SECONDS)
        assertEquals(660.0, tap.target(1_100), 0.001)
        assertEquals(600.0 + 1380, tap.target(6_000), 0.001)
        // An arrow tap is the 10 s step.
        assertEquals(610.0, TimelineScrub(600.0, true, 1_000).target(1_100), 0.001)
    }

    @Test fun playlistCheckKeepsTheGatewaysText() {
        val t = parse(sample)
        val seen = ArrayList<Pair<String, Map<String, String>>>()
        DvbClient(config) { url, headers ->
            seen += url to headers
            HttpResponse(200, "#EXTM3U")
        }.checkPlaylist(t)
        assertEquals("http://10.0.2.2:8098/dvb/timeline/bnova1-1792832400-1792834200.m3u8", seen.single().first)
        assertEquals("Bearer dev-key-dev-key-dev-key-dev-key-0000", seen.single().second["Authorization"])
        val e = failure { DvbClient(config) { _, _ -> HttpResponse(404, """{"error": "Nahrávka není k dispozici"}""") }.checkPlaylist(t) }
        assertEquals("Nahrávka není k dispozici", e.userMessage)
        val down = failure { DvbClient(config) { _, _ -> throw IOException("refused") }.checkPlaylist(t) }
        assertEquals(DvbMessages.UNREACHABLE, down.userMessage)
    }

    @Test fun growingWindowRunsOnPastItsLastPiece() {
        val end = Instant.parse("2026-10-24T10:35:00Z")
        assertEquals(end, TimelineRules.toTime(pieces, 6000.0))
        assertEquals(end.plusSeconds(600), TimelineRules.toTime(pieces, 6000.0, growing = true))
        // Inside the window a growing window maps like a complete one.
        assertEquals(TimelineRules.toTime(pieces, 4000.0), TimelineRules.toTime(pieces, 4000.0, growing = true))
    }

    @Test fun targetTimeExtendsBeyondBothEnds() {
        assertEquals(Instant.parse("2026-10-24T08:59:50Z"), TimelineRules.targetTime(pieces, -10.0))
        assertEquals(Instant.parse("2026-10-24T10:35:10Z"), TimelineRules.targetTime(pieces, 5410.0))
        assertEquals(TimelineRules.toTime(pieces, 4000.0), TimelineRules.targetTime(pieces, 4000.0))
    }

    @Test fun growingEndReadsNowWhileTheRecordingReachesNow() {
        val now = Instant.parse("2026-10-24T19:00:00Z")
        val playhead = Instant.parse("2026-10-24T17:00:00Z")
        val end = Instant.parse("2026-10-24T18:59:40Z")
        assertEquals("Teď", TimelineRules.endLabel(end, complete = false, playhead, now))
        assertEquals("20:59", TimelineRules.endLabel(end, complete = true, playhead, now))
        assertEquals("20:58", TimelineRules.endLabel(end.minusSeconds(60), complete = false, playhead, now))
    }

    @Test fun scrubFollowsTheHoldAndStepsOnAShortPress() {
        val back = TimelineScrub(from = 600.0, forward = false, downAtMs = 1_000)
        assertEquals(590.0, back.target(1_200), 0.001)
        assertEquals(600.0 - 1380, back.target(6_000), 0.001)
        val forward = TimelineScrub(from = 600.0, forward = true, downAtMs = 1_000)
        assertEquals(660.0, forward.target(2_000), 0.001)
    }

    @Test fun timelineIdsAreContinuousRecordingsOnly() {
        assertTrue(TimelineRules.isTimelineId("bnova1-1792832400-1792834200"))
        // A TVHeadend DVR uuid may begin with "b" too; it keeps the old player.
        assertFalse(TimelineRules.isTimelineId("b3f9d2c4e5a6b7c8d9e0f1a2b3c4d5e6"))
        assertFalse(TimelineRules.isTimelineId("cb2de4250e92ae9a4c549f7e48885ff8"))
        assertFalse(TimelineRules.isTimelineId("bnova1-1792832400-1792834200/../x"))
    }

    @Test fun startPositionFromStartResumeAndHandOff() {
        val timeline = parse(sample)
        // Od začátku: programme start 09:00 − 30 s lies in the first piece (06:00 + 10770 s).
        assertEquals(10770.0, TimelineRules.startPosition(timeline, null), 0.001)
        // Pokračovat 750 s: 09:12:30 is in the second piece (09:05 at 10800).
        assertEquals(11250.0, TimelineRules.startPosition(timeline, 750.0), 0.001)
        assertNotEquals(TimelineRules.startPosition(timeline, null), TimelineRules.startPosition(timeline, 750.0), 0.001)
        // A hand-off continues at its wall clock.
        assertEquals(3600.0, TimelineRules.startPosition(timeline, 750.0, at = Instant.parse("2026-10-24T07:00:00Z")), 0.001)
    }

    @Test fun resumeSpotIsTheProgrammeAtThePlayhead() {
        val timeline = parse(sample)
        val spot = TimelineRules.resumeSpot(timeline.programmes, Instant.parse("2026-10-24T09:10:00Z"))!!
        assertEquals("dvb:bnova1-1792832400-1792834200", spot.key)
        assertEquals(Instant.parse("2026-10-24T09:00:00Z"), spot.start)
        assertEquals(1800.0, spot.lengthSeconds, 0.0)
        // Teleshopping has no id: nothing remembered there, nor in a gap.
        assertNull(TimelineRules.resumeSpot(timeline.programmes, Instant.parse("2026-10-24T09:40:00Z")))
        assertNull(TimelineRules.resumeSpot(timeline.programmes, Instant.parse("2026-10-24T13:00:00Z")))
    }

    @Test fun breaksAreMappedFromTheProgrammesPlaylistStart() {
        // Programme 09:00, its playlist starts 08:59:30: a break at 600–660 s is 09:09:30–09:10:30, in the second
        // piece (09:05 at 10800) = 11070–11130.
        val timeline = parse(sample)
        val mapped = TimelineRules.breaksInWindow(timeline.pieces, timeline.programmeStart, listOf(AdBreak(600.0, 660.0, 0.8)))
        assertEquals(listOf(AdBreak(11070.0, 11130.0, 0.8)), mapped)
        // A break inside the gap 09:00–09:05 collapses onto the next piece's start and is dropped.
        assertEquals(emptyList<AdBreak>(), TimelineRules.breaksInWindow(timeline.pieces, timeline.programmeStart, listOf(AdBreak(90.0, 200.0))))
    }

    // -- parser ------------------------------------------------------------------------------

    @Test fun sampleParses() {
        val t = parse(sample)
        assertEquals("bnova1-1792832400-1792834200", t.id)
        assertEquals("nova1", t.channel)
        assertEquals("/dvb/timeline/bnova1-1792832400-1792834200.m3u8", t.playlist)
        assertTrue(t.complete)
        assertEquals(Instant.parse("2026-10-24T09:00:00Z"), t.programmeStart)
        assertEquals(Instant.parse("2026-10-24T09:30:00Z"), t.programmeEnd)
        assertEquals(
            listOf(
                TimelinePiece(0.0, Instant.parse("2026-10-24T06:00:00Z"), 10800.0),
                TimelinePiece(10800.0, Instant.parse("2026-10-24T09:05:00Z"), 10500.5),
            ),
            t.pieces,
        )
        assertEquals(21300.5, t.duration, 0.0)
        assertEquals(listOf("Ranní show", "Zprávy", "Teleshopping", "Film: Testovací obrazec"), t.programmes.map { it.title })
        assertEquals("Ranní vydání", t.programmes[1].subtitle)
        assertTrue(t.programmes[1].breaks)
        assertFalse(t.programmes[0].breaks)
        assertNull(t.programmes[2].id)
        assertEquals("bnova1-1792810800-1792821600", t.previous)
        assertNull(t.next)
    }

    private fun mutated(edit: (JsonObject) -> Map<String, kotlinx.serialization.json.JsonElement>): String =
        JsonObject(edit(kotlinx.serialization.json.Json.parseToJsonElement(sample).jsonObject)).toString()

    private fun without(key: String) = mutated { it - key }

    private fun with(key: String, value: kotlinx.serialization.json.JsonElement) = mutated { it + (key to value) }

    @Test fun missingMapPartsAreErrors() {
        for (key in listOf("id", "playlist", "complete", "programme", "pieces")) {
            failure { parse(without(key)) }
        }
        failure { parse(with("pieces", JsonArray(emptyList()))) }
        failure { parse("<html>") }
    }

    @Test fun badPiecesAreErrors() {
        fun piece(position: String, time: String, duration: String) =
            kotlinx.serialization.json.Json.parseToJsonElement("""[{"position": $position, "time": $time, "duration": $duration}]""")
        failure { parse(with("pieces", piece("\"0\"", "\"2026-10-24T06:00:00Z\"", "10"))) }
        failure { parse(with("pieces", piece("-1", "\"2026-10-24T06:00:00Z\"", "10"))) }
        failure { parse(with("pieces", piece("0", "\"yesterday\"", "10"))) }
        failure { parse(with("pieces", piece("0", "\"2026-10-24T06:00:00Z\"", "null"))) }
        val outOfOrder = kotlinx.serialization.json.Json.parseToJsonElement(
            """[{"position": 100, "time": "2026-10-24T06:00:00Z", "duration": 10}, {"position": 0, "time": "2026-10-24T07:00:00Z", "duration": 10}]""",
        )
        failure { parse(with("pieces", outOfOrder)) }
        // The same piece with good values parses: the failures above come from the bad value alone.
        assertEquals(1, parse(with("pieces", piece("0", "\"2026-10-24T06:00:00Z\"", "10"))).pieces.size)
    }

    @Test fun foreignPlaylistAndNeighboursAreRefused() {
        failure { parse(with("playlist", kotlinx.serialization.json.JsonPrimitive("http://evil.example/x.m3u8"))) }
        failure { parse(with("playlist", kotlinx.serialization.json.JsonPrimitive("//evil.example/x.m3u8"))) }
        failure { parse(with("complete", kotlinx.serialization.json.JsonPrimitive("yes"))) }
        val t = parse(with("previous", kotlinx.serialization.json.JsonPrimitive("../../admin")))
        assertNull(t.previous)
    }

    @Test fun badProgrammesAreDroppedAndSorted() {
        val list = kotlinx.serialization.json.Json.parseToJsonElement(
            """[{"id": "bx-1792836000-1792845000", "title": "B", "start": "2026-10-24T10:00:00Z", "end": "2026-10-24T11:00:00Z"},
               {"title": "no times"}, {"title": "bad", "start": "2026-10-24T10:00:00Z", "end": "soon"},
               {"title": "zero", "start": "2026-10-24T10:00:00Z", "end": "2026-10-24T10:00:00Z"},
               {"id": 5, "title": "A", "start": "2026-10-24T09:00:00Z", "end": "2026-10-24T10:00:00Z", "breaks": "maybe"}, 7]""",
        )
        val t = parse(with("programmes", list))
        assertEquals(listOf("A", "B"), t.programmes.map { it.title })
        assertNull(t.programmes[0].id)
        assertFalse(t.programmes[0].breaks)
        assertEquals("bx-1792836000-1792845000", t.programmes[1].id)
        assertEquals(emptyList<TimelineProgramme>(), parse(without("programmes")).programmes)
    }

    @Test fun gatewayErrorsKeepTheirText() {
        val e = failure { parse("""{"error": "Nahrávka není k dispozici"}""", status = 404) }
        assertEquals("Nahrávka není k dispozici", e.userMessage)
        assertEquals("404", e.status)
    }

    // -- client and planner ------------------------------------------------------------------

    @Test fun clientFetchesTimelineWithTheKey() {
        val seen = ArrayList<Pair<String, Map<String, String>>>()
        val client = DvbClient(config) { url, headers ->
            seen += url to headers
            HttpResponse(200, sample)
        }
        val t = client.timeline("bnova1-1792832400-1792834200")
        assertEquals("http://10.0.2.2:8098/dvb/timeline/bnova1-1792832400-1792834200", seen.single().first)
        assertEquals("Bearer dev-key-dev-key-dev-key-dev-key-0000", seen.single().second["Authorization"])
        assertEquals("http://10.0.2.2:8098/dvb/timeline/bnova1-1792832400-1792834200.m3u8", client.timelineUrl(t))
    }

    private fun planner(): Planner {
        val client = DvbClient(config) { url, _ ->
            if (url.contains("/dvb/timeline/")) HttpResponse(200, sample) else throw IOException("no route $url")
        }
        return Planner(CtApi { _, _ -> throw IOException("no CT in this test") }, dvb = { client })
    }

    @Test fun plannerPlaysContinuousRecordingsInTheTimeline() {
        val p = planner()
        val id = "bnova1-1792832400-1792834200"
        val fromStart = p.plan(PlayRequest.DvbRecorded("c1", "Zprávy", id, fromStart = true, lengthSeconds = 1800.0))
        assertEquals("http://10.0.2.2:8098/dvb/timeline/$id.m3u8", fromStart.url)
        assertEquals(PlayKind.VOD, fromStart.kind)
        assertEquals(10_770_000L, fromStart.startPositionMs)
        assertEquals(id, fromStart.timeline?.id)
        // An ended programme without an offer starts like "Od začátku" (never at the live edge).
        assertEquals(10_770_000L, p.plan(PlayRequest.DvbRecorded("c1", "Zprávy", id, fromStart = false)).startPositionMs)
        val resumed = p.plan(PlayRequest.DvbRecorded("c1", "Zprávy", id, fromStart = false, resumeSeconds = 750.0))
        assertEquals(11_250_000L, resumed.startPositionMs)
        val handOff = p.plan(PlayRequest.DvbRecorded("c1", "Zprávy", id, fromStart = false), timelineAt = Instant.parse("2026-10-24T07:00:00Z"))
        assertEquals(3_600_000L, handOff.startPositionMs)
    }

    @Test fun plannerKeepsDvrRecordingsOnTheirOwnPlaylist() {
        val client = DvbClient(config) { url, _ -> throw IOException("no request expected: $url") }
        val p = Planner(CtApi { _, _ -> throw IOException("no CT") }, dvb = { client })
        // The DVR uuid starting with "b" from dvb_epg_today.json.
        val uuid = "b78bcbaa893cfa623208e2efbab5f165"
        assertTrue(Fixtures.text("dvb/dvb_epg_today.json").contains("\"$uuid\""))
        val target = p.plan(PlayRequest.DvbRecorded("c1", "x", uuid, fromStart = false))
        assertEquals("http://10.0.2.2:8098/dvb/recording/$uuid.m3u8", target.url)
        assertNull(target.timeline)
        // "Od začátku" of a growing DVR recording starts at 0 explicitly (not at the live edge).
        val fromStart = p.plan(PlayRequest.DvbRecorded("c1", "x", uuid, fromStart = true))
        assertEquals("http://10.0.2.2:8098/dvb/recording/$uuid.m3u8", fromStart.url)
        assertEquals(0L, fromStart.startPositionMs)
    }
}
