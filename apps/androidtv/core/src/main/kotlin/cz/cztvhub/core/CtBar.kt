package cz.cztvhub.core

import cz.cztvhub.core.dvb.BarClock
import cz.cztvhub.core.dvb.BarFrame
import cz.cztvhub.core.dvb.BarWindow
import cz.cztvhub.core.dvb.TimelineEdge
import cz.cztvhub.core.dvb.TimelineProgramme
import cz.cztvhub.core.dvb.TimelineRules
import cz.cztvhub.core.dvb.TimelineTrick
import java.time.Duration
import java.time.Instant
import java.time.LocalDate

/**
 * Pure rules of the ČT player bar (spec/apps.md "ČT player bar (T62)"); golden spec/fixtures/ct_bar_cases.json
 * (`window`, `clamp`, `labels`).
 */
object CtBarRules {
    /** The buffer: wall clock `now − 3 h` … `now`. */
    val WINDOW: Duration = Decisions.TIMESHIFT_WINDOW

    /** The oldest minute of the buffer may already be gone from the CDN: seeks stay this far inside its start. */
    val OLDEST_SKIPPED: Duration = Duration.ofSeconds(60)

    /** Seeks stay this far before the buffer's live edge (the existing live-edge guard) and the VOD's end. */
    val END_GUARD: Duration = Duration.ofSeconds(10)
    private val END_GUARD_SECONDS = END_GUARD.seconds.toDouble()

    /** The window on the wall clock: [start] / [end] null for a VOD without a known start. */
    data class Span(val start: Instant?, val end: Instant?, val growing: Boolean)

    fun timeshiftSpan(now: Instant): Span = Span(now.minus(WINDOW), now, growing = true)

    fun vodSpan(start: Instant?, durationSeconds: Double): Span =
        Span(start, start?.plusMillis(Math.round(durationSeconds * 1000)), growing = false)

    /**
     * A timeshift seek target (wall clock) inside `[now − 3 h + 60 s, now − 10 s]`; the lower end slides with now.
     * [now] = the end of the stream where the player knows it (ČT's stream lags the real clock by minutes).
     */
    fun clampTimeshift(target: Instant, now: Instant): Instant {
        val lower = now.minus(WINDOW).plus(OLDEST_SKIPPED)
        val upper = now.minus(END_GUARD)
        return when {
            target.isBefore(lower) -> lower
            target.isAfter(upper) -> upper
            else -> target
        }
    }

    /** A VOD seek target (position, s) inside `[0, duration − 10]`. */
    fun clampVod(target: Double, durationSeconds: Double): Double =
        target.coerceIn(0.0, maxOf(0.0, durationSeconds - END_GUARD_SECONDS))

    /** The playhead's label of a VOD without a known start: the elapsed position `H:MM:SS` (`0:12:34`). */
    fun elapsedLabel(positionSeconds: Double): String {
        val total = maxOf(0L, positionSeconds.toLong())
        return "${total / 3600}:${two(total % 3600 / 60)}:${two(total % 60)}"
    }

    /** A bar end of a VOD without a known start: `H:MM`, whole minutes floored (`0:00`, `1:27`). */
    fun elapsedEdgeLabel(seconds: Double): String {
        val minutes = maxOf(0L, seconds.toLong()) / 60
        return "${minutes / 60}:${two(minutes % 60)}"
    }

    /**
     * The wall clock of the frame the player shows: the playlist's program date-time ([windowStartMs]) + position,
     * else the estimate `now − window length + position` (the one "Pokračovat" uses). Null = unknown.
     */
    fun playerWallClockMs(windowStartMs: Long?, windowDurationMs: Long?, positionMs: Long, nowMs: Long): Long? = when {
        windowStartMs != null -> windowStartMs + positionMs
        windowDurationMs != null -> nowMs - windowDurationMs + positionMs
        else -> null
    }

    /**
     * The wall clock of the stream's end (the seekable range's end): program date-time + window duration; null without
     * both (the estimate `now − duration + position` puts the end at the real clock anyway).
     */
    fun streamEndMs(windowStartMs: Long?, windowDurationMs: Long?): Long? =
        if (windowStartMs != null && windowDurationMs != null) windowStartMs + windowDurationMs else null

