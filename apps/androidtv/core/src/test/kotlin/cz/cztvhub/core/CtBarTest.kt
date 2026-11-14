package cz.cztvhub.core

import cz.cztvhub.core.dvb.BarClock
import cz.cztvhub.core.dvb.TimelineEdge
import cz.cztvhub.core.dvb.TimelineProgramme
import cz.cztvhub.core.dvb.TimelineTrick
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.double
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Duration
import java.time.Instant
import java.time.LocalDate

/** spec/apps.md "ČT player bar (T62)": golden spec/fixtures/ct_bar_cases.json, plus timeline_cases.json `jump`. */
class CtBarTest {
    private val cases = Fixtures.json("ct_bar_cases.json")
    private val timelineCases = Fixtures.json("timeline_cases.json")

    private fun group(root: JsonObject, name: String): List<JsonObject> =
        root.getValue(name).jsonArray.map { it.jsonObject }.also { assertTrue("$name is empty", it.isNotEmpty()) }

    private fun JsonObject.text(key: String) = getValue(key).jsonPrimitive.content
    private fun JsonObject.time(key: String): Instant = Instant.parse(text(key))
    private fun JsonObject.timeOrNull(key: String): Instant? =
        getValue(key).let { if (it is JsonNull) null else Instant.parse(it.jsonPrimitive.content) }
    private fun JsonObject.num(key: String) = getValue(key).jsonPrimitive.double

    private val now = Instant.parse("2026-11-14T17:00:00Z")

    // -- golden ct_bar_cases.json ------------------------------------------------------------

    @Test fun window() {
        group(cases, "window").forEach { case ->
            val name = case.text("name")
            val caseNow = case.time("now")
            val span = when (case.text("kind")) {
                "timeshift" -> CtBarRules.timeshiftSpan(caseNow)
                "vod" -> CtBarRules.vodSpan(case.timeOrNull("programmeStart"), case.num("duration"))
                else -> error("kind ${case.text("kind")}")
            }
            assertEquals(name, case.timeOrNull("start"), span.start)
            assertEquals(name, case.timeOrNull("end"), span.end)
            assertEquals(name, case.getValue("growing").jsonPrimitive.boolean, span.growing)
            // The bar draws the same window, whenever the buffer was planned.
            if (case.text("kind") == "timeshift") {
                val frame = CtBarWindow.timeshift(caseNow.minusSeconds(3600), "Pořad")
                    .frame(0.0, BarClock(caseNow), "ČT1")
                assertEquals(name, span.start!!.toEpochMilli(), frame.startMs)
                assertEquals(name, span.end!!.toEpochMilli(), frame.endMs)
                assertEquals(name, "Teď", frame.endLabel)
            } else if (span.start != null) {
                val frame = CtBarWindow.vod(span.start, case.num("duration"), "Pořad").frame(0.0, BarClock(caseNow), "ČT1")
                assertEquals(name, span.start.toEpochMilli(), frame.startMs)
                assertEquals(name, span.end!!.toEpochMilli(), frame.endMs)
            }
        }
    }

    @Test fun clamp() {
        group(cases, "clamp").forEach { case ->
            val name = case.text("name")
            when (case.text("kind")) {
                "timeshift" -> {
                    val caseNow = case.time("now")
                    val target = case.time("target")
                    val result = case.time("result")
                    assertEquals(name, result, CtBarRules.clampTimeshift(target, caseNow))
                    // Through the window in bar seconds, planned well before now: the clamps slide with now.
                    val window = CtBarWindow.timeshift(caseNow.minus(Duration.ofMinutes(47)), "Pořad")
                    val clock = BarClock(caseNow)
                    assertEquals(name, result, window.toTime(window.clamp(window.toPosition(target), clock)))
                    val inside = target == result
                    assertEquals(name, if (inside) TimelineEdge.SEEK else TimelineEdge.CLAMP, window.edge(window.toPosition(target), clock))
                }
                "vod" -> {
                    val duration = case.num("duration")
                    val result = case.num("result")
                    assertEquals(name, result, CtBarRules.clampVod(case.num("target"), duration), 0.001)
                    val window = CtBarWindow.vod(null, 0.0, "Pořad")
                    assertEquals(name, result, window.clamp(case.num("target"), BarClock(now, duration)), 0.001)
                }
                else -> error("kind ${case.text("kind")}")
            }
        }
    }

