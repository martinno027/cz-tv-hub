package cz.cztvhub.core.dvb

import cz.cztvhub.core.Days
import cz.cztvhub.core.ResumeRules
import cz.cztvhub.core.ResumeSpot
import java.time.Duration
import java.time.Instant
import java.time.format.DateTimeFormatter

/** A recorded stretch of a timeline window: [position] and [duration] in playlist seconds, [time] its first frame. */
data class TimelinePiece(val position: Double, val time: Instant, val duration: Double) {
    val endPosition: Double get() = position + duration
    val endTime: Instant get() = time.plusMillis(Math.round(duration * 1000))
}

/** An EPG programme overlapping the window; [id] is a playable `b…` id or null, [breaks] = ad breaks stored. */
data class TimelineProgramme(
    val id: String?,
    val title: String,
    val subtitle: String,
    val start: Instant,
    val end: Instant,
    val breaks: Boolean = false,
)

/**
 * `GET /dvb/timeline/<id>` (spec/dvb-gateway.md "Channel timeline"): a window of a channel's continuous recording
 * around one programme. [pieces] are never empty and in playlist order; [programmes] sorted by start.
 */
data class DvbTimeline(
    val id: String,
    val channel: String,
    /** Gateway path of the playlist, always starting with a single `/`. */
    val playlist: String,
    /** False while the window still grows (`EVENT` playlist, re-fetch every [TimelineRules.REFRESH]). */
    val complete: Boolean,
    val programmeStart: Instant,
    val programmeEnd: Instant,
    val pieces: List<TimelinePiece>,
    val programmes: List<TimelineProgramme>,
    val previous: String?,
    val next: String?,
) {
    val duration: Double get() = pieces.last().endPosition
}

/** What a seek target does at the window's edges (`edge` in spec/fixtures/timeline_cases.json). */
enum class TimelineEdge { SEEK, PREVIOUS, NEXT, CLAMP }

/** Pure rules of the Anténa timeline player (spec/apps.md "Anténa timeline player"); golden timeline_cases.json. */
object TimelineRules {
    /** "Od začátku" starts this long before the programme (as the gateway's EXT-X-START). */
    val START_MARGIN: Duration = Duration.ofSeconds(30)
    val REFRESH: Duration = Duration.ofSeconds(60)

    /** A ←/→ press released within this is a step, longer is a scrub. */
    const val HOLD_MIN_SECONDS = 0.5

    /** ⏪/⏩ scrub like held arrows but count at least this long (a tap = 60 s). */
    const val MEDIA_MIN_HELD_SECONDS = 1.0

    /** Without a key-up event, the release counts this long after the last repeat. */
    const val RELEASE_TIMEOUT_MS = 800L
    const val STEP_SECONDS = 10.0
    const val HOLD_SLOW_PER_SECOND = 60.0
    const val HOLD_FAST_AFTER_SECONDS = 3.0
    const val HOLD_FAST_PER_SECOND = 600.0

    /** ▲ more than this into a programme goes to its start, else to the previous one. */
    val JUMP_BACK_GRACE: Duration = Duration.ofSeconds(10)

    /** A ▲ target mapping this close to the playhead (a start inside a recording gap) takes the programme before. */
    const val JUMP_MIN_MOVE_SECONDS = 10.0

    /** A growing window's right end reads "Teď" while its last piece ends this close to now. */
    val NOW_TOLERANCE: Duration = Duration.ofSeconds(30)

    /** Continuous-recording ids (`b<channel>-<start>-<end>`, as the gateway's RECORDING_ID_RE); DVR uuids are not. */
    private val TIMELINE_ID = Regex("^b[A-Za-z0-9_]{1,40}-\\d{9,11}-\\d{9,11}$")

    fun isTimelineId(id: String): Boolean = TIMELINE_ID.matches(id)

    /**
     * Wall clock of [position]: a boundary belongs to the later piece; outside the window clamps, except past the
     * end of a growing window ([growing]) where the time runs on with the position.
     */
    fun toTime(pieces: List<TimelinePiece>, position: Double, growing: Boolean = false): Instant {
        val first = pieces.first()
        if (position <= first.position) return first.time
        val piece = pieces.last { it.position <= position }
        val offset = if (growing && piece === pieces.last()) position - piece.position else minOf(position - piece.position, piece.duration)
        return piece.time.plusMillis(Math.round(offset * 1000))
    }