    /** The player position of [wallClockMs], the inverse of [playerWallClockMs]. Null = unknown. */
    fun playerPositionMs(wallClockMs: Long, windowStartMs: Long?, windowDurationMs: Long?, nowMs: Long): Long? = when {
        windowStartMs != null -> wallClockMs - windowStartMs
        windowDurationMs != null -> wallClockMs - (nowMs - windowDurationMs)
        else -> null
    }

    /** The broadcast days whose guide covers the buffer at [now] (oldest first). */
    fun guideDays(now: Instant): List<LocalDate> =
        listOf(Days.openDay(now.minus(WINDOW)), Days.openDay(now)).distinct()

    /** The guide's programmes overlapping `[from, to)` as bar programmes (id = idec), sorted by start. */
    fun guideProgrammes(guide: List<Programme>, from: Instant, to: Instant): List<TimelineProgramme> = guide
        .filter { it.end.isAfter(from) && it.start.isBefore(to) }
        .distinctBy { it.idec to it.start }
        .sortedBy { it.start }
        .map { TimelineProgramme(it.idec.ifBlank { null }, it.title, it.episodeTitle, it.start, it.end) }

    private fun two(n: Long) = n.toString().padStart(2, '0')
}

enum class CtBarKind { TIMESHIFT, VOD }

/**
 * The window of a ČT start-over / "Zpět v čase" or archive playback as a [BarWindow] (T62): one window, no
 * neighbours, no ad breaks. Bar seconds count from [origin]: for the buffer a fixed wall clock (the buffer's start
 * when it was planned), so the playhead and the rewind stay on the wall clock while the player's positions slide
 * with the buffer; for VOD the programme's start (position = bar seconds), [Instant.EPOCH] when unknown.
 */
