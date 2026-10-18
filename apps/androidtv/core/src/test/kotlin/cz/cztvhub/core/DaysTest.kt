package cz.cztvhub.core

import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.Instant
import java.time.LocalDate

class DaysTest {
    private val today = LocalDate.of(2026, 10, 17) // Saturday

    @Test fun labels() {
        assertEquals("Dnes", Days.label(today, today))
        assertEquals("Včera", Days.label(today.minusDays(1), today))
        assertEquals("po 12. 10.", Days.label(LocalDate.of(2026, 10, 12), today))
        assertEquals("ne 18. 10.", Days.label(today.plusDays(1), today))
        assertEquals("čt 1. 1.", Days.label(LocalDate.of(2026, 1, 1), today))
    }

    @Test fun guideRangeIsSevenBackTwoAhead() {
        val days = Days.guideDays(today)
        assertEquals(10, days.size)
        assertEquals(LocalDate.of(2026, 10, 10), days.first())
        assertEquals(LocalDate.of(2026, 10, 19), days.last())
    }

    @Test fun todayAndTimesArePragueBased() {
        assertEquals(LocalDate.of(2026, 10, 18), Days.today(Instant.parse("2026-10-17T22:30:00Z")))
        assertEquals("07:00", Days.time(Instant.parse("2026-10-17T05:00:00Z")))
        assertEquals("05:59–09:00", Days.timeRange(Instant.parse("2026-10-16T03:59:00Z"), Instant.parse("2026-10-16T07:00:00Z")))
    }
}
