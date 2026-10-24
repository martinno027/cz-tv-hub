package cz.cztvhub.core

import cz.cztvhub.core.dvb.DvbClient
import cz.cztvhub.core.dvb.DvbConfig
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class HouseholdDefaultTest {
    private val root = Fixtures.json("household_default_cases.json")

    private fun label(element: JsonElement?): String? =
        if (element == null || element is JsonNull) null else element.jsonPrimitive.content

    private fun state(element: JsonElement): DefaultState<String> {
        val o = element.jsonObject
        return DefaultState(
            o.getValue("own").jsonPrimitive.boolean,
            label(o["local"])!!,
            label(o["default"]),
            o.getValue("version").jsonPrimitive.long,
        )
    }

    private fun event(element: JsonElement): DefaultEvent<String> {
        val o = element.jsonObject
        return when (val type = o.getValue("type").jsonPrimitive.content) {
            "defaults" -> DefaultEvent.Defaults(label(o["settings"]), o.getValue("version").jsonPrimitive.long)
            "change" -> DefaultEvent.Change(label(o["settings"])!!)
            "reset" -> DefaultEvent.Reset
            else -> throw AssertionError("unknown event $type")
        }
    }

    @Test fun goldenMigrate() {
        val cases = root.getValue("migrate").jsonArray
        assertTrue(cases.size >= 5)
        for (case in cases) {
            val c = case.jsonObject
            val name = c.getValue("name").jsonPrimitive.content
            val stored = ChannelPrefs.decode(c.getValue("stored").toString())
            assertEquals(name, c.getValue("own").jsonPrimitive.boolean, HouseholdDefault.isOwn(stored))
        }
    }

    @Test fun goldenSequences() {
        val cases = root.getValue("sequences").jsonArray
        assertTrue(cases.size >= 3)
        for (case in cases) {
            val c = case.jsonObject
            val name = c.getValue("name").jsonPrimitive.content
            var state = state(c.getValue("start"))
            for ((i, step) in c.getValue("steps").jsonArray.withIndex()) {
                val s = step.jsonObject
                state = HouseholdDefault.reduce(state, event(s.getValue("event")), "empty")
                assertEquals("$name, step $i", state(s.getValue("state")), state)
                assertEquals("$name, step $i", s.getValue("effective").jsonPrimitive.content, state.effective("builtin"))
            }
        }
    }

    // --- GET /dvb/defaults

    private fun client(status: Int, body: String, seen: MutableList<String> = ArrayList()) =
        DvbClient(DvbConfig(BASE, "k".repeat(32))) { url, _ ->
            seen += url
            HttpResponse(status, body)
        }

    @Test fun defaultsParse() {
        val seen = ArrayList<String>()
        val d = client(
            200,
            """{"channelPrefs":{"order":["dvb:x","ct:CH_24"],"hidden":["ct:CH_5"],"sourcesOff":["psp"],"new":1},"version":7,"updated":"2026-10-24T10:00:00Z"}""",
            seen,
        ).defaults()
        assertEquals("$BASE/dvb/defaults", seen.single())
        assertEquals(HouseholdDefaults(ChannelPrefs(listOf("dvb:x", "ct:CH_24"), setOf("ct:CH_5"), setOf("psp")), 7), d)
    }

    @Test fun defaultsWithoutAListAreNone() {
        assertEquals(HouseholdDefaults(null, 0), client(200, """{"channelPrefs":null,"version":0,"updated":null}""").defaults())
        assertEquals(HouseholdDefaults(null, 4), client(200, """{"version":4}""").defaults())
    }

    @Test fun badDefaultsAreErrors() {
        for (body in listOf(
            """{"channelPrefs":null}""",
            """{"channelPrefs":null,"version":"3"}""",
            """{"channelPrefs":null,"version":-1}""",
            """{"channelPrefs":null,"version":1.5}""",
            """{"channelPrefs":["ct:CH_1"],"version":1}""",
            "not json",
        )) {
            try {
                client(200, body).defaults()
                fail("accepted $body")
            } catch (_: CtException) {
            }
        }
        try {
            client(404, """{"error":"Nenalezeno"}""").defaults()
            fail("accepted 404")
        } catch (e: CtException) {
            assertEquals("Nenalezeno", e.userMessage)
        }
    }

    // --- HouseholdDefaultSync over a store

    private val d1 = ChannelPrefs(order = listOf("ct:CH_24"), hidden = setOf("ct:CH_5"))
    private val d2 = ChannelPrefs(sourcesOff = setOf("psp"))

    private inner class Tv(own: Boolean = false, store: ChannelPrefs = ChannelPrefs.DEFAULT) {
        var signedIn = false
        var store = store
        var persisted: DefaultState<ChannelPrefs>? = null
        val sync = HouseholdDefaultSync(
            DefaultState(own, if (own) store else ChannelPrefs.DEFAULT, null, 0),
            { signedIn },
            { this.store },
            { next -> (next != this.store).also { this.store = next } },
            { persisted = it },
        )
    }

    @Test fun aFreshTvShowsTheDefaultAndKeepsItCached() {
        val tv = Tv()
        assertTrue(tv.sync.defaults(HouseholdDefaults(d1, 1)))
        assertEquals(d1, tv.store)
        assertEquals(DefaultState(false, ChannelPrefs.DEFAULT, d1, 1), tv.persisted)
        assertFalse("an older version is ignored", tv.sync.defaults(HouseholdDefaults(d2, 1)))
        assertEquals(d1, tv.store)
    }

    @Test fun theFirstChangeStartsFromWhatTheTvShowed() {
        val tv = Tv()
        tv.sync.defaults(HouseholdDefaults(d1, 1))
        tv.store = tv.store.toggle("ct:CH_1")
        tv.sync.changed()
        assertTrue(tv.sync.state.own)
        assertEquals(d1.copy(hidden = d1.hidden + "ct:CH_1"), tv.sync.state.local)
        assertFalse(tv.sync.defaults(HouseholdDefaults(d2, 2)))
        assertNotEquals(d2, tv.store)
        assertEquals(d2, tv.sync.state.default)
        assertTrue("reset follows the default again", tv.sync.reset())
        assertEquals(d2, tv.store)
        assertFalse(tv.sync.state.own)
    }

    @Test fun resetWithoutADefaultIsTheBuiltInOrder() {
        val tv = Tv(own = true, store = d1)
        assertTrue(tv.sync.reset())
        assertEquals(ChannelPrefs.DEFAULT, tv.store)
        assertFalse(tv.sync.state.own)
    }

    @Test fun signedInTheProfileIsShownAndChangesStayInIt() {
        val tv = Tv()
        tv.signedIn = true
        tv.store = d2 // the profile's list
        assertFalse(tv.sync.defaults(HouseholdDefaults(d1, 1)))
        assertEquals(d2, tv.store)
        tv.store = tv.store.toggle("ct:CH_1")
        tv.sync.changed()
        assertFalse("a change in the profile is not the TV's own", tv.sync.state.own)
        // Signing out puts the TV's settings from before back; the TV follows the default fetched meanwhile.
        tv.signedIn = false
        tv.store = ChannelPrefs.DEFAULT
        assertTrue(tv.sync.follow())
        assertEquals(d1, tv.store)
    }

    @Test fun anOwnListIsNotReplacedOnSignOut() {
        val tv = Tv(own = true, store = d2)
        tv.sync.defaults(HouseholdDefaults(d1, 1))
        assertFalse(tv.sync.follow())
        assertEquals(d2, tv.store)
    }

    @Test fun aClearedDefaultGoesBackToTheBuiltInOrder() {
        val tv = Tv()
        tv.sync.defaults(HouseholdDefaults(d1, 1))
        assertTrue(tv.sync.defaults(HouseholdDefaults(null, 2)))
        assertEquals(ChannelPrefs.DEFAULT, tv.store)
        assertNull(tv.sync.state.default)
    }

    @Test fun signedInResetCopiesTheDefaultIntoTheProfile() {
        val tv = Tv(own = true, store = d2)
        tv.sync.defaults(HouseholdDefaults(d1, 1))
        tv.signedIn = true
        tv.store = ChannelPrefs(hidden = setOf("ct:CH_1")) // the profile's list
        assertTrue(tv.sync.reset())
        assertEquals(d1, tv.store)
        assertEquals("the TV's own list is untouched", DefaultState(true, d2, d1, 1), tv.sync.state)
        assertFalse("already the default", tv.sync.reset())
    }

    @Test fun signedInResetWithoutADefaultIsTheBuiltInOrder() {
        val tv = Tv()
        tv.signedIn = true
        tv.store = d2
        assertTrue(tv.sync.reset())
        assertEquals(ChannelPrefs.DEFAULT, tv.store)
        assertFalse(tv.sync.state.own)
    }

    @Test fun anotherGatewayDropsTheCache() {
        val tv = Tv()
        tv.sync.defaults(HouseholdDefaults(d1, 5))
        assertTrue(tv.sync.dropCache())
        assertEquals(ChannelPrefs.DEFAULT, tv.store)
        assertEquals(DefaultState(false, ChannelPrefs.DEFAULT, null, 0), tv.persisted)
        assertTrue("the new gateway's versions start again", tv.sync.defaults(HouseholdDefaults(d2, 1)))
        assertEquals(d2, tv.store)
    }

    @Test fun droppingTheCacheKeepsAnOwnList() {
        val tv = Tv(own = true, store = d2)
        tv.sync.defaults(HouseholdDefaults(d1, 1))
        assertFalse(tv.sync.dropCache())
        assertEquals(d2, tv.store)
        assertNull(tv.sync.state.default)
    }

    private companion object {
        const val BASE = "http://gateway.test:18098"
    }
}
