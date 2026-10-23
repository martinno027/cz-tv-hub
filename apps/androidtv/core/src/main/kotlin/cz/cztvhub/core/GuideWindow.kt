package cz.cztvhub.core

/**
 * The part of the TV guide that is actually composed: a range of rows and a time range (minutes from the
 * axis start). Composing every cell of a day for dozens of Anténa channels (thousands of cells) made the
 * guide react seconds late on real TVs; the rest of the grid stays empty space of the same size.
 * The window follows focus and moves before focus reaches its edge, so the cell focus moves into is
 * always composed.
 */
data class GuideWindow(val rows: IntRange, val fromMin: Double, val toMin: Double) {
    /** A cell of [row] spanning [start, end) minutes is composed. */
    fun contains(row: Int, start: Double, end: Double): Boolean = row in rows && overlaps(start, end)

    /** A cell spanning [start, end) minutes overlaps the time range. */
    fun overlaps(start: Double, end: Double): Boolean = start < toMin && end > fromMin

    /**
     * Still fine for a focused cell of [row] spanning [start, end): enough rows and time around it to move
     * into ([axisEnd] = the axis length in minutes, [rowCount] = all rows).
     */
    fun covers(row: Int, start: Double, end: Double, rowCount: Int, axisEnd: Double): Boolean {
        val top = rows.first <= 0 || row >= rows.first + ROW_MARGIN
        val bottom = rows.last >= rowCount - 1 || row <= rows.last - ROW_MARGIN
        val left = fromMin <= 0.0 || start >= fromMin + MARGIN_MIN
        val right = toMin >= axisEnd || end <= toMin - MARGIN_MIN
        return top && bottom && left && right
    }

    companion object {
        const val ROWS_BEFORE = 5
        const val ROWS_AFTER = 10
        const val ROW_MARGIN = 3
        /** About two hours are on screen; long programmes and quick presses need room on both sides. */
        const val BEFORE_MIN = 180.0
        const val AFTER_MIN = 300.0
        const val MARGIN_MIN = 120.0

        /** A window centred on the cell of [row] spanning [start, end) minutes. */
        fun around(row: Int, start: Double, end: Double, rowCount: Int): GuideWindow =
            GuideWindow(
                rows = maxOf(0, row - ROWS_BEFORE)..minOf(rowCount - 1, row + ROWS_AFTER),
                fromMin = maxOf(0.0, start - BEFORE_MIN),
                toMin = end + AFTER_MIN,
            )

        /** [current] if it still covers the focused cell, else a window around it. */
        fun follow(current: GuideWindow, row: Int, start: Double, end: Double, rowCount: Int, axisEnd: Double): GuideWindow =
            if (current.covers(row, start, end, rowCount, axisEnd)) current else around(row, start, end, rowCount)
    }
}
