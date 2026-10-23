package cz.cztvhub.core

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ChannelNumbersTest {
    private val root = Fixtures.json("channel_numbers_cases.json")

    private fun strings(element: JsonElement?): List<String> =
        element?.jsonArray.orEmpty().map { it.jsonPrimitive.content }

    private fun keyOrNull(element: JsonElement?): String? =
        if (element == null || element is JsonNull) null else element.jsonPrimitive.contentOrNull

    @Test fun goldenStepCases() {
        val cases = root.getValue("step").jsonArray
        assertTrue(cases.size >= 6)
        for (case in cases) {
            val c = case.jsonObject
            val name = c.getValue("name").jsonPrimitive.content
            val got = ChannelNumbers.step(strings(c["visible"]), keyOrNull(c["current"]), c.getValue("by").jsonPrimitive.int)
            assertEquals(name, keyOrNull(c["key"]), got)
        }
    }

    @Test fun goldenTypeCases() {
        val cases = root.getValue("type").jsonArray
        assertTrue(cases.size >= 6)
        for (case in cases) {
            val c = case.jsonObject
            val name = c.getValue("name").jsonPrimitive.content
            val got = ChannelNumbers.type(
                c.getValue("buffer").jsonPrimitive.content,
                c.getValue("digit").jsonPrimitive.int,
                c.getValue("count").jsonPrimitive.int,
            )
            assertEquals(name, c.getValue("bufferAfter").jsonPrimitive.content, got.buffer)
            assertEquals(name, c.getValue("commit").jsonPrimitive.boolean, got.commit)
        }
    }

    @Test fun goldenResolveCases() {
        val cases = root.getValue("resolve").jsonArray
        assertTrue(cases.size >= 3)
        for (case in cases) {
            val c = case.jsonObject
            val name = c.getValue("name").jsonPrimitive.content
            val got = ChannelNumbers.resolve(strings(c["visible"]), c.getValue("number").jsonPrimitive.int)
            assertEquals(name, keyOrNull(c["key"]), got)
        }
    }

    @Test fun numberIsThePosition() {
        assertEquals(2, ChannelNumbers.number(listOf("ct:CH_1", "dvb:a"), "dvb:a"))
        assertNull(ChannelNumbers.number(listOf("ct:CH_1"), "ct:CH_2"))
    }
}
