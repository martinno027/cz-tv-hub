package cz.cztvhub.core

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.time.Instant
import java.time.LocalDate
import org.junit.Assert.assertEquals
import org.junit.Test

/** spec/fixtures/guide_days_cases.json: every case. */
class GuideDaysTest {
    private val root = Fixtures.json("guide_days_cases.json")

    private fun cases(name: String): List<JsonObject> = root.getValue(name).jsonArray.map { it.jsonObject }

    private fun JsonObject.str(key: String): String = getValue(key).jsonPrimitive.content

    @Test
    fun openDay() {
        for (c in cases("openDay")) {
            assertEquals(c.str("name"), LocalDate.parse(c.str("day")), Days.openDay(Instant.parse(c.str("now"))))
        }
    }

    @Test
    fun chipDay() {
        for (c in cases("chipDay")) {
            assertEquals(c.str("name"), LocalDate.parse(c.str("day")), Days.chipDay(Instant.parse(c.str("time"))))
        }
    }

    @Test
    fun select() {
        for (c in cases("select")) {
            val target = Days.select(LocalDate.parse(c.str("chip")), Instant.parse(c.str("now")))
            val anchor = c.str("anchor").let { if (it == "now") null else Instant.parse(it) }
            assertEquals(c.str("name"), Days.Target(LocalDate.parse(c.str("day")), anchor), target)
        }
    }

    @Test
    fun todayFollowsTheClock() {
        // An airing 0:10 programme is "Dnes" once midnight has passed, never "Zítra".
        val now = Instant.parse("2026-10-23T22:20:00Z")
        val start = Instant.parse("2026-10-23T22:10:00Z")
        assertEquals("Dnes", Days.label(Days.chipDay(start), Days.today(now)))
    }
}
