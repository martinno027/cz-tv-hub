package cz.cztvhub.core.dvb

import cz.cztvhub.core.CtException
import cz.cztvhub.core.Fixtures
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.double
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/** spec/ad-breaks.md with the golden fixtures in spec/fixtures/ads/. */
class AdBreaksTest {
    private val config = DvbConfig("http://10.0.2.2:8098", "dev-key-dev-key-dev-key-dev-key-0000")
    private val cases = Fixtures.json("ads/skip_cases.json")

    private fun parse(name: String): List<AdBreak> {
        val response = Fixtures.response("ads/$name")
        return DvbParser.parseBreaks(response.status, response.body)
    }

    private fun goldenBreaks(): List<AdBreak> = cases.getValue("breaks").jsonArray.map {
        val o = it.jsonObject
        AdBreak(o.num("start"), o.num("end"), o.num("confidence"))
    }

    private fun JsonObject.num(key: String) = getValue(key).jsonPrimitive.double

    @Test fun doneParsesToGoldenBreaks() {
        assertEquals(goldenBreaks(), parse("breaks_done.json"))
    }

    @Test fun pendingIsEmpty() {
        assertEquals(emptyList<AdBreak>(), parse("breaks_pending.json"))
    }

    @Test fun featureOffIsAnError() {
        try {
            parse("breaks_off.json")
            fail("expected CtException")
        } catch (e: CtException) {
            assertEquals("404", e.status)
        }
    }

    @Test fun garbageIsAnError() {
        try {
            DvbParser.parseBreaks(200, "<html>")
            fail("expected CtException")
        } catch (_: CtException) {
        }
        assertEquals(emptyList<AdBreak>(), DvbParser.parseBreaks(200, """{"status":"done"}"""))
    }

    @Test fun minRemainingMatchesSpec() {
        assertEquals(cases.getValue("minRemainingSeconds").jsonPrimitive.double, AdBreaks.MIN_REMAINING_SECONDS, 0.0)
    }

    @Test fun goldenSkipCases() {
        val breaks = goldenBreaks()
        val all = cases.getValue("cases").jsonArray
        assertTrue(all.isNotEmpty())
        all.forEach {
            val o = it.jsonObject
            val position = o.num("position")
            val expected = o.getValue("skipTo").let { v -> if (v is JsonNull) null else v.jsonPrimitive.doubleOrNull }
            assertEquals("position $position", expected, AdBreaks.skipTarget(breaks, position))
        }
    }

    @Test fun clientSendsBearerKeyToBreaksUrl() {
        val seen = ArrayList<Pair<String, Map<String, String>>>()
        val client = DvbClient(config) { url, headers ->
            seen += url to headers
            Fixtures.response("ads/breaks_done.json")
        }
        assertEquals(goldenBreaks(), client.breaks("abc123"))
        assertEquals("http://10.0.2.2:8098/dvb/recording/abc123/breaks", seen.single().first)
        assertEquals("Bearer dev-key-dev-key-dev-key-dev-key-0000", seen.single().second["Authorization"])
    }
}