    @Test fun labels() {
        group(cases, "labels").forEach { case ->
            val name = case.text("name")
            val position = case.num("position")
            val duration = case.num("duration")
            val edges = case.getValue("edges").jsonArray.map { it.jsonPrimitive.content }
            assertEquals(name, case.text("clock"), CtBarRules.elapsedLabel(position))
            assertEquals(name, edges, listOf(CtBarRules.elapsedEdgeLabel(0.0), CtBarRules.elapsedEdgeLabel(duration)))
            // The bar of a VOD without a known start shows exactly these.
            val frame = CtBarWindow.vod(null, duration, "Pořad").frame(position, BarClock(now, duration), "ČT1")
            assertEquals(name, case.text("clock"), frame.clock)
            assertEquals(name, edges, listOf(frame.startLabel, frame.endLabel))
        }
    }

    // -- the window in the player ------------------------------------------------------------

    @Test fun vodWithAKnownStartShowsTheWallClock() {
        val start = Instant.parse("2026-11-10T18:55:00Z")
        val frame = CtBarWindow.vod(start, 3600.0, "Film").frame(754.0, BarClock(now, 3600.0), "ČT2")
        assertEquals("út 10. 11. 20:07:34", frame.clock)
        assertEquals("19:55", frame.startLabel)
        assertEquals("20:55", frame.endLabel)
        assertEquals("Film", frame.title)
        assertEquals("ČT2", frame.subtitle)
        assertTrue("no ticks in VOD", frame.ticks.isEmpty())
        // Without a known start the same position reads as elapsed time.
        assertNotEquals(frame.clock, CtBarWindow.vod(null, 3600.0, "Film").frame(754.0, BarClock(now, 3600.0), "ČT2").clock)
    }

    @Test fun vodTakesThePlayersLengthOverTheGuides() {
        val window = CtBarWindow.vod(null, 3600.0, "Film")
        assertEquals(3590.0, window.upper(BarClock(now)), 0.001)
        assertEquals(4990.0, window.upper(BarClock(now, 5000.0)), 0.001)
        assertEquals("unknown length: no upper limit yet", 7200.0, CtBarWindow.vod(null, 0.0, "Film").clamp(7200.0, BarClock(now)), 0.001)
        assertEquals(0.0, CtBarWindow.vod(null, 0.0, "Film").clamp(-5.0, BarClock(now)), 0.001)
    }

    @Test fun theMovingPlayheadStopsAtTheClampsWhichSlideWithNow() {
        val window = CtBarWindow.timeshift(now, "Pořad") // origin = now − 3 h
        val lower = window.lower(BarClock(now))
        assertEquals(60.0, lower, 0.001)
        assertEquals(3 * 3600.0 - 10, window.upper(BarClock(now)), 0.001)
        val back = TimelineTrick()
        back.media(forward = false, atMs = 0, from = 120.0) // 60 s per second
        assertEquals(TimelineEdge.SEEK, window.movingEdge(back, 500, BarClock(now)))
        assertEquals(TimelineEdge.CLAMP, window.movingEdge(back, 1_000, BarClock(now)))
        // Five minutes later the lower clamp is five minutes on: the playhead at 120 is already below it.
        val later = BarClock(now.plus(Duration.ofMinutes(5)))
        assertEquals(360.0, window.lower(later), 0.001)
        assertEquals(TimelineEdge.CLAMP, window.movingEdge(back, 500, later))
        val forward = TimelineTrick()
        forward.media(forward = true, atMs = 0, from = 3 * 3600.0 - 70)
        assertEquals(TimelineEdge.SEEK, forward.clampEdge(500, lower, window.upper(BarClock(now))))
        assertEquals(TimelineEdge.CLAMP, window.movingEdge(forward, 1_000, BarClock(now)))
        assertEquals("the start is no edge moving forward", TimelineEdge.SEEK, forward.clampEdge(0, 1e9, 1e10))
    }