    /** Playlist position of [time]: in a gap the next piece's start, outside the window clamps. */
    fun toPosition(pieces: List<TimelinePiece>, time: Instant): Double {
        for (piece in pieces) {
            if (time.isBefore(piece.time)) return piece.position
            if (time.isBefore(piece.endTime)) return piece.position + seconds(piece.time, time)
        }
        return pieces.last().endPosition
    }

    /**
     * The wall clock a seek [target] aims at, also outside the window (before it counted back from the first
     * piece, after it on from the last one): where a neighbouring window continues.
     */
    fun targetTime(pieces: List<TimelinePiece>, target: Double): Instant {
        val first = pieces.first()
        val last = pieces.last()
        return when {
            target < first.position -> first.time.plusMillis(Math.round((target - first.position) * 1000))
            target > last.endPosition -> last.endTime.plusMillis(Math.round((target - last.endPosition) * 1000))
            else -> toTime(pieces, target)
        }
    }

    /**
     * Seek target of a wall clock, also outside the window (before it negative, after it past the end), so that
     * the [edge] rule applies to it; inside the window the map ([toPosition]).
     */
    fun targetPosition(pieces: List<TimelinePiece>, time: Instant): Double {
        val first = pieces.first()
        val last = pieces.last()
        return when {
            time.isBefore(first.time) -> first.position + seconds(first.time, time)
            time.isAfter(last.endTime) -> last.endPosition + seconds(last.endTime, time)
            else -> toPosition(pieces, time)
        }
    }

    /**
     * ▲ / ▼ from the playhead at [position]: the [jump] target through the map ([targetPosition], may lie outside
     * the window); when a ▲ target maps within 10 s of the playhead the programme before is taken. Null = none.
     */
    fun jumpTarget(timeline: DvbTimeline, position: Double, up: Boolean): Double? {
        val pieces = timeline.pieces
        val time = toTime(pieces, position, growing = !timeline.complete)
        var to = jump(timeline.programmes, time, up) ?: return null
        var target = targetPosition(pieces, to)
        while (up && Math.abs(target - position) <= JUMP_MIN_MOVE_SECONDS) {
            to = timeline.programmes.lastOrNull { it.start.isBefore(to) }?.start ?: return null
            target = targetPosition(pieces, to)
        }
        return target
    }

    fun edge(target: Double, duration: Double, complete: Boolean, hasPrevious: Boolean, hasNext: Boolean): TimelineEdge =
        when {
            target < 0 -> if (hasPrevious) TimelineEdge.PREVIOUS else TimelineEdge.CLAMP
            target >= duration -> if (complete && hasNext) TimelineEdge.NEXT else TimelineEdge.CLAMP
            else -> TimelineEdge.SEEK
        }

    /** Seek offset (seconds) of ←/→ held for [heldSeconds]: a press is 10 s, then 60 × s up to 3 s, then +600 × s. */
    fun holdOffset(heldSeconds: Double): Double = when {
        heldSeconds < HOLD_MIN_SECONDS -> STEP_SECONDS
        heldSeconds <= HOLD_FAST_AFTER_SECONDS -> HOLD_SLOW_PER_SECOND * heldSeconds
        else -> HOLD_SLOW_PER_SECOND * HOLD_FAST_AFTER_SECONDS + HOLD_FAST_PER_SECOND * (heldSeconds - HOLD_FAST_AFTER_SECONDS)
    }

    private val CLOCK: DateTimeFormatter = DateTimeFormatter.ofPattern("H:mm:ss").withZone(Days.ZONE)
    private val SHORT_CLOCK: DateTimeFormatter = DateTimeFormatter.ofPattern("H:mm").withZone(Days.ZONE)

    /** The playhead label: `Dnes 21:14:30`, `Včera 23:59:59`, `čt 22. 10. 10:05:00` (calendar days in Prague). */
    fun label(time: Instant, now: Instant): String =
        "${Days.label(Days.today(time), Days.today(now))} ${CLOCK.format(time)}"

    /** A bar end's label `H:MM`, with the day in front only when that day is not the [playhead]'s. */
    fun edgeLabel(time: Instant, playhead: Instant, now: Instant): String {
        val clock = SHORT_CLOCK.format(time)
        val day = Days.today(time)
        return if (day == Days.today(playhead)) clock else "${Days.label(day, Days.today(now))} $clock"
    }

