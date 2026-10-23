package cz.cztvhub.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class GuideWindowTest {
    @Test
    fun aroundIsClampedToTheGrid() {
        val window = GuideWindow.around(row = 2, start = 60.0, end = 90.0, rowCount = 60)
        assertEquals(0..12, window.rows)
        assertEquals(0.0, window.fromMin, 0.0)
        assertEquals(390.0, window.toMin, 0.0)
        assertEquals(0..3, GuideWindow.around(row = 3, start = 0.0, end = 10.0, rowCount = 4).rows)
    }

    @Test
    fun containsOnlyCellsInsideRowsAndTime() {
        val window = GuideWindow(10..20, 100.0, 400.0)
        assertTrue(window.contains(10, 50.0, 101.0))
        assertTrue(window.contains(20, 399.0, 500.0))
        assertFalse(window.contains(9, 200.0, 210.0))
        assertFalse(window.contains(21, 200.0, 210.0))
        assertFalse(window.contains(15, 0.0, 100.0)) // ends where the window starts
        assertFalse(window.contains(15, 400.0, 430.0))
    }

    @Test
    fun followKeepsTheWindowWhileFocusIsWellInside() {
        val window = GuideWindow.around(row = 30, start = 600.0, end = 630.0, rowCount = 60)
        assertSame(window, GuideWindow.follow(window, 31, 640.0, 700.0, 60, 1440.0))
        assertSame(window, GuideWindow.follow(window, 28, 560.0, 600.0, 60, 1440.0))
    }

    @Test
    fun followMovesBeforeFocusReachesAnEdge() {
        val window = GuideWindow.around(row = 30, start = 600.0, end = 630.0, rowCount = 60) // rows 25..40, 420..930
        // near the bottom rows, the top rows, the right and the left edge
        assertEquals(33..48, GuideWindow.follow(window, 38, 600.0, 630.0, 60, 1440.0).rows)
        assertEquals(22..37, GuideWindow.follow(window, 27, 600.0, 630.0, 60, 1440.0).rows)
        assertEquals(1120.0, GuideWindow.follow(window, 30, 760.0, 820.0, 60, 1440.0).toMin, 0.0)
        assertEquals(330.0, GuideWindow.follow(window, 30, 510.0, 530.0, 60, 1440.0).fromMin, 0.0)
    }

    @Test
    fun theGridsOwnEdgesNeedNoMargin() {
        val window = GuideWindow.around(row = 0, start = 0.0, end = 30.0, rowCount = 8) // rows 0..7, 0..330
        assertSame(window, GuideWindow.follow(window, 7, 10.0, 30.0, 8, 1440.0))
        assertSame(window, GuideWindow.follow(window, 0, 0.0, 30.0, 8, 1440.0))
        val end = GuideWindow(0..7, 1000.0, 1500.0)
        assertSame(end, GuideWindow.follow(end, 3, 1400.0, 1440.0, 8, 1440.0))
    }
}
