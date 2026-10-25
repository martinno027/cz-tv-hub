package cz.cztvhub.core.archive

import cz.cztvhub.core.CtException
import cz.cztvhub.core.Fixtures
import cz.cztvhub.core.HttpResponse
import cz.cztvhub.core.dvb.DvbClient
import cz.cztvhub.core.dvb.DvbConfig
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/** The Archiv menu (T23): golden `archive_cases.json` "menu" and the categories answer. */
class ArchiveMenuTest {
    private fun JsonObject.str(key: String): String? =
        this[key]?.takeIf { it !is JsonNull }?.jsonPrimitive?.content

    private fun assertState(where: String, expected: JsonObject, state: MenuState) {
        assertEquals("$where level", expected.str("level"), if (state.isSubMenu) "sub" else "main")
        assertEquals("$where items", expected.getValue("items").jsonArray.map { it.jsonPrimitive.content }, state.items.map { it.key })
        assertEquals("$where selected", expected.str("selected"), state.selected.key)
        assertEquals("$where sub-menu category", expected.str("category"), state.category?.id)
        assertEquals("$where title", expected.str("title"), state.title)
        val grid = expected.getValue("grid").jsonObject
        val shown = when (val g = state.grid) {
            GridContent.Continue -> mapOf("show" to "continue")
            GridContent.Recordings -> mapOf("show" to "recordings")
            GridContent.Search -> mapOf("show" to "search")
            is GridContent.Category -> mapOf("show" to "category", "category" to g.slug, "genre" to g.genre)
        }
        assertEquals("$where grid", grid.keys.associateWith { grid.str(it) }, shown)
    }

    @Test fun goldenMenuCases() {
        val cases = Fixtures.json("archive_cases.json").getValue("menu").jsonArray
        assertTrue(cases.size >= 6)
        var steps = 0
        for (c in cases.map { it.jsonObject }) {
            val name = c.str("name")!!
            val categories = c.getValue("categories").jsonArray.map {
                val o = it.jsonObject
                ArchiveCategory(o.str("id")!!, o.str("title")!!)
            }
            val genres = c["genres"]?.jsonObject?.mapValues { (_, v) -> v.jsonArray.map { g ->
                val o = g.jsonObject
                ArchiveGenre(o.str("id")!!, o.str("title")!!)
            } }.orEmpty()
            var state: MenuState? = null
            for ((index, s) in c.getValue("steps").jsonArray.map { it.jsonObject }.withIndex()) {
                val where = "$name, step $index"
                val step: MenuStep = when (s.str("do")) {
                    "open" -> MenuStep.Show(ArchiveMenu.open(categories, c.getValue("continueEntries").jsonPrimitive.int))
                    "ok" -> {
                        val current = state ?: error("$where: no state")
                        val item = current.items.firstOrNull { it.key == s.str("on") } ?: error("$where: no item ${s.str("on")}")
                        ArchiveMenu.ok(current, item)
                    }
                    "back" -> ArchiveMenu.back(state ?: error("$where: no state"))
                    else -> error("$where: unknown step ${s.str("do")}")
                }
                steps++
                if (s["leave"]?.jsonPrimitive?.boolean == true) {
                    assertEquals(where, MenuStep.Leave, step)
                    continue
                }
                assertTrue("$where: expected a state, got $step", step is MenuStep.Show)
                step as MenuStep.Show
                assertEquals("$where focus", s.str("focus") == "searchField", step.focusSearchField)
                // The category answer brings the sub-genres (the screen calls this when it arrives).
                val shown = step.state.category?.let { ArchiveMenu.withGenres(step.state, it.id, genres[it.id].orEmpty()) } ?: step.state
                assertState(where, s.getValue("state").jsonObject, shown)
                state = shown
            }
        }
        assertTrue("every step ran", steps >= 14)
    }

    private val films = ArchiveCategory("3947-filmy", "Filmy")
    private val series = ArchiveCategory("3976-serialy", "Seriály")
    private val krimi = ArchiveGenre("4012", "Krimi")

    private fun show(step: MenuStep): MenuState = (step as MenuStep.Show).state

    @Test fun focusSettlingChangesTheGridButOpensNoSubMenu() {
        val open = ArchiveMenu.open(listOf(films, series), continueEntries = 0)
        val settled = ArchiveMenu.settle(open, MenuItem.Category(series))
        assertEquals(GridContent.Category("3976-serialy"), settled.grid)
        assertFalse(settled.isSubMenu)
        assertEquals(GridContent.Search, ArchiveMenu.settle(open, MenuItem.Search).grid)
        val sub = ArchiveMenu.withGenres(show(ArchiveMenu.ok(open, MenuItem.Category(films))), films.id, listOf(krimi))
        assertEquals("resting on ‹ changes nothing", sub, ArchiveMenu.settle(sub, MenuItem.Back))
        assertEquals(GridContent.Category(films.id, krimi.id), ArchiveMenu.settle(sub, MenuItem.Genre(krimi)).grid)
        assertEquals("an item not in the menu changes nothing", open, ArchiveMenu.settle(open, MenuItem.Genre(krimi)))
    }