    /** The right end of the bar: "Teď" for a growing window whose recording reaches (about) now. */
    fun endLabel(end: Instant, complete: Boolean, playhead: Instant, now: Instant): String =
        if (!complete && !end.isBefore(now.minus(NOW_TOLERANCE))) "Teď" else edgeLabel(end, playhead, now)

    /** Index of the programme at [time] (a boundary belongs to the later one), null in a gap. */
    fun programmeAt(programmes: List<TimelineProgramme>, time: Instant): Int? =
        programmes.indexOfFirst { !time.isBefore(it.start) && time.isBefore(it.end) }.takeIf { it >= 0 }

    /** ▲ ([up]) = this programme's start, or the previous one's within 10 s of it; ▼ = the next one's; null = none. */
    fun jump(programmes: List<TimelineProgramme>, time: Instant, up: Boolean): Instant? {
        if (!up) return programmes.firstOrNull { it.start.isAfter(time) }?.start
        val current = programmeAt(programmes, time)?.let { programmes[it] }
        if (current != null && Duration.between(current.start, time) > JUMP_BACK_GRACE) return current.start
        val before = current?.start ?: time
        return programmes.lastOrNull { it.start.isBefore(before) }?.start
    }

    /** "Pokračovat" progress of a programme starting at [start] for a playhead at [time]; before the start = 0. */
    fun resumeProgress(start: Instant, time: Instant): Double = maxOf(0.0, seconds(start, time))

    /** The programme remembered for "Pokračovat" at a playhead at [time]: the one there, when it has an id. */
    fun resumeSpot(programmes: List<TimelineProgramme>, time: Instant): ResumeSpot? {
        val programme = programmeAt(programmes, time)?.let { programmes[it] } ?: return null
        val id = programme.id ?: return null
        return ResumeSpot(ResumeRules.dvbKey(id), programme.start, seconds(programme.start, programme.end))
    }

    /**
     * Where the window opens: [resumeSeconds] after the programme's start ("Pokračovat"), else 30 s before it
     * ("Od začátku", also an ended programme without an offer); [at] (a hand-off or a re-open) wins over both.
     */
    fun startPosition(timeline: DvbTimeline, resumeSeconds: Double?, at: Instant? = null): Double {
        val time = at
            ?: resumeSeconds?.let { timeline.programmeStart.plusMillis(Math.round(it * 1000)) }
            ?: timeline.programmeStart.minus(START_MARGIN)
        return toPosition(timeline.pieces, time)
    }

    /**
     * The programme at [time] whose ad breaks apply: it has an id, stored breaks and has ended (also inside a
     * growing window); else null.
     */
    fun breaksProgramme(programmes: List<TimelineProgramme>, time: Instant, now: Instant): TimelineProgramme? {
        val programme = programmeAt(programmes, time)?.let { programmes[it] } ?: return null
        return programme.takeIf { it.id != null && it.breaks && !it.end.isAfter(now) }
    }

    /**
     * Ad breaks of a programme (positions from its playlist start = programme start − 30 s) as positions of this
     * window: to wall clock, then through the map. Breaks wholly outside the recorded window are dropped.
     */
    fun breaksInWindow(pieces: List<TimelinePiece>, programmeStart: Instant, breaks: List<AdBreak>): List<AdBreak> {
        val origin = programmeStart.minus(START_MARGIN)
        return breaks.mapNotNull {
            val start = toPosition(pieces, origin.plusMillis(Math.round(it.start * 1000)))
            val end = toPosition(pieces, origin.plusMillis(Math.round(it.end * 1000)))
            if (end > start) AdBreak(start, end, it.confidence) else null
        }
    }

    private fun seconds(from: Instant, to: Instant): Double = Duration.between(from, to).toMillis() / 1000.0
}

/**
 * A ←/→ press being chosen from [from] (playlist seconds): the playhead follows [target] while the key is held,
 * the video seeks once on release. Times are the key events' clock in milliseconds.
 */
class TimelineScrub(
    val from: Double,
    val forward: Boolean,
    private val downAtMs: Long,
    /** ⏪/⏩: [TimelineRules.MEDIA_MIN_HELD_SECONDS]; arrows 0. */
    private val minHeldSeconds: Double = 0.0,
) {
    fun target(atMs: Long): Double {
        val offset = TimelineRules.holdOffset(maxOf(minHeldSeconds, (atMs - downAtMs) / 1000.0))
        return if (forward) from + offset else from - offset
    }
}
