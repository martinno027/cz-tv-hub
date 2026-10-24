package cz.cztvhub.core

import java.time.Duration
import java.time.Instant
import kotlin.math.abs

/**
 * Which guide images to fetch ahead of the focus (spec/apps.md "Image prefetch"): the programmes of the rows from
 * 3 above to 8 below the focused row that overlap ±3 h around the focused time, nearest to the focus first.
 */
object ImagePrefetch {
    const val ROWS_ABOVE = 3
    const val ROWS_BELOW = 8
    val SPAN: Duration = Duration.ofHours(3)

    /** A new window is computed once the focus is further than this from the last prefetch point. */
    const val MOVE_ROWS = 3
    val MOVE_TIME: Duration = Duration.ofHours(1)

    /** Where the focus is: a row index and a moment. */
    data class Point(val row: Int, val time: Instant)

    /** A programme of the window: [index] in [row]'s list. */
    data class Item(val row: Int, val index: Int)

    /** True when [focus] is far enough from [last] (null = nothing prefetched yet) for a new window. */
    fun moved(last: Point?, focus: Point): Boolean =
        last == null || abs(focus.row - last.row) > MOVE_ROWS ||
            Duration.between(last.time, focus.time).abs() > MOVE_TIME

    /**
     * The window around [focus] over [rows] (each row's programmes), ordered by row distance, then by the time
     * between the focused moment and the programme (0 when it spans it); ties keep the grid order.
     */
    fun <T : Timed> window(rows: List<List<T>>, focus: Point): List<Item> {
        val from = focus.time.minus(SPAN)
        val to = focus.time.plus(SPAN)
        val first = maxOf(0, focus.row - ROWS_ABOVE)
        val last = minOf(rows.size - 1, focus.row + ROWS_BELOW)
        val picked = ArrayList<Triple<Item, Int, Long>>()
        for (row in first..last) {
            rows[row].forEachIndexed { index, p ->
                if (p.start.isBefore(to) && p.end.isAfter(from)) {
                    val gap = when {
                        focus.time.isBefore(p.start) -> Duration.between(focus.time, p.start).seconds
                        !focus.time.isBefore(p.end) -> Duration.between(p.end, focus.time).seconds
                        else -> 0L
                    }
                    picked += Triple(Item(row, index), abs(row - focus.row), gap)
                }
            }
        }
        // sortedWith is stable: equal distances stay in row, then index order.
        return picked.sortedWith(compareBy({ it.second }, { it.third })).map { it.first }
    }

    /** A `404` image of a still-running recording is asked again after this; a finished one never (per guide). */
    val MISSING_RETRY: Duration = Duration.ofSeconds(60)
}

/** Images that answered `404`, so the guide does not ask for them again (spec/apps.md "Image prefetch"). */
class MissingImages {
    /** URL → when it may be asked again; null = not for this guide's lifetime. */
    private val until = HashMap<String, Instant?>()

    /** [stillRecording]: the image may still appear (a recording in progress), so only for a minute. */
    fun add(url: String, stillRecording: Boolean, now: Instant) {
        until[url] = if (stillRecording) now.plus(ImagePrefetch.MISSING_RETRY) else null
    }

    fun isMissing(url: String, now: Instant): Boolean {
        if (url !in until) return false
        val retry = until[url] ?: return true
        if (now.isBefore(retry)) return true
        until.remove(url)
        return false
    }
}