    @Test fun sortIsKeptInOneCategoryAndResetForAnother() {
        val open = ArchiveMenu.open(listOf(films, series), continueEntries = 0)
        assertEquals(ArchiveSort.POPULAR, open.sort)
        assertEquals(listOf(ArchiveSort.POPULAR, ArchiveSort.NEWEST, ArchiveSort.AZ), open.sorts)
        val az = ArchiveMenu.withSort(open, ArchiveSort.AZ)
        assertNotEquals(open, az)
        var sub = ArchiveMenu.withGenres(show(ArchiveMenu.ok(az, MenuItem.Category(films))), films.id, listOf(krimi))
        assertEquals(ArchiveSort.AZ, sub.sort)
        sub = show(ArchiveMenu.ok(sub, MenuItem.Genre(krimi)))
        assertEquals("kept in a sub-genre", ArchiveSort.AZ, sub.sort)
        val main = show(ArchiveMenu.back(sub))
        assertEquals(ArchiveSort.AZ, main.sort)
        assertEquals("reset for another category", ArchiveSort.POPULAR, ArchiveMenu.settle(main, MenuItem.Category(series)).sort)
        val recordings = ArchiveMenu.settle(main, MenuItem.Recordings)
        assertEquals(listOf(ArchiveSort.NEWEST, ArchiveSort.AZ), recordings.sorts)
        assertEquals(ArchiveSort.NEWEST, recordings.sort)
        assertEquals("no popular for our recordings", recordings, ArchiveMenu.withSort(recordings, ArchiveSort.POPULAR))
        assertTrue(ArchiveMenu.settle(main, MenuItem.Continue).sorts.isEmpty())
        assertTrue(ArchiveMenu.settle(main, MenuItem.Search).sorts.isEmpty())
    }

    @Test fun subGenresOnlyForTheOpenCategoryAndOnlyOnce() {
        val open = ArchiveMenu.open(listOf(films, series), continueEntries = 0)
        assertEquals("not in a sub-menu", open, ArchiveMenu.withGenres(open, films.id, listOf(krimi)))
        val sub = show(ArchiveMenu.ok(open, MenuItem.Category(films)))
        assertEquals(sub, ArchiveMenu.withGenres(sub, series.id, listOf(krimi)))
        val listed = ArchiveMenu.withGenres(sub, films.id, listOf(krimi, krimi))
        assertEquals(listOf("back", "all", "4012"), listed.items.map { it.key })
        assertEquals("a sub-genre's answer does not replace them", listed, ArchiveMenu.withGenres(listed, films.id, emptyList()))
    }

    @Test fun withoutCategoriesTheTabOpensOnOurRecordings() {
        val open = ArchiveMenu.open(emptyList(), continueEntries = 0)
        assertEquals(MenuItem.Recordings, open.selected)
        assertEquals(listOf("search", "continue", "recordings"), open.items.map { it.key })
        assertEquals(MenuItem.Continue, ArchiveMenu.open(emptyList(), continueEntries = 3).selected)
        assertEquals(listOf("search", "continue", "recordings", films.id), ArchiveMenu.withCategories(open, listOf(films)).items.map { it.key })
    }

    @Test fun backInTheSearchClearsFirst() {
        assertEquals(SearchBack.CLEAR, ArchiveMenu.searchBack("mo"))
        assertEquals(SearchBack.MENU, ArchiveMenu.searchBack(""))
    }

    // -- GET /dvb/archive/categories ---------------------------------------------------------------------------

    @Test fun parsesCategoriesDefensively() {
        val list = ArchiveParser.parseCategories(200, """
            {"categories": [
              {"id": "3947-filmy", "title": "Filmy"},
              {"id": "../admin", "title": "Špatné"},
              {"id": "3976-serialy", "title": "  "},
              {"id": "recordings", "title": "Naše nahrávky"},
              {"id": "3947-filmy", "title": "Znovu"},
              {"title": "bez id"},
              "nonsense",
              {"id": "4732-archiv", "title": "Archiv"}]}
        """.trimIndent())
        assertEquals(listOf(ArchiveCategory("3947-filmy", "Filmy"), ArchiveCategory("4732-archiv", "Archiv")), list)
        assertTrue(ArchiveParser.parseCategories(200, "{}").isEmpty())
        try {
            ArchiveParser.parseCategories(404, """{"error": "not found"}""")
            fail("404 is an error")
        } catch (e: CtException) {
            assertEquals("not found", e.userMessage)
        }
    }

    @Test fun clientAsksForTheCategoriesWithTheKey() {
        val seen = ArrayList<Pair<String, Map<String, String>>>()
        val config = DvbConfig("http://10.0.2.2:18098", "dev-key-dev-key-dev-key-dev-key-0000")
        val client = DvbClient(config) { url, headers ->
            seen += url to headers
            HttpResponse(200, """{"categories": [{"id": "3947-filmy", "title": "Filmy"}]}""")
        }
        assertEquals(listOf(films), client.archiveCategories())
        assertEquals("http://10.0.2.2:18098/dvb/archive/categories", seen.single().first)
        assertEquals("Bearer ${config.key}", seen.single().second["Authorization"])
    }

    @Test fun sharedCategories() {
        val list = ArchiveParser.parseCategories(200, Fixtures.response("dvb/dvb_archive_categories.json").body)
        assertEquals(14, list.size)
        assertEquals(ArchiveCategory("3947-filmy", "Filmy"), list.first())
        assertEquals("ČT's Archiv last", "Archiv", list.last().title)
    }
}
