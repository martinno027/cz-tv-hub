package cz.cztvhub.core

import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.double
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Golden cases `spec/fixtures/programme_page_cases.json` (spec/apps.md "Programme page (T15)"). */
class ProgrammePageTest {
    private val root = Fixtures.json("programme_page_cases.json")

    private val ids = mapOf(
        "resume" to PageButton.RESUME,
        "startOver" to PageButton.START_OVER,
        "live" to PageButton.LIVE,
        "openOfficialApp" to PageButton.OPEN_OFFICIAL_APP,
    )

    @Test fun goldenButtons() {
        val cases = root.getValue("buttons").jsonArray
        assertTrue(cases.size >= 10)
        for (case in cases) {
            val c = case.jsonObject
            fun flag(name: String) = c[name]?.jsonPrimitive?.boolean ?: false
            val actual = ProgrammePage.buttons(
                source = PageSource.valueOf(c.getValue("source").jsonPrimitive.content.uppercase()),
                state = PageState.valueOf(c.getValue("state").jsonPrimitive.content.uppercase()),
                startable = flag("startable"),
                playable = flag("playable"),
                resume = flag("resume"),
            )
            val expected = c.getValue("buttons").jsonArray.map { ids.getValue(it.jsonPrimitive.content) }
            assertEquals(c.getValue("name").jsonPrimitive.content, expected, actual)
        }
    }

    @Test fun goldenRatingLines() {
        val cases = root.getValue("rating").jsonArray
        assertTrue(cases.size >= 6)
        for (case in cases) {
            val c = case.jsonObject
            val line = c.getValue("line").takeUnless { it is JsonNull }?.jsonPrimitive?.content
            assertEquals("$c", line, ProgrammePage.ratingLine(c.getValue("value").jsonPrimitive.double, c.getValue("votes").jsonPrimitive.int))
        }
    }

    @Test fun goldenPlural() {
        val cases = root.getValue("plural").jsonArray
        assertTrue(cases.size >= 6)
        for (case in cases) {
            val c = case.jsonObject
            assertEquals("$c", c.getValue("word").jsonPrimitive.content, ProgrammePage.votesWord(c.getValue("n").jsonPrimitive.int))
        }
    }

    @Test fun ratingRoundsHalfUpWithoutBinaryDrift() {
        // 6.25 and 2.35 are not exact in binary; the line still rounds the written value half up.
        assertEquals("Hodnocení TMDB 6,3 (20 hlasů)", ProgrammePage.ratingLine(6.25, 20))
        assertEquals("Hodnocení TMDB 2,4 (20 hlasů)", ProgrammePage.ratingLine(2.35, 20))
        assertNull(ProgrammePage.ratingLine(Double.NaN, 100))
    }

    @Test fun originalTitleOnlyWhenItDiffersFromBothTitles() {
        assertEquals("Původní název: The Matrix", ProgrammePage.originalTitleLine("Matrix", "Matrix", "The Matrix"))
        assertEquals("Původní název: Cozy Dens", ProgrammePage.originalTitleLine("Pelíšky (R)", "Pelíšky", "Cozy Dens"))
        assertNull(ProgrammePage.originalTitleLine("Matrix", null, "matrix"))
        // The guide's title differs (EPG suffix), TMDb's Czech title is the original: nothing to add.
        assertNull(ProgrammePage.originalTitleLine("Pelíšky (R)", "Pelíšky", "Pelíšky"))
        assertNull(ProgrammePage.originalTitleLine("Matrix", "Matrix", " "))
        assertNull(ProgrammePage.originalTitleLine("Matrix", "Matrix", null))
    }

    @Test fun genreIsTheGuidesElseTwoOfTmdbs() {
        assertEquals("Film", ProgrammePage.genre("Film", listOf("Komedie", "Drama")))
        assertEquals("Komedie, Drama", ProgrammePage.genre(" ", listOf("Komedie", "", "Drama", "Rodinný")))
        assertEquals("", ProgrammePage.genre("", emptyList()))
    }

    @Test fun minutesAreTheBroadcastLength() {
        val start = java.time.Instant.parse("2026-10-24T18:15:00Z")
        assertEquals(110L, ProgrammePage.minutes(start, start.plusSeconds(110 * 60)))
        assertEquals(111L, ProgrammePage.minutes(start, start.plusSeconds(110 * 60 + 30)))
        assertEquals(110L, ProgrammePage.minutes(start, start.plusSeconds(110 * 60 + 29)))
        assertEquals(0L, ProgrammePage.minutes(start, start.minusSeconds(60)))
    }
}
