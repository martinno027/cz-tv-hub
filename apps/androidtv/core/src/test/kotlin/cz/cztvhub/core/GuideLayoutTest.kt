package cz.cztvhub.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.LocalDate

class GuideLayoutTest {
    private fun t(hm: String) = Instant.parse("2026-10-17T${hm}:00Z")
    private fun p(id: String, from: String, to: String) = Programme(id, id, t(from), t(to), true)
    private val axisStart = t("20:00")
    private val axisEnd = t("23:00")

    private fun assertContiguous(slots: List<GuideLayout.Slot<Programme>>) {
        assertEquals(axisStart, slots.first().start)
        slots.zipWithNext().forEach { (a, b) -> assertEquals("no overlap, no hole", a.end, b.start) }
        slots.forEach { assertTrue(it.end.isAfter(it.start)) }
    }

    @Test fun emptyRowIsOneFiller() {
        val slots = GuideLayout.row(emptyList<Programme>(), axisStart, axisEnd)
        assertEquals(listOf(GuideLayout.Slot<Programme>(axisStart, axisEnd, null)), slots)
    }

    @Test fun gapsBecomeFillersAndProgrammesKeepTheirTimes() {
        val slots = GuideLayout.row(listOf(p("a", "20:30", "21:00"), p("b", "21:30", "22:00")), axisStart, axisEnd)
        assertContiguous(slots)
        assertEquals(listOf(null, "a", null, "b", null), slots.map { it.item?.idec })
        assertEquals(t("20:30"), slots[1].start)
        assertEquals(t("21:00"), slots[1].end)
        assertEquals(axisEnd, slots.last().end)
    }

    @Test fun overlappingProgrammesAreLaidOutSideBySide() {
        // The schedule API does publish overlapping programmes (a short slot overlapping the next one).
        val slots = GuideLayout.row(listOf(p("weather", "21:00", "21:08"), p("news", "21:05", "22:00")), axisStart, axisEnd)
        assertContiguous(slots)
        val news = slots.single { it.item?.idec == "news" }
        assertEquals(t("21:08"), news.start)
        assertEquals(t("22:00"), news.end)
    }

    @Test fun veryShortProgrammeGetsMinimumWidthAndPushesTheNextOne() {
        val slots = GuideLayout.row(listOf(p("short", "21:00", "21:01"), p("next", "21:01", "21:30")), axisStart, axisEnd)
        assertContiguous(slots)
        val short = slots.single { it.item?.idec == "short" }
        assertEquals(GuideLayout.MIN_SLOT.seconds, short.end.epochSecond - short.start.epochSecond)
        assertEquals(short.end, slots.single { it.item?.idec == "next" }.start)
    }

    @Test fun tinyGapIsAbsorbed() {
        val slots = GuideLayout.row(listOf(p("a", "20:00", "21:00"), p("b", "21:00", "22:00")), axisStart, t("22:00"))
        assertEquals(listOf("a", "b"), slots.map { it.item?.idec })
        val withSliver = GuideLayout.row(
            listOf(Programme("a", "a", t("20:00"), t("21:00").minusSeconds(20), true), p("b", "21:00", "22:00")),
            axisStart, t("22:00"),
        )
        assertContiguous(withSliver)
        assertNull(withSliver.firstOrNull { it.item == null })
    }

    @Test fun axisIsTheBroadcastDayInPrague() {
        // summer time: 05:00 CEST = 03:00 UTC, 24 h
        assertEquals(Instant.parse("2026-10-17T03:00:00Z") to Instant.parse("2026-10-18T03:00:00Z"),
            GuideLayout.axis(LocalDate.of(2026, 10, 17)))
        val dst = GuideLayout.axis(LocalDate.of(2026, 10, 24)) // clocks go back on 25 Oct: a 25 h day
        assertEquals(Instant.parse("2026-10-24T03:00:00Z"), dst.first)
        assertEquals(Instant.parse("2026-10-25T04:00:00Z"), dst.second)
        assertEquals(25L, java.time.Duration.between(dst.first, dst.second).toHours())
        val spring = GuideLayout.axis(LocalDate.of(2026, 3, 28)) // 29 Mar: a 23 h day
        assertEquals(23L, java.time.Duration.between(spring.first, spring.second).toHours())
    }

    @Test fun programmesCrossingTheDayEdgeAreClipped() {
        val slots = GuideLayout.row(
            listOf(p("night", "19:00", "20:30"), p("late", "22:30", "23:45"), p("tomorrow", "23:50", "23:59")),
            axisStart, axisEnd,
        )
        assertContiguous(slots)
        assertEquals(listOf("night", null, "late"), slots.map { it.item?.idec })
        assertEquals(axisStart, slots.first().start) // clipped at the start
        assertEquals(axisEnd, slots.last().end) // clipped at the end, "tomorrow" left out
    }

    @Test fun ticksAreWholeLabels() {
        assertEquals(listOf(t("20:00"), t("20:30"), t("21:00")), GuideLayout.ticks(t("20:00"), t("21:10")))
    }

    @Test fun slotAtFindsTheCellUnderTheNowLine() {
        val slots = GuideLayout.row(listOf(p("a", "20:30", "21:00"), p("b", "21:30", "22:00")), axisStart, axisEnd)
        assertEquals("a", slots[GuideLayout.slotAt(slots, t("20:45"))!!].item?.idec)
        // nothing airing: the "Mimo vysílání" filler under the line
        val gap = GuideLayout.slotAt(slots, t("21:10"))!!
        assertNull(slots[gap].item)
        assertEquals(t("21:00"), slots[gap].start)
        // the boundary belongs to the programme that starts there
        assertEquals("b", slots[GuideLayout.slotAt(slots, t("21:30"))!!].item?.idec)
        // outside the axis: the next slot, else the last
        assertEquals(0, GuideLayout.slotAt(slots, t("19:00")))
        assertEquals(slots.lastIndex, GuideLayout.slotAt(slots, t("23:30")))
        assertNull(GuideLayout.slotAt(emptyList<GuideLayout.Slot<Programme>>(), t("20:45")))
    }

    @Test fun verticalMoveFromAnAiringProgrammeAimsAtNow() {
        val now = t("20:45")
        val cursor = t("20:10")
        val airing = GuideLayout.Slot(t("20:30"), t("21:00"), p("a", "20:30", "21:00"))
        val earlier = GuideLayout.Slot(t("20:00"), t("20:30"), p("e", "20:00", "20:30"))
        // from an airing programme: the next row's programme at now, even after left/right (live = false)
        assertEquals(now, GuideLayout.verticalMoment(live = false, from = airing, now = now, cursor = cursor))
        // from a programme that is not airing: keep the time position
        assertEquals(cursor, GuideLayout.verticalMoment(live = false, from = earlier, now = now, cursor = cursor))
        // the initial live mode, or nothing focused yet
        assertEquals(now, GuideLayout.verticalMoment(live = true, from = earlier, now = now, cursor = cursor))
        assertEquals(cursor, GuideLayout.verticalMoment(live = false, from = null, now = now, cursor = cursor))
        // the end is exclusive: a programme that just ended is not airing
        assertEquals(cursor, GuideLayout.verticalMoment(false, airing, t("21:00"), cursor))
        // a slot pushed right by an overlap: its programme is still airing
        val pushed = GuideLayout.Slot(t("20:50"), t("21:30"), p("o", "20:40", "21:30"))
        assertEquals(t("20:45"), GuideLayout.verticalMoment(false, pushed, t("20:45"), cursor))
    }
}
