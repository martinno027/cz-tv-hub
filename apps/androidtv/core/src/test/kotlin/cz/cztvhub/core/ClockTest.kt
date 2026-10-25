package cz.cztvhub.core

import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class ClockTest {
    private val cases = Fixtures.json("clock_cases.json").getValue("format").jsonArray.map { it.jsonObject }

    /** core's tests run in America/New_York (build.gradle.kts): these also prove the device zone is ignored. */
    @Test
    fun goldenFormatCases() {
        assertTrue(cases.isNotEmpty())
        for (case in cases) {
            val utc = Instant.parse(case.getValue("utc").jsonPrimitive.content)
            assertEquals(case.getValue("name").jsonPrimitive.content, case.getValue("text").jsonPrimitive.content, Clock.text(utc))
        }
    }

    @Test
    fun nextUpdateIsAtTheMinuteBoundary() {
        assertEquals(60_000L, Clock.untilNextMinute(Instant.parse("2026-10-25T13:30:00Z")))
        assertEquals(1L, Clock.untilNextMinute(Instant.parse("2026-10-25T13:30:59.999Z")))
        assertEquals(25_500L, Clock.untilNextMinute(Instant.parse("2026-10-25T13:30:34.500Z")))
        assertEquals(30_000L, Clock.untilNextMinute(Instant.parse("1969-12-31T23:59:30Z")))
    }
}
