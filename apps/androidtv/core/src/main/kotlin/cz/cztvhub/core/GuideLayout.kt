package cz.cztvhub.core

import java.time.Duration
import java.time.Instant

/**
 * Horizontal layout of one guide row. Slots never overlap, follow start/end where the data allows,
 * and gaps (or a row without data) become "Mimo vysílání" filler slots so the row is never blank.
 */
object GuideLayout {
    /** Smallest slot, so very short programmes stay visible and focusable. */
    val MIN_SLOT: Duration = Duration.ofMinutes(3)

    /** Gaps shorter than this are absorbed instead of producing a sliver of filler. */
    val MIN_FILLER: Duration = Duration.ofMinutes(1)

    /** [item] is null for a filler. */
    data class Slot<T : Timed>(val start: Instant, val end: Instant, val item: T?)

    /** Start of the time axis: the earliest programme, floored to a half hour. */
    fun axisStart(programmes: Collection<Timed>): Instant? {
        val first = programmes.minOfOrNull { it.start } ?: return null
        val minutes = first.epochSecond / 60
        return Instant.ofEpochSecond((minutes - minutes % 30) * 60)
    }

    fun <T : Timed> row(programmes: List<T>, axisStart: Instant, axisEnd: Instant): List<Slot<T>> {
        val slots = ArrayList<Slot<T>>()
        var cursor = axisStart
        for (p in programmes.sortedBy { it.start }) {
            if (!p.end.isAfter(cursor) && !p.start.isBefore(cursor)) continue // zero-length duplicate
            var start = maxOf(p.start, cursor)
            if (Duration.between(cursor, start) >= MIN_FILLER) {
                slots += Slot(cursor, start, null)
            } else {
                start = cursor // absorb a tiny gap
            }
            // Overlaps push the start right; a minimum width pushes the end (and the next start) right.
            val end = maxOf(p.end, start.plus(MIN_SLOT))
            slots += Slot(start, end, p)
            cursor = end
        }
        if (Duration.between(cursor, axisEnd) >= MIN_FILLER) slots += Slot(cursor, axisEnd, null)
        return slots
    }

    /** Axis ticks every [step], starting at [axisStart]. */
    fun ticks(axisStart: Instant, axisEnd: Instant, step: Duration = Duration.ofMinutes(30)): List<Instant> =
        generateSequence(axisStart) { it.plus(step) }.takeWhile { !it.isAfter(axisEnd) }.toList()
}