    @Test fun barSecondsStayOnTheWallClockWhileThePlayersWindowSlides() {
        // The player's window starts at 14:00 at first, at 14:10 ten minutes later; the same frame has the same time.
        val frameMs = Instant.parse("2026-11-14T16:00:00Z").toEpochMilli()
        val at1 = CtBarRules.playerWallClockMs(Instant.parse("2026-11-14T14:00:00Z").toEpochMilli(), null, 7_200_000, 0)
        val at2 = CtBarRules.playerWallClockMs(Instant.parse("2026-11-14T14:10:00Z").toEpochMilli(), null, 6_600_000, 0)
        assertEquals(frameMs, at1)
        assertEquals(frameMs, at2)
        assertEquals(6_600_000L, CtBarRules.playerPositionMs(frameMs, Instant.parse("2026-11-14T14:10:00Z").toEpochMilli(), null, 0))
        // Without program date-time: now − window length + position, and back.
        val nowMs = now.toEpochMilli()
        val estimate = CtBarRules.playerWallClockMs(null, 3 * 3_600_000L, 3_600_000, nowMs)
        assertEquals(Instant.parse("2026-11-14T15:00:00Z").toEpochMilli(), estimate)
        assertEquals(3_600_000L, CtBarRules.playerPositionMs(estimate!!, null, 3 * 3_600_000L, nowMs))
        assertNull(CtBarRules.playerWallClockMs(null, null, 0, nowMs))
        assertNull(CtBarRules.playerPositionMs(nowMs, null, null, nowMs))
    }

    private val guide = listOf(
        TimelineProgramme("1", "Zprávy", "", Instant.parse("2026-11-14T14:30:00Z"), Instant.parse("2026-11-14T15:00:00Z")),
        TimelineProgramme("2", "Film", "Díl 2", Instant.parse("2026-11-14T15:00:00Z"), Instant.parse("2026-11-14T16:45:00Z")),
        TimelineProgramme("3", "Počasí", "", Instant.parse("2026-11-14T16:45:00Z"), Instant.parse("2026-11-14T17:15:00Z")),
    )

    @Test fun timeshiftTitleTicksAndHighlightFollowTheGuide() {
        val window = CtBarWindow.timeshift(now, "Opened").copy(programmes = guide)
        val clock = BarClock(now)
        val film = window.frame(window.toPosition(Instant.parse("2026-11-14T15:30:00Z")), clock, "ČT1")
        assertEquals("Film", film.title)
        assertEquals("ČT1 · Díl 2", film.subtitle)
        assertEquals("Dnes 16:30:00", film.clock)
        assertEquals("15:00", film.startLabel)
        assertEquals(guide.map { it.start.toEpochMilli() }, film.ticks)
        assertEquals(guide[1].start.toEpochMilli() to guide[1].end.toEpochMilli(), film.highlight)
        assertEquals("before the guide: the channel", "ČT1", window.frame(window.toPosition(Instant.parse("2026-11-14T14:10:00Z")), clock, "ČT1").title)
        assertEquals("no guide yet: the programme opened", "Opened", CtBarWindow.timeshift(now, "Opened").frame(5000.0, clock, "ČT1").title)
    }

    @Test fun timeshiftJumpsOverTheGuide() {
        val window = CtBarWindow.timeshift(now, "Pořad").copy(programmes = guide)
        val clock = BarClock(now)
        fun jump(time: String, up: Boolean) =
            window.jumpTarget(window.toPosition(Instant.parse(time)), up, clock)?.let(window::toTime)
        assertEquals(guide[1].start, jump("2026-11-14T15:30:00Z", up = true))
        assertEquals("within 10 s of the start: the one before", guide[0].start, jump("2026-11-14T15:00:05Z", up = true))
        assertEquals(guide[2].start, jump("2026-11-14T15:30:00Z", up = false))
        // ▼ into the future: a target past the live edge, which the clamp then keeps 10 s before now.
        val ahead = window.copy(programmes = guide + TimelineProgramme(
            "4", "Sport", "", Instant.parse("2026-11-14T17:15:00Z"), Instant.parse("2026-11-14T18:00:00Z"),
        ))
        val future = ahead.jumpTarget(ahead.toPosition(Instant.parse("2026-11-14T16:50:00Z")), false, clock)
        assertEquals(TimelineEdge.CLAMP, window.edge(future!!, clock))
        assertEquals(now.minusSeconds(10), window.toTime(window.clamp(future, clock)))
        assertNull("nothing before the first programme", jump("2026-11-14T14:30:05Z", up = true))
    }

