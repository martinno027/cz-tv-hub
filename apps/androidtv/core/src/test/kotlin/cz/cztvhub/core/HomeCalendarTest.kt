package cz.cztvhub.core

import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Duration
import java.time.Instant
import java.time.LocalDate

class HomeCalendarTest {
    private val fixture = Fixtures.json("calendar_cases.json")

    private fun strings(name: String) = fixture.getValue(name).jsonArray.map { it.jsonPrimitive.content }

    @Test
    fun goldenCases() {
        val cases = fixture.getValue("cases").jsonArray.map { it.jsonObject }
        assertTrue(cases.size > 20)
        for (case in cases) {
            val day = LocalDate.parse(case.getValue("date").jsonPrimitive.content)
            val expected = HomeHeader(
                fullDate = case.getValue("fullDate").jsonPrimitive.content,
                nameLine = case.getValue("nameLine").takeIf { it != JsonNull }?.jsonPrimitive?.content,
                lines = case.getValue("lines").jsonArray.map { it.jsonPrimitive.content },
            )
            assertEquals("$day", expected, HomeCalendar.header(day))
        }
    }

    @Test
    fun tablesEqualTheFixtures() {
        assertEquals(strings("weekdays"), HomeCalendar.WEEKDAYS)
        assertEquals(strings("monthsGenitive"), HomeCalendar.MONTHS_GENITIVE)
        val fixed = fixture.getValue("stateHolidays").jsonObject.mapValues { it.value.jsonPrimitive.content }
        assertEquals(fixed, HomeCalendar.STATE_HOLIDAYS)
        val easter = fixture.getValue("easterHolidays").jsonObject
            .map { it.key.toInt() to it.value.jsonPrimitive.content }.toMap()
        assertEquals(easter, HomeCalendar.EASTER_HOLIDAYS)
        val school = fixture.getValue("schoolHolidays").jsonArray.map { it.jsonObject }.map {
            SchoolHoliday(
                it.getValue("name").jsonPrimitive.content,
                LocalDate.parse(it.getValue("from").jsonPrimitive.content),
                LocalDate.parse(it.getValue("to").jsonPrimitive.content),
            )
        }
        assertEquals(school, HomeCalendar.SCHOOL_HOLIDAYS)
    }

    @Test
    fun nameDaysEqualTheFixture() {
        val days = Fixtures.json("namedays_cz.json").getValue("days").jsonObject.mapValues { (_, names) ->
            if (names == JsonNull) emptyList() else names.jsonArray.map { it.jsonPrimitive.content }
        }
        assertEquals(366, days.size)
        assertEquals(days, NameDays.DAYS)
        // Same order of days too: the table is read by key, but a diff against the fixture stays readable.
        assertEquals(days.keys.toList(), NameDays.DAYS.keys.toList())
    }

    @Test
    fun easterByComputus() {
        for ((year, date) in fixture.getValue("easter").jsonObject) {
            assertEquals(year, LocalDate.parse(date.jsonPrimitive.content), HomeCalendar.easter(year.toInt()))
        }
    }

    @Test
    fun clockChangeIsTheLastSundayOfMarchAndOctober() {
        for ((year, dates) in fixture.getValue("clockChange").jsonObject) {
            val expected = dates.jsonArray.map { LocalDate.parse(it.jsonPrimitive.content) }
            assertEquals(year, expected, HomeCalendar.clockChanges(year.toInt()))
        }
    }

    @Test
    fun moon2026MatchesPublishedTables() = checkMoonYear(2026)

    /** 2027 has phases near Prague midnight (21 Feb 0:25, 21 Apr 0:20, 16 Sep 0:59): the day must still be right. */
    @Test
    fun moon2027MatchesPublishedTables() = checkMoonYear(2027)

    /** Every day of [year]: exactly the days listed in `moon<year>` have a phase (no extra, none missing). */
    private fun checkMoonYear(year: Int) {
        val expected = fixture.getValue("moon$year").jsonArray.map { it.jsonObject }.associate {
            LocalDate.parse(it.getValue("date").jsonPrimitive.content) to it.getValue("phase").jsonPrimitive.content
        }
        assertEquals(25, expected.size)
        var day = LocalDate.of(year, 1, 1)
        while (day.year == year) {
            val phase = HomeCalendar.moonPhase(day)?.name?.lowercase()
            assertEquals("$day", expected[day], phase)
            day = day.plusDays(1)
        }
    }

    @Test
    fun moonInstantToTheSecond() {
        val case = fixture.getValue("moonInstant").jsonObject
        val phase = HomeCalendar.MoonPhase.valueOf(case.getValue("phase").jsonPrimitive.content.uppercase())
        val near = LocalDate.parse(case.getValue("near").jsonPrimitive.content)
        val expected = Instant.parse(case.getValue("utc").jsonPrimitive.content)
        val k = case.getValue("k").jsonPrimitive.content.toDouble()
        val actual = HomeCalendar.moonInstant(k)
        assertTrue("$actual vs $expected", Duration.between(expected, actual).abs() <= Duration.ofSeconds(1))
        // The day search finds the same phase.
        assertEquals(actual, HomeCalendar.moonNear(near, phase))
    }

    @Test
    fun headerUsesThePragueCalendarDay() {
        // 23:30 UTC on 3 November is already 4 November in Prague (tests run in America/New_York).
        assertEquals("středa 4. listopadu", HomeCalendar.header(Instant.parse("2026-11-03T23:30:00Z")).fullDate)
        assertEquals("úterý 3. listopadu", HomeCalendar.header(Instant.parse("2026-11-03T22:59:00Z")).fullDate)
    }

    @Test
    fun noNameAndNoHolidayIsNoLine() {
        assertNull(HomeCalendar.nameLine(LocalDate.of(2026, 11, 2)))
    }
}
