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

    /** A ←/→ press released within this is a click (±10 s); held this long it starts the movement (T55). */
    const val HOLD_MIN_SECONDS = 0.5
    const val STEP_SECONDS = 10.0

    /** Without a key-up event, the press ends this long after its last repeat. */
    const val RELEASE_TIMEOUT_MS = 800L

    /** Rewind / fast-forward (`trick`): speed 1…[TRICK_MAX_SPEED], [TRICK_SECONDS_PER_SPEED] s of programme per second each. */
    const val TRICK_MAX_SPEED = 4
    const val TRICK_SECONDS_PER_SPEED = 60.0

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

    /** A ←/→ press held [heldSeconds] is a hold (starts the movement); shorter is a click, [STEP_SECONDS]. */
    fun isHold(heldSeconds: Double): Boolean = heldSeconds >= HOLD_MIN_SECONDS

    /** Seconds of programme per second at [speed] (`trick` speeds: 1 = 60 … 4 = 240); 0 = stopped. */
    fun trickRate(speed: Int): Double = TRICK_SECONDS_PER_SPEED * speed.coerceIn(0, TRICK_MAX_SPEED)

    /** The bar's sign while moving: `◀◀` / `▶▶`, from speed 2 with the number (`◀◀ 2`). */
    fun trickBadge(forward: Boolean, speed: Int): String {
        val sign = if (forward) "▶▶" else "◀◀"
        return if (speed >= 2) "$sign $speed" else sign
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

/** What a key did to the rewind / fast-forward (`trick`), for the player to act on. */
sealed class TrickAction {
    /** Nothing to do (yet): a press being timed, or the rest of a press already used. */
    object None : TrickAction()

    /** A click while stopped: seek exactly by [seconds] (−10 / +10). */
    data class Step(val seconds: Double) : TrickAction()

    /** The movement began: pause the video, move the playhead every frame. */
    object Start : TrickAction()

    /** Speed or direction changed while moving. */
    object Change : TrickAction()
}

/** How the movement stopped: play from the playhead, or back to where it began. */
enum class TrickStop { PLAY_HERE, RETURN }

/**
 * Rewind / fast-forward of the timeline player (spec/apps.md "Keys" ← / →, T55; golden `trick`). While stopped a
 * ←/→ press is timed: released under [TimelineRules.HOLD_MIN_SECONDS] it is a click (±10 s), held that long it
 * starts moving at speed 1 (already while held; the release adds nothing). While moving every press is a click at
 * its down: the same side = speed + 1 (at most 4), the other side = speed − 1, from 1 it turns round. ⏪/⏩ act on
 * their down the same way (stopped: start). Positions are playlist seconds of the current window, times the key
 * events' clock in milliseconds.
 *
 * Remotes without a key-up: their repeats lasting ≥ 0.5 s are a hold; the press ends [TimelineRules.RELEASE_TIMEOUT_MS]
 * after the last repeat ([released]), counted at that last repeat (the wait adds nothing).
 */
class TimelineTrick {
    private class Press(val forward: Boolean, val downAtMs: Long, val used: Boolean, val clickSteps: Boolean = true) {
        var lastEventMs = downAtMs
    }

    private var press: Press? = null

    /** -1 back, 1 forward, 0 stopped. */
    var direction = 0
        private set

    /** 1…4 while moving, 0 stopped. */
    var speed = 0
        private set

    /** Where the movement began (the Back target); meaningful in the window it began in. */
    var origin = 0.0
        private set

    /** Held still at its position while a neighbouring window opens ([freeze] … [rebase]). */
    var frozen = false
        private set

    private var anchor = 0.0
    private var anchorMs = 0L

    val moving: Boolean get() = speed > 0

    /** Seconds of programme per second, signed. */
    val rate: Double get() = direction * TimelineRules.trickRate(speed)

    /** The bar's sign, null while stopped. */
    val badge: String? get() = if (moving) TimelineRules.trickBadge(direction > 0, speed) else null

    /** The playhead at [atMs] while moving (else the last anchor). */
    fun position(atMs: Long): Double =
        if (!moving || frozen) anchor else anchor + rate * (atMs - anchorMs) / 1000.0

    /**
     * The first down of a ←/→ press at [atMs]; [from] = the playhead shown now. [barShown] false: this press only
     * shows the bar when it is a click, a hold still starts the movement (T55).
     */
    fun down(forward: Boolean, atMs: Long, from: Double, barShown: Boolean = true): TrickAction {
        if (moving) {
            press = Press(forward, atMs, used = true)
            change(forward, atMs)
            return TrickAction.Change
        }
        press = Press(forward, atMs, used = false, clickSteps = barShown)
        anchor = from
        return TrickAction.None
    }

    /** A repeat of the press at [atMs]: lasting ≥ 0.5 s it starts the movement. */
    fun repeat(atMs: Long): TrickAction {
        val p = press ?: return TrickAction.None
        if (atMs > p.lastEventMs) p.lastEventMs = atMs
        if (p.used || moving) return TrickAction.None
        if (!TimelineRules.isHold((atMs - p.downAtMs) / 1000.0)) return TrickAction.None
        start(p.forward, anchor, atMs)
        return TrickAction.Start
    }

    /** The key-up of the press at [atMs]: a click steps, a hold not seen as one yet starts now. */
    fun up(atMs: Long): TrickAction {
        val p = press ?: return TrickAction.None
        press = null
        if (p.used || moving) return TrickAction.None
        if (TimelineRules.isHold((atMs - p.downAtMs) / 1000.0)) {
            start(p.forward, anchor, atMs)
            return TrickAction.Start
        }
        if (!p.clickSteps) return TrickAction.None
        return TrickAction.Step(if (p.forward) TimelineRules.STEP_SECONDS else -TimelineRules.STEP_SECONDS)
    }

    /** When a press without a key-up counts as released (null = no press open). */
    fun releaseAtMs(): Long? = press?.let { it.lastEventMs + TimelineRules.RELEASE_TIMEOUT_MS }

    /** No key-up came: the press ends at its last repeat. */
    fun released(): TrickAction {
        val p = press ?: return TrickAction.None
        return up(p.lastEventMs)
    }

    /** ⏪ / ⏩ pressed at [atMs]: stopped = start (speed 1), moving = as a click. */
    fun media(forward: Boolean, atMs: Long, from: Double): TrickAction {
        press = null
        if (moving) {
            change(forward, atMs)
            return TrickAction.Change
        }
        start(forward, from, atMs)
        return TrickAction.Start
    }

    /** Stops the movement at [atMs]: [TrickStop.PLAY_HERE] at the playhead, [TrickStop.RETURN] at [origin]. */
    fun stop(atMs: Long, how: TrickStop = TrickStop.PLAY_HERE): Double {
        val at = if (how == TrickStop.RETURN) origin else position(atMs)
        reset()
        anchor = at
        return at
    }

    /** Back to stopped, nothing pending (the player closed or changed programme). */
    fun reset() {
        press = null
        direction = 0
        speed = 0
        frozen = false
    }

    /** A neighbouring window opens: the playhead waits at [position] until [rebase]. */
    fun freeze(position: Double) {
        anchor = position
        frozen = true
    }

    /** The movement goes on from [position] (of the new window) at [atMs], same speed and direction. */
    fun rebase(position: Double, atMs: Long) {
        anchor = position
        anchorMs = atMs
        frozen = false
    }

    /**
     * What the moving playhead meets at [atMs] (`edge` rule): [TimelineEdge.SEEK] = nothing, keep moving;
     * [TimelineEdge.PREVIOUS] / [TimelineEdge.NEXT] = continue in that window; [TimelineEdge.CLAMP] = stop there
     * and play (no neighbour, or a growing window's live edge [liveEnd]).
     */
    fun edge(atMs: Long, duration: Double, complete: Boolean, hasPrevious: Boolean, hasNext: Boolean, liveEnd: Double): TimelineEdge {
        if (!moving || frozen) return TimelineEdge.SEEK
        val p = position(atMs)
        return when {
            direction < 0 && p <= 0 -> if (hasPrevious) TimelineEdge.PREVIOUS else TimelineEdge.CLAMP
            direction > 0 && complete && p >= duration -> if (hasNext) TimelineEdge.NEXT else TimelineEdge.CLAMP
            direction > 0 && !complete && p >= liveEnd -> TimelineEdge.CLAMP
            else -> TimelineEdge.SEEK
        }
    }

    private fun start(forward: Boolean, from: Double, atMs: Long) {
        origin = from
        anchor = from
        anchorMs = atMs
        direction = if (forward) 1 else -1
        speed = 1
        frozen = false
    }

    private fun change(forward: Boolean, atMs: Long) {
        anchor = position(atMs)
        anchorMs = atMs
        when {
            forward == (direction > 0) -> speed = minOf(TimelineRules.TRICK_MAX_SPEED, speed + 1)
            speed > 1 -> speed -= 1
            else -> direction = -direction
        }
    }
}

/**
 * After a chosen seek the bar keeps showing its target until the player is there (T55: no jump back to the old
 * position, e.g. while the previous / next window opens). Positions are playlist seconds, times milliseconds.
 */
class TimelineShownSeek {
    private var target: Double? = null
    private var sinceMs = 0L

    /** The seek just chosen: [target] as the player was asked to go there (clamped), or beyond the edge on a switch. */
    fun seek(target: Double, atMs: Long) {
        this.target = target
        sinceMs = atMs
    }

    fun clear() {
        target = null
    }

    /** What the bar shows for the player at [playerPosition]: the target until the player is within reach of it. */
    fun position(playerPosition: Double, atMs: Long, switching: Boolean = false): Double {
        val t = target ?: return playerPosition
        val arrived = !switching && kotlin.math.abs(playerPosition - t) <= ARRIVED_SECONDS
        if (arrived || atMs - sinceMs > MAX_MS) {
            target = null
            return playerPosition
        }
        return t
    }

    companion object {
        /** Within this of the target the player is there (a keyframe seek lands up to a segment, ~2.6 s, away). */
        const val ARRIVED_SECONDS = 3.0

        /** A seek the player never finished (or a window that did not open) stops being shown after this. */
        const val MAX_MS = 10_000L
    }
}
