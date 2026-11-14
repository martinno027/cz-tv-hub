package cz.cztvhub.core.dvb

import cz.cztvhub.core.PlayerTitle
import java.time.Instant

/**
 * What the bar's rules need of the moment: [now] (the real clock: day labels), the window's length the player knows
 * ([knownDuration], s) and, for the ČT buffer, the wall clock of the stream's end ([streamEnd], T62: ČT's stream
 * ends minutes behind the real clock); null = unknown, the real clock stands in.
 */
data class BarClock(val now: Instant, val knownDuration: Double? = null, val streamEnd: Instant? = null) {
    /** The ČT buffer's `now`: the end of the stream where known, else the real clock (spec "ČT player bar"). */
    val liveEnd: Instant get() = streamEnd ?: now
}

/**
 * What the own player bar shows at one moment (spec/apps.md "Anténa timeline player", "ČT player bar (T62)"):
 * the title lines, the playhead's clock, the bar's end labels and the bar itself in wall-clock milliseconds.
 */
data class BarFrame(
    val title: String,
    val subtitle: String,
    val clock: String,
    val startLabel: String,
    val endLabel: String,
    val startMs: Long,
    val endMs: Long,
    /** Filled stretches (recorded pieces; one piece for ČT). */
    val pieces: List<Pair<Long, Long>>,
    /** Programme boundaries. */
    val ticks: List<Long>,
    /** The programme at the playhead, null in a gap. */
    val highlight: Pair<Long, Long>?,
    val playheadMs: Long,
)

/**
 * The window the own bar plays over. Positions are "bar seconds": the Anténa window's playlist position, the ČT
 * VOD position, or seconds of wall clock from a fixed moment for the ČT buffer (whose player positions slide).
 * Seeks, the moving playhead (T55 rewind) and ▲/▼ go through it, so the keys work the same over every window.
 */
interface BarWindow {
    /** What a chosen seek to [target] does at the window's edges (`edge`): only the Anténa has neighbours. */
    fun edge(target: Double, clock: BarClock): TimelineEdge

    /** [target] kept inside the window where a seek may land. */
    fun clamp(target: Double, clock: BarClock): Double

    /** What the moving playhead of [trick] meets at [atMs] (SEEK = nothing, keep moving). */
    fun movingEdge(trick: TimelineTrick, atMs: Long, clock: BarClock): TimelineEdge

    /** ▲ ([up]) / ▼ from [position]: the target (may lie outside, then the edge rule applies) or null = nothing. */
    fun jumpTarget(position: Double, up: Boolean, clock: BarClock): Double?

    fun frame(position: Double, clock: BarClock, channelName: String): BarFrame
}

/** The Anténa timeline window ([DvbTimeline]) as a [BarWindow]: the T05 rules unchanged. */
class DvbBarWindow(val timeline: DvbTimeline) : BarWindow {
    /** The window's length: a growing window also what the player already knows. */
    fun duration(clock: BarClock): Double =
        if (timeline.complete) timeline.duration else maxOf(timeline.duration, clock.knownDuration ?: 0.0)

    /** A growing window's live edge, [TimelineRules.LIVE_GUARD_SECONDS] before the end the player knows. */
    fun liveEnd(clock: BarClock): Double = maxOf(0.0, duration(clock) - TimelineRules.LIVE_GUARD_SECONDS)

    override fun edge(target: Double, clock: BarClock): TimelineEdge = TimelineRules.edge(
        target, duration(clock), timeline.complete, timeline.previous != null, timeline.next != null,
    )

    override fun clamp(target: Double, clock: BarClock): Double =
        target.coerceIn(0.0, if (timeline.complete) duration(clock) else liveEnd(clock))

    override fun movingEdge(trick: TimelineTrick, atMs: Long, clock: BarClock): TimelineEdge = trick.edge(
        atMs, duration(clock), timeline.complete, timeline.previous != null, timeline.next != null, liveEnd(clock),
    )

    override fun jumpTarget(position: Double, up: Boolean, clock: BarClock): Double? =
        TimelineRules.jumpTarget(timeline, position, up)

    override fun frame(position: Double, clock: BarClock, channelName: String): BarFrame {
        val growing = !timeline.complete
        val pieces = timeline.pieces
        val duration = duration(clock)
        val windowStart = pieces.first().time
        val windowEnd = TimelineRules.toTime(pieces, duration, growing)
        val playhead = TimelineRules.toTime(pieces, position.coerceIn(0.0, duration), growing)
        val programme = TimelineRules.programmeAt(timeline.programmes, playhead)?.let { timeline.programmes[it] }
        val info = PlayerTitle.programme(programme?.title.orEmpty(), channelName, programme?.subtitle.orEmpty())
        val lastPiece = pieces.last()
        return BarFrame(
            title = info.title,
            subtitle = info.subtitle,
            clock = TimelineRules.label(playhead, clock.now),
            startLabel = TimelineRules.edgeLabel(windowStart, playhead, clock.now),
            endLabel = TimelineRules.endLabel(windowEnd, timeline.complete, playhead, clock.now),
            startMs = windowStart.toEpochMilli(),
            endMs = windowEnd.toEpochMilli(),
            pieces = pieces.map { piece ->
                val end = if (growing && piece === lastPiece) windowEnd else piece.endTime
                piece.time.toEpochMilli() to end.toEpochMilli()
            },
            ticks = timeline.programmes.map { it.start.toEpochMilli() },
            highlight = programme?.let { it.start.toEpochMilli() to it.end.toEpochMilli() },
            playheadMs = playhead.toEpochMilli(),
        )
    }
}