data class CtBarWindow(
    val kind: CtBarKind,
    val origin: Instant,
    /** VOD: the programme's start is known (labels on the wall clock, else the elapsed position). */
    val knownStart: Boolean,
    /** The programme played (VOD), or shown until the guide is known (buffer). */
    val title: String,
    /** VOD: the programme's length (s) until the player knows the stream's. */
    val length: Double = 0.0,
    /** The buffer: the channel's guide over the window (ticks, the title at the playhead, ▲/▼, "Pokračovat"). */
    val programmes: List<TimelineProgramme> = emptyList(),
) : BarWindow {

    fun toTime(position: Double): Instant = origin.plusMillis(Math.round(position * 1000))

    fun toPosition(time: Instant): Double = Duration.between(origin, time).toMillis() / 1000.0

    /** VOD: the stream's length, from the player when it knows it; null unknown. */
    fun duration(clock: BarClock): Double? =
        (clock.knownDuration?.takeIf { it > 0 } ?: length.takeIf { it > 0 })

    /** The lowest bar position a seek or the moving playhead reaches (the buffer: slides with now). */
    fun lower(clock: BarClock): Double = when (kind) {
        CtBarKind.TIMESHIFT -> toPosition(CtBarRules.clampTimeshift(Instant.MIN, clock.liveEnd))
        CtBarKind.VOD -> 0.0
    }

    /** The highest: 10 s before the live edge / the VOD's end (unknown length: no limit yet). */
    fun upper(clock: BarClock): Double = when (kind) {
        CtBarKind.TIMESHIFT -> toPosition(CtBarRules.clampTimeshift(Instant.MAX, clock.liveEnd))
        CtBarKind.VOD -> duration(clock)?.let { CtBarRules.clampVod(Double.MAX_VALUE, it) } ?: Double.MAX_VALUE
    }

    override fun edge(target: Double, clock: BarClock): TimelineEdge =
        if (target >= lower(clock) && target <= upper(clock)) TimelineEdge.SEEK else TimelineEdge.CLAMP

    override fun clamp(target: Double, clock: BarClock): Double = when (kind) {
        CtBarKind.TIMESHIFT -> toPosition(CtBarRules.clampTimeshift(toTime(target), clock.liveEnd))
        CtBarKind.VOD -> duration(clock)?.let { CtBarRules.clampVod(target, it) } ?: maxOf(0.0, target)
    }

    override fun movingEdge(trick: TimelineTrick, atMs: Long, clock: BarClock): TimelineEdge =
        trick.clampEdge(atMs, lower(clock), upper(clock))

    /**
     * ▲ / ▼ over the guide (buffer, `jump`; a ▲ target within 10 s of the playhead takes the programme before); VOD:
     * ▲ = the programme's start (more than 10 s in), ▼ nothing.
     */
    override fun jumpTarget(position: Double, up: Boolean, clock: BarClock): Double? {
        if (kind == CtBarKind.VOD) {
            return if (up && position > TimelineRules.JUMP_BACK_GRACE.seconds) 0.0 else null
        }
        var to = TimelineRules.jump(programmes, toTime(position), up) ?: return null
        var target = toPosition(to)
        while (up && Math.abs(target - position) <= TimelineRules.JUMP_MIN_MOVE_SECONDS) {
            to = programmes.lastOrNull { it.start.isBefore(to) }?.start ?: return null
            target = toPosition(to)
        }
        return target
    }

    /** The programme at [position] in the buffer's guide; null in a gap, for VOD or without a guide. */
    fun programmeAt(position: Double): TimelineProgramme? =
        TimelineRules.programmeAt(programmes, toTime(position))?.let { programmes[it] }

    /**
     * "Pokračovat" in the buffer: the programme at the playhead with an idec (its `ct:` key, start and length);
     * null in a gap or for a programme without one. Only meaningful with a guide ([programmes] not empty).
     */
    fun resumeSpot(position: Double): ResumeSpot? {
        val programme = programmeAt(position) ?: return null
        return spotOf(programme)
    }

    /**
     * What "Pokračovat" saves for a playhead at [position] in the buffer: the spot and its progress (wall clock −
     * programme start). While the playhead is in the 30 s margin before the [opened] programme's start it counts as
     * that one with progress 0, so starting a programme does not clear the previous one's entry. Null = nothing
     * (a gap, a programme without an idec).
     */
    fun resume(position: Double, opened: ResumeSpot?): Pair<ResumeSpot, Double>? {
        val time = toTime(position)
        val openedStart = opened?.start
        if (openedStart != null && time.isBefore(openedStart) &&
            !time.isBefore(openedStart.minus(Decisions.START_MARGIN))
        ) {
            return opened to 0.0
        }
        val spot = resumeSpot(position) ?: return null
        val start = spot.start ?: return null
        return spot to maxOf(0.0, Duration.between(start, time).toMillis() / 1000.0)
    }

    /**
     * Resuming playback (the end of a pause) at [position]: the lower clamp when the buffer's start slid past the
     * paused playhead, else null (play on where it is). Only the buffer slides.
     */
    fun resumeTarget(position: Double, clock: BarClock): Double? {
        if (kind != CtBarKind.TIMESHIFT) return null
        val lower = lower(clock)
        return if (position < lower) lower else null
    }

    private fun spotOf(programme: TimelineProgramme): ResumeSpot? {
        val id = programme.id ?: return null
        val length = Duration.between(programme.start, programme.end).toMillis() / 1000.0
        return ResumeSpot(ResumeRules.ctKey(id), programme.start, length)
    }

    override fun frame(position: Double, clock: BarClock, channelName: String): BarFrame = when (kind) {
        CtBarKind.TIMESHIFT -> timeshiftFrame(position, clock, channelName)
        CtBarKind.VOD -> vodFrame(position, clock, channelName)
    }

    private fun timeshiftFrame(position: Double, clock: BarClock, channelName: String): BarFrame {
        val span = CtBarRules.timeshiftSpan(clock.liveEnd)
        val start = span.start!!
        val end = span.end!!
        val playhead = toTime(position).let { if (it.isBefore(start)) start else if (it.isAfter(end)) end else it }
        val index = TimelineRules.programmeAt(programmes, playhead)
        val programme = index?.let { programmes[it] }
        val info = when {
            programme != null -> PlayerTitle.programme(programme.title, channelName, programme.subtitle)
            programmes.isEmpty() -> PlayerTitle.programme(title, channelName) // no guide (yet)
            else -> PlayerTitle.programme("", channelName) // a gap in the guide
        }
        return BarFrame(
            title = info.title,
            subtitle = info.subtitle,
            clock = TimelineRules.label(playhead, clock.now),
            startLabel = TimelineRules.edgeLabel(start, playhead, clock.now),
            endLabel = TimelineRules.endLabel(end, complete = false, playhead = playhead, now = clock.liveEnd),
            startMs = start.toEpochMilli(),
            endMs = end.toEpochMilli(),
            pieces = listOf(start.toEpochMilli() to end.toEpochMilli()),
            ticks = programmes.map { it.start.toEpochMilli() },
            highlight = programme?.let { it.start.toEpochMilli() to it.end.toEpochMilli() },
            playheadMs = playhead.toEpochMilli(),
        )
    }

    private fun vodFrame(position: Double, clock: BarClock, channelName: String): BarFrame {
        val duration = duration(clock) ?: maxOf(position, 0.0)
        val at = position.coerceIn(0.0, maxOf(duration, 0.0))
        val start = origin
        val end = toTime(duration)
        val playhead = toTime(at)
        val info = PlayerTitle.programme(title, channelName)
        val clockLabel: String
        val startLabel: String
        val endLabel: String
        if (knownStart) {
            clockLabel = TimelineRules.label(playhead, clock.now)
            startLabel = TimelineRules.edgeLabel(start, playhead, clock.now)
            endLabel = TimelineRules.edgeLabel(end, playhead, clock.now)
        } else {
            clockLabel = CtBarRules.elapsedLabel(at)
            startLabel = CtBarRules.elapsedEdgeLabel(0.0)
            endLabel = CtBarRules.elapsedEdgeLabel(duration)
        }
        val whole = start.toEpochMilli() to end.toEpochMilli()
        return BarFrame(
            title = info.title,
            subtitle = info.subtitle,
            clock = clockLabel,
            startLabel = startLabel,
            endLabel = endLabel,
            startMs = whole.first,
            endMs = whole.second,
            pieces = listOf(whole),
            ticks = emptyList(),
            highlight = whole,
            playheadMs = playhead.toEpochMilli(),
        )
    }

    companion object {
        /** The buffer planned at [plannedAt]: bar seconds count from `plannedAt − 3 h`. */
        fun timeshift(plannedAt: Instant, title: String): CtBarWindow =
            CtBarWindow(CtBarKind.TIMESHIFT, plannedAt.minus(CtBarRules.WINDOW), knownStart = true, title = title)

        /** An archive programme starting at [start] (null = unknown) of [lengthSeconds]. */
        fun vod(start: Instant?, lengthSeconds: Double, title: String): CtBarWindow =
            CtBarWindow(CtBarKind.VOD, start ?: Instant.EPOCH, knownStart = start != null, title = title, length = lengthSeconds)

        /**
         * The bar of a ČT playback planned as [kind] at [now]: start-over / "Zpět v čase" and the archive; null for
         * live and everything else (native controls). An archive episode without an `aired` time has the start
         * [Instant.EPOCH] (ArchiveRules.request): no known start.
         */
        fun of(request: PlayRequest, kind: PlayKind, now: Instant): CtBarWindow? {
            val (start, end) = when (request) {
                is PlayRequest.Programme -> request.start to request.end
                is PlayRequest.Startover -> request.start to request.end
                else -> return null
            }
            return when (kind) {
                PlayKind.TIMESHIFT -> timeshift(now, request.title)
                PlayKind.VOD -> {
                    val known = start.takeIf { it != Instant.EPOCH }
                    val length = end?.let { Duration.between(start, it).toMillis() / 1000.0 } ?: 0.0
                    vod(known, maxOf(0.0, length), request.title)
                }
                PlayKind.LIVE -> null
            }
        }

        /** Whether [request] plays with the own bar (ČT start-over and archive; the planner gives VOD or TIMESHIFT). */
        fun usesBar(request: PlayRequest): Boolean = request is PlayRequest.Programme || request is PlayRequest.Startover
    }
}
