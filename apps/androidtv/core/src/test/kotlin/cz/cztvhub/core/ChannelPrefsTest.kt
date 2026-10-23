package cz.cztvhub.core

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ChannelPrefsTest {
    private val root = Fixtures.json("channel_prefs_cases.json")

    private fun strings(element: JsonElement?): List<String> =
        element?.jsonArray.orEmpty().map { it.jsonPrimitive.content }

    private fun prefs(element: JsonElement): ChannelPrefs {
        val p = element.jsonObject
        return ChannelPrefs(strings(p["order"]), strings(p["hidden"]).toSet(), strings(p["sourcesOff"]).toSet())
    }

    @Test fun goldenArrangeCases() {
        val cases = root.getValue("arrange").jsonArray
        assertTrue(cases.size >= 6)
        for (case in cases) {
            val c = case.jsonObject
            val name = c.getValue("name").jsonPrimitive.content
            val arranged = prefs(c.getValue("prefs")).arrange(strings(c["channels"]))
            assertEquals(name, strings(c["all"]), arranged.all)
            assertEquals(name, strings(c["visible"]), arranged.visible)
        }
    }

    @Test fun goldenMoveCases() {
        val cases = root.getValue("move").jsonArray
        assertTrue(cases.size >= 6)
        for (case in cases) {
            val c = case.jsonObject
            val name = c.getValue("name").jsonPrimitive.content
            val before = prefs(c.getValue("prefs"))
            val after = before.move(c.getValue("key").jsonPrimitive.content, c.getValue("by").jsonPrimitive.int, strings(c["channels"]))
            assertEquals(name, strings(c["order"]), after.order)
            assertEquals(name, before.hidden, after.hidden) // moving never changes visibility
        }
    }

    @Test fun toggleAndRoundTrip() {
        val prefs = ChannelPrefs(listOf("ct:CH_24", "dvb:a")).toggle("ct:CH_1").toggle("dvb:a").toggle("dvb:a")
        assertEquals(setOf("ct:CH_1"), prefs.hidden)
        assertEquals(prefs, ChannelPrefs.decode(prefs.encode()))
    }

    @Test fun sourcesSwitch() {
        val ct = listOf("ct:CH_1", "ct:CH_2")
        val dvb = listOf("dvb:a")
        assertEquals(ct + dvb, ChannelPrefs.DEFAULT.sourceKeys(ct, dvb))
        val off = ChannelPrefs(listOf("dvb:a", "ct:CH_2")).toggleSource(ChannelPrefs.SOURCE_DVB)
        assertEquals(ct, off.sourceKeys(ct, dvb))
        assertEquals(listOf("ct:CH_2", "ct:CH_1"), off.arrange(off.sourceKeys(ct, dvb)).all)
        // moving with the source off keeps the Anténa key stored
        assertEquals(listOf("ct:CH_1", "ct:CH_2", "dvb:a"), off.move("ct:CH_2", 1, off.sourceKeys(ct, dvb)).order)
        assertEquals(off, ChannelPrefs.decode(off.encode()))
        assertEquals(dvb, off.toggleSource(ChannelPrefs.SOURCE_DVB).toggleSource(ChannelPrefs.SOURCE_CT).sourceKeys(ct, dvb))
        assertEquals(emptySet<String>(), ChannelPrefs.decode("""{"sourcesOff": ["dvb", "weird", 3]}""").sourcesOff - "dvb")
    }

    @Test fun unreadableOrCappedInput() {
        for (text in listOf(null, "", "nonsense", "[1,2]", """{"order": 5}""")) {
            assertEquals(text, ChannelPrefs.DEFAULT, ChannelPrefs.decode(text))
        }
        val many = ChannelPrefs((1..800).map { "dvb:$it" })
        assertEquals(ChannelPrefs.MAX_KEYS, ChannelPrefs.decode(many.encode()).order.size)
    }
}
