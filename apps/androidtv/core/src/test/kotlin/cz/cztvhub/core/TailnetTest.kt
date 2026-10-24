package cz.cztvhub.core

import cz.cztvhub.core.dvb.DvbConfig
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** spec/fixtures/tailnet_cases.json; synthetic values only. */
class TailnetTest {
    private val root = Fixtures.json("tailnet_cases.json")

    @Test fun goldenTargetCases() {
        val cases = root.getValue("target").jsonArray
        assertTrue(cases.size >= 10)
        for (case in cases) {
            val c = case.jsonObject
            val address = c.getValue("address").jsonPrimitive.content
            val expected = c.getValue("target").takeUnless { it is JsonNull }?.jsonPrimitive?.content
            assertEquals(address, expected, DvbConfig.tailnetTarget(address))
        }
    }

    @Test fun targetIgnoresTheSchemePortButNotTheGatewayUrl() {
        // The gateway URL keeps https on 443; the bridge always dials plain HTTP on 8098.
        assertEquals("https://pi.tail1234.ts.net:443", DvbConfig.normalizeAddress("https://pi.tail1234.ts.net"))
        assertEquals("pi.tail1234.ts.net:8098", DvbConfig.tailnetTarget("https://pi.tail1234.ts.net"))
        assertNull(DvbConfig.tailnetTarget("example.com")) // never the internet
    }

    @Test fun goldenStatusCases() {
        val cases = root.getValue("status").jsonArray
        assertTrue(cases.size >= 10)
        for (case in cases) {
            val c = case.jsonObject
            val name = c.getValue("name").jsonPrimitive.content
            val info = c.getValue("info").takeUnless { it is JsonNull }?.let { TailnetInfo.parse(it.toString()) }
            val error = c.getValue("error").takeUnless { it is JsonNull }?.jsonPrimitive?.content
            val status = TailnetStatus.of(c.getValue("enabled").jsonPrimitive.boolean, info, error)
            assertEquals(name, c.getValue("line").jsonPrimitive.content, status.line)
            assertEquals(name, c.getValue("qr").jsonPrimitive.boolean, status.qr)
            assertEquals(name, c.getValue("logout").jsonPrimitive.boolean, status.logout)
            assertEquals(name, c.getValue("usesBridge").jsonPrimitive.boolean, status.usesBridge)
        }
    }

    @Test fun infoParsing() {
        val info = TailnetInfo.parse("""{"state":"NeedsLogin","loginURL":"https://login.tailscale.com/a/x","stateVersion":3}""")
        assertEquals(TailnetInfo("NeedsLogin", "https://login.tailscale.com/a/x", stateVersion = 3), info)
        assertNull(TailnetInfo.parse("not json"))
        assertNull(TailnetInfo.parse("[]"))
        assertEquals(TailnetInfo(""), TailnetInfo.parse("""{"stateVersion":"x","self":null}"""))
    }

    @Test fun notStartedYetIsConnectingWithoutTheBridge() {
        val status = TailnetStatus.of(enabled = true, info = null, error = null)
        assertEquals(TailnetStatus.CONNECTING, status.line)
        assertFalse(status.usesBridge)
    }
}