    @Test fun goldenJumpCasesHoldInTheBuffer() {
        val programmes = group(timelineCases, "programmes").map {
            TimelineProgramme(null, it.text("title"), "", it.time("start"), it.time("end"))
        }
        val window = CtBarWindow.timeshift(Instant.parse("2026-10-24T12:00:00Z"), "Pořad").copy(programmes = programmes)
        group(timelineCases, "jump").forEach {
            val up = it.text("direction") == "up"
            val target = window.jumpTarget(window.toPosition(it.time("time")), up, BarClock(Instant.parse("2026-10-24T12:00:00Z")))
            assertEquals(it.text("name"), it.timeOrNull("target"), target?.let(window::toTime))
        }
    }

    @Test fun vodJumpUpGoesToTheStartAndDownDoesNothing() {
        val window = CtBarWindow.vod(null, 3600.0, "Film")
        val clock = BarClock(now, 3600.0)
        assertEquals(0.0, window.jumpTarget(600.0, up = true, clock = clock)!!, 0.001)
        assertNull("within 10 s of the start", window.jumpTarget(5.0, up = true, clock = clock))
        assertNull(window.jumpTarget(600.0, up = false, clock = clock))
    }

    @Test fun resumeRemembersTheProgrammeAtThePlayhead() {
        val window = CtBarWindow.timeshift(now, "Pořad").copy(programmes = guide + TimelineProgramme(
            null, "Bez idec", "", Instant.parse("2026-11-14T17:15:00Z"), Instant.parse("2026-11-14T17:30:00Z"),
        ))
        val spot = window.resumeSpot(window.toPosition(Instant.parse("2026-11-14T15:30:00Z")))
        assertEquals(ResumeSpot("ct:2", guide[1].start, 6300.0), spot)
        assertNull("a gap", window.resumeSpot(window.toPosition(Instant.parse("2026-11-14T14:00:00Z"))))
        assertNull("no idec", window.resumeSpot(window.toPosition(Instant.parse("2026-11-14T17:20:00Z"))))
    }

    @Test fun theMarginBeforeTheOpenedProgrammeCountsAsIt() {
        // Opened "Film" (15:00) from the start: playback begins 30 s early, inside "Zprávy".
        val window = CtBarWindow.timeshift(now, "Film").copy(programmes = guide)
        val opened = ResumeSpot("ct:2", guide[1].start, 6300.0)
        val zpravy = ResumeSpot("ct:1", guide[0].start, 1800.0)
        data class Case(val name: String, val time: String, val spot: ResumeSpot?, val progress: Double?)
        val cases = listOf(
            Case("30 s before the opened start: the opened one, 0", "2026-11-14T14:59:30Z", opened, 0.0),
            Case("1 s before: still the opened one", "2026-11-14T14:59:59Z", opened, 0.0),
            Case("at the start: the opened one", "2026-11-14T15:00:00Z", opened, 0.0),
            Case("into it", "2026-11-14T15:10:00Z", opened, 600.0),
            Case("31 s before: the previous programme", "2026-11-14T14:59:29Z", zpravy, 1769.0),
            Case("moved on into the next one", "2026-11-14T16:50:00Z", ResumeSpot("ct:3", guide[2].start, 1800.0), 300.0),
        )
        cases.forEach { case ->
            val result = window.resume(window.toPosition(Instant.parse(case.time)), opened)
            assertEquals(case.name, case.spot, result?.first)
            assertEquals(case.name, case.progress, result?.second)
        }
        // Without an opened programme the margin is the previous programme's (where the entry would be cleared).
        val noOpened = window.resume(window.toPosition(Instant.parse("2026-11-14T14:59:30Z")), null)
        assertEquals(zpravy, noOpened?.first)
        assertEquals(ResumeAction.CLEAR, ResumeRules.action(noOpened!!.second, zpravy.lengthSeconds))
        assertNull("a gap", window.resume(window.toPosition(Instant.parse("2026-11-14T14:10:00Z")), opened))
    }

