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

    /**
     * Index of the slot under [moment] (programme or "Mimo vysílání" filler), else the next one,
     * else the last one; null for an empty row. Used to walk the guide's "now" column
     * (spec/apps.md "Live column first") and to keep a time position on up/down.
     */
    fun <T : Timed> slotAt(slots: List<Slot<T>>, moment: Instant): Int? {
        val under = slots.indexOfFirst { !moment.isBefore(it.start) && moment.isBefore(it.end) }
        if (under >= 0) return under
        val next = slots.indexOfFirst { it.start.isAfter(moment) }
        return if (next >= 0) next else slots.indices.lastOrNull()
    }

    /** Axis ticks every [step], starting at [axisStart]. */
    fun ticks(axisStart: Instant, axisEnd: Instant, step: Duration = Duration.ofMinutes(30)): List<Instant> =
        generateSequence(axisStart) { it.plus(step) }.takeWhile { !it.isAfter(axisEnd) }.toList()
}
