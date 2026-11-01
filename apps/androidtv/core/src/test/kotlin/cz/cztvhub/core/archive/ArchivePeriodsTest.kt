package cz.cztvhub.core.archive

import cz.cztvhub.core.Fixtures
import cz.cztvhub.core.HttpResponse
import cz.cztvhub.core.dvb.DvbClient
import cz.cztvhub.core.dvb.DvbConfig
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Year and month chips of a long ČT episode list (T32): golden `archive_cases.json` "periods", parser and requests. */
class ArchivePeriodsTest {
    private val golden = Fixtures.json("archive_cases.json").getValue("periods").jsonObject

    /** A show answer whose ČT group carries [periods] (the gateway's shape, spec/dvb-gateway.md "Archive"). */
    private fun show(periods: String, season: String = "null") = """
        {"card": {"id": "ct:10000000009", "source": "ct", "title": "Dlouhý pořad", "kind": "series"},
         "seasons": [],
         "groups": [
           {"title": "Díly", "season": $season, "nextPage": 2, "periods": $periods, "episodes": [
             {"source": "ct", "idec": "226000000001", "title": "Díl z listopadu", "durationSeconds": 1500,
              "aired": "2026-11-01T17:00:00Z", "channel": "ČT1", "channelId": "CH_1", "playable": true}]},
           {"title": "Nahráno z antény", "episodes": []}]}
    """.trimIndent()

    private fun parsed(periods: JsonElement): ArchivePeriods? =
        ArchiveParser.parseShow(200, show(periods.toString())).groups.first().periods

    private fun labels(chips: JsonElement) = chips.jsonArray.map { it.jsonObject.getValue("label").jsonPrimitive.content to it.jsonObject.getValue("selected").jsonPrimitive.boolean }

    @Test fun monthNamesFollowTheGoldenCases() {
        val cases = golden.getValue("monthName").jsonArray
        assertEquals(12, cases.size)
        for (case in cases) {
            val o = case.jsonObject
            assertEquals("month ${o["month"]}", o.getValue("name").jsonPrimitive.content, ArchiveRules.monthName(o.getValue("month").jsonPrimitive.int))
        }
        assertNull(ArchiveRules.monthName(0))
        assertNull(ArchiveRules.monthName(13))
    }

    @Test fun chipsFollowTheGoldenCases() {
        val cases = golden.getValue("chips").jsonArray
        assertTrue(cases.size >= 3)
        for (case in cases) {
            val o = case.jsonObject
            val name = o.getValue("name").jsonPrimitive.content
            val periods = o.getValue("periods").takeIf { it !is JsonNull }?.let { parsed(it) }
            if (o.getValue("periods") !is JsonNull) assertTrue("$name: parsed", periods != null)
            assertEquals("$name years", labels(o.getValue("years")), ArchiveRules.yearChips(periods).map { it.label to it.selected })
            assertEquals("$name months", labels(o.getValue("months")), ArchiveRules.monthChips(periods).map { it.label to it.selected })
        }
        // A show answer without `periods` (100 episodes or fewer) parses to none.
        val plain = ArchiveParser.parseShow(200, show("null")).groups.first()
        assertNull(plain.periods)
        assertTrue(ArchiveRules.yearChips(plain.periods).isEmpty())
    }

    @Test fun chipsCarryWhatTheySelect() {
        val p = ArchivePeriods(listOf(2026, 2005), 2005, listOf(12, 1), 1)
        assertEquals(listOf(2026, 2005), ArchiveRules.yearChips(p).map { it.value })
        assertEquals(listOf(12, 1), ArchiveRules.monthChips(p).map { it.value })
    }

    @Test fun selectYearFollowsTheGoldenCases() {
        for (case in golden.getValue("selectYear").jsonArray) {
            val o = case.jsonObject
            val months = o.getValue("months").jsonArray.map { it.jsonPrimitive.int }
            val name = o.getValue("name").jsonPrimitive.content
            assertEquals(name, o.getValue("month").jsonPrimitive.int, ArchiveRules.selectYear(months))
            // The same rule decides when an answer names a year but no month (or one it does not list).
            val noMonth = ArchiveParser.parseShow(200, show("""{"years": [2026, 2025], "year": 2025, "months": ${months.reversed()}}"""))
            assertEquals("$name, answer without month", o.getValue("month").jsonPrimitive.int, noMonth.groups.first().periods?.month)
        }
        assertNull(ArchiveRules.selectYear(emptyList()))
    }

