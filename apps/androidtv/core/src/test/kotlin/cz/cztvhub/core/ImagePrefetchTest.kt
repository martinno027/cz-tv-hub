package cz.cztvhub.core

import cz.cztvhub.core.ImagePrefetch.Item
import cz.cztvhub.core.ImagePrefetch.Point
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class ImagePrefetchTest {
    private fun t(hm: String) = Instant.parse("2026-10-17T${hm}:00Z")
    private fun p(from: String, to: String) = Programme("$from-$to", "x", t(from), t(to), true)

    /** Every row: hourly programmes 12:00–24:00. */
    private val hourly = (12 until 24).map { h -> p("%02d:00".format(h), if (h == 23) "23:59" else "%02d:00".format(h + 1)) }

    @Test
    fun windowIsThreeAboveEightBelowAndThreeHoursEachWay() {
        val rows = List(20) { hourly }
        val items = ImagePrefetch.window(rows, Point(row = 5, time = t("18:30")))
        assertEquals(2..13, items.minOf { it.row }..items.maxOf { it.row })
        // 15:00–16:00 ends 2.5 h before; 14:00–15:00 ends 3.5 h before (out); 21:00–22:00 starts 2.5 h after.
        val hours = items.filter { it.row == 5 }.map { rows[5][it.index].start }
        assertEquals((15..21).map { t("%02d:00".format(it)) }, hours.sorted())
    }

    @Test
    fun nearestFirstByRowsThenTime() {
        val rows = List(20) { hourly }
        val items = ImagePrefetch.window(rows, Point(row = 5, time = t("18:30")))
        // The focused programme, then its row outwards by time, then the rows next to it.
        assertEquals(Item(5, 6), items.first())
        assertEquals(setOf(Item(5, 5), Item(5, 7)), items.subList(1, 3).toSet())
        assertTrue(items.takeWhile { it.row == 5 }.size == 7)
        assertEquals(setOf(4, 6), items.subList(7, 21).map { it.row }.toSet())
        val distances = items.map { kotlin.math.abs(it.row - 5) }
        assertEquals(distances.sorted(), distances)
    }

    @Test
    fun clipsAtTheGridEdgesAndSkipsEmptyRows() {
        val rows = listOf(hourly, emptyList(), hourly)
        val items = ImagePrefetch.window(rows, Point(row = 0, time = t("12:10")))
        assertEquals(setOf(0, 2), items.map { it.row }.toSet())
        assertTrue(ImagePrefetch.window(emptyList<List<Programme>>(), Point(0, t("12:00"))).isEmpty())
    }

    @Test
    fun movedAfterMoreThanOneHourOrThreeRows() {
        val last = Point(5, t("18:00"))
        assertTrue(ImagePrefetch.moved(null, last))
        assertFalse(ImagePrefetch.moved(last, Point(8, t("19:00"))))
        assertTrue(ImagePrefetch.moved(last, Point(9, t("18:00"))))
        assertFalse(ImagePrefetch.moved(last, Point(2, t("18:00"))))
        assertTrue(ImagePrefetch.moved(last, Point(1, t("18:00"))))
        assertTrue(ImagePrefetch.moved(last, Point(5, t("19:01"))))
        assertTrue(ImagePrefetch.moved(last, Point(5, t("16:59"))))
    }

    @Test
    fun missingImagesForTheGuideOrAMinute() {
        val missing = MissingImages()
        val now = t("18:00")
        missing.add("done", stillRecording = false, now = now)
        missing.add("rec", stillRecording = true, now = now)
        assertTrue(missing.isMissing("done", now.plusSeconds(3600)))
        assertTrue(missing.isMissing("rec", now.plusSeconds(59)))
        assertFalse(missing.isMissing("rec", now.plusSeconds(60)))
        assertFalse(missing.isMissing("other", now))
    }
}