    @Test fun aPlayheadLeftBelowTheClampByAPauseSeeksToIt() {
        val window = CtBarWindow.timeshift(now, "Pořad") // origin = now − 3 h, lower clamp = 60
        assertNull("inside: plays on", window.resumeTarget(600.0, BarClock(now)))
        // Paused at 120 for ten minutes: the lower clamp is at 660 now.
        val later = BarClock(now.plus(Duration.ofMinutes(10)))
        assertEquals(660.0, window.resumeTarget(120.0, later)!!, 0.001)
        assertNull("VOD does not slide", CtBarWindow.vod(null, 3600.0, "Film").resumeTarget(0.0, later))
    }

    @Test fun whichPlaybacksUseTheBar() {
        val start = Instant.parse("2026-11-14T15:00:00Z")
        val programme = PlayRequest.Programme("CH_1", "Film", "123", start, start.plusSeconds(5400), true)
        val vod = CtBarWindow.of(programme, PlayKind.VOD, now)!!
        assertEquals(CtBarKind.VOD, vod.kind)
        assertTrue(vod.knownStart)
        assertEquals(5400.0, vod.length, 0.001)
        val ts = CtBarWindow.of(programme, PlayKind.TIMESHIFT, now)!!
        assertEquals(CtBarKind.TIMESHIFT, ts.kind)
        assertEquals(now.minus(Duration.ofHours(3)), ts.origin)
        // An archive episode without `aired` (ArchiveRules.request: EPOCH) has no known start.
        val noAired = programme.copy(start = Instant.EPOCH, end = Instant.EPOCH.plusSeconds(1800))
        assertFalse(CtBarWindow.of(noAired, PlayKind.VOD, now)!!.knownStart)
        assertEquals(CtBarKind.TIMESHIFT, CtBarWindow.of(PlayRequest.Startover("CH_1", "Film", start), PlayKind.TIMESHIFT, now)!!.kind)
        assertNull(CtBarWindow.of(PlayRequest.Live("CH_1", "ČT1"), PlayKind.LIVE, now))
        assertNull(CtBarWindow.of(PlayRequest.DvbRecorded("nova", "X", "b123", false), PlayKind.VOD, now))
        assertTrue(CtBarWindow.usesBar(programme))
        assertTrue(CtBarWindow.usesBar(PlayRequest.Startover("CH_1", "Film", start)))
        listOf(
            PlayRequest.Live("CH_1", "ČT1"), PlayRequest.DvbLive("nova", "Nova"), PlayRequest.PspLive("stream1", "PSP"),
            PlayRequest.DvbRecorded("nova", "X", "0123abcd", false),
        ).forEach { assertFalse(it.toString(), CtBarWindow.usesBar(it)) }
    }

    @Test fun guideCoversTheBufferAcrossBroadcastDays() {
        // 06:30 Prague: the buffer reaches back to 03:30, still the previous broadcast day.
        assertEquals(
            listOf(LocalDate.parse("2026-11-13"), LocalDate.parse("2026-11-14")),
            CtBarRules.guideDays(Instant.parse("2026-11-14T05:30:00Z")),
        )
        assertEquals(listOf(LocalDate.parse("2026-11-14")), CtBarRules.guideDays(now))
        val list = listOf(
            Programme("a", "Old", Instant.parse("2026-11-14T12:00:00Z"), Instant.parse("2026-11-14T13:59:00Z"), true),
            Programme("c", "Later", Instant.parse("2026-11-14T16:00:00Z"), Instant.parse("2026-11-14T18:00:00Z"), true, episodeTitle = "Ep"),
            Programme("b", "Edge", Instant.parse("2026-11-14T13:30:00Z"), Instant.parse("2026-11-14T14:30:00Z"), true),
            Programme("b", "Edge", Instant.parse("2026-11-14T13:30:00Z"), Instant.parse("2026-11-14T14:30:00Z"), true),
            Programme("", "Next", Instant.parse("2026-11-14T18:00:00Z"), Instant.parse("2026-11-14T19:00:00Z"), true),
        )
        val bar = CtBarRules.guideProgrammes(list, Instant.parse("2026-11-14T14:00:00Z"), now)
        assertEquals(listOf("Edge", "Later"), bar.map { it.title })
        assertEquals(listOf("b", "c"), bar.map { it.id })
        assertEquals("Ep", bar[1].subtitle)
        assertNull(CtBarRules.guideProgrammes(list, now, Instant.parse("2026-11-14T18:30:00Z")).last().id)
    }
}