    /** The gateway's sample (spec/fixtures/dvb/dvb_archive_show_periods.json): a show without seasons, > 100 episodes. */
    @Test fun sharedPeriodsSample() {
        val s = ArchiveParser.parseShow(200, Fixtures.response("dvb/dvb_archive_show_periods.json").body)
        assertTrue(s.seasons.isEmpty())
        val group = s.groups.first(ArchiveRules::isCtGroup)
        assertNull(group.season)
        assertEquals("pages as today", 2, group.nextPage)
        assertEquals(ArchivePeriods(listOf(2026, 2025, 2024), 2026, (10 downTo 1).toList(), 10), group.periods)
        assertEquals(listOf("2026" to true, "2025" to false, "2024" to false), ArchiveRules.yearChips(group.periods).map { it.label to it.selected })
        assertEquals("říjen" to true, ArchiveRules.monthChips(group.periods).first().let { it.label to it.selected })
        val aired = group.episodes.map { it.aired!! }
        assertEquals("that month's episodes, newest first", aired.sortedDescending(), aired)
        assertTrue(aired.all { it.toString().startsWith("2026-10-") })
    }

    @Test fun periodsAreParsedDefensively() {
        fun p(json: String) = ArchiveParser.parseShow(200, show(json)).groups.first().periods
        assertEquals(
            "sorted newest first, duplicates, out-of-range and non-numbers dropped",
            ArchivePeriods(listOf(2026, 2024), 2024, listOf(12, 3), 3),
            p("""{"years": [2024, 2026, 2024, 99999, "2023", 2025.5], "year": 2024, "months": [3, 12, 13, 0, "5"], "month": 3}"""),
        )
        assertNull("the selected year must be listed", p("""{"years": [2026], "year": 2020, "months": [1], "month": 1}"""))
        assertNull("no year", p("""{"years": [2026], "months": [1], "month": 1}"""))
        assertNull("no months", p("""{"years": [2026], "year": 2026, "months": [], "month": 1}"""))
        assertNull("not an object", p("[2026]"))
        assertEquals("an unlisted month is the year's newest", 11, p("""{"years": [2026], "year": 2026, "months": [11, 10], "month": 4}""")?.month)
        val group = ArchiveParser.parseShow(200, show("""{"years": [2026], "year": 2026, "months": [11], "month": 11}""")).groups
        assertTrue("a group with periods is the iVysílání group", ArchiveRules.isCtGroup(group[0].copy(episodes = emptyList())))
        assertEquals(2, group[0].nextPage)
    }

    @Test fun anotherPeriodReplacesThenAppendsAndKeepsOurRecordings() {
        val first = ArchiveParser.parseShow(200, show("""{"years": [2026, 2025], "year": 2026, "months": [11, 10], "month": 11}"""))
        val octoberJson = show("""{"years": [2026, 2025], "year": 2026, "months": [11, 10], "month": 10}""")
            .replace("226000000001", "226000000002").replace("Díl z listopadu", "Díl z října")
        val october = ArchiveParser.parseShow(200, octoberJson)
        val replaced = ArchiveRules.withSeasonPage(first, october, null, append = false)
        assertEquals(listOf("226000000002"), replaced.groups[0].episodes.map { it.idec })
        assertEquals(10, replaced.groups[0].periods?.month)
        assertNotEquals(first.groups[0].periods, replaced.groups[0].periods)
        assertEquals("Nahráno z antény stays", first.groups[1], replaced.groups[1])
        val noPeriods = october.copy(groups = listOf(october.groups[0].copy(periods = null)))
        val appended = ArchiveRules.withSeasonPage(replaced, noPeriods, null, append = true)
        assertEquals(listOf("226000000002", "226000000002"), appended.groups[0].episodes.map { it.idec })
        assertEquals("a later page keeps the period", 10, appended.groups[0].periods?.month)
        assertNull("page 1 without periods drops the chips", ArchiveRules.withSeasonPage(replaced, noPeriods, null, append = false).groups[0].periods)
    }

    @Test fun requestsForAnotherYearOrMonth() {
        val seen = ArrayList<String>()
        val c = DvbClient(DvbConfig("http://10.0.2.2:18098", "dev-key-dev-key-dev-key-dev-key-0000")) { url, _ ->
            seen += url
            HttpResponse(200, show("null"))
        }
        c.archiveShow("ct:10000000009", year = 2025)
        c.archiveShow("ct:10000000009", year = 2025, month = 3)
        c.archiveShow("ct:10000000009", season = "s1", page = 2, year = 2025, month = 3)
        c.archiveShow("ct:10000000009", month = 3)
        c.archiveShow("ct:10000000009", page = 2)
        c.archiveShow("ct:10000000009")
        val base = "http://10.0.2.2:18098/dvb/archive/show/ct:10000000009"
        assertEquals(
            listOf(
                "$base?year=2025&page=1",
                "$base?year=2025&month=3&page=1",
                "$base?season=s1&year=2025&month=3&page=2",
                base, // a month never goes out without its year
                "$base?page=2", // a show without seasons pages too
                base,
            ),
            seen,
        )
    }
}
