package cz.cztvhub.core.profile

import cz.cztvhub.core.ChannelPrefs
import cz.cztvhub.core.Fixtures
import cz.cztvhub.core.HttpResponse
import cz.cztvhub.core.dvb.DvbConfig
import cz.cztvhub.core.dvb.DvbMessages
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
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
import java.io.IOException

class ProfileTest {
    private val root = Fixtures.json("profile_cases.json")

    @Test fun goldenNames() {
        val cases = root.getValue("names").jsonArray
        assertTrue(cases.size >= 10)
        for (case in cases) {
            val c = case.jsonObject
            val name = c.getValue("name").jsonPrimitive.content
            val normalized = ProfileRules.normalizeName(name)
            if (c.getValue("valid").jsonPrimitive.boolean) {
                assertEquals(name, c.getValue("normalized").jsonPrimitive.content, normalized)
                assertEquals(name, c.getValue("key").jsonPrimitive.content, ProfileRules.nameKey(normalized!!))
            } else {
                assertNull(name, normalized)
            }
        }
    }

    @Test fun goldenPasswords() {
        val cases = root.getValue("passwords").jsonArray
        assertTrue(cases.size >= 5)
        for (case in cases) {
            val c = case.jsonObject
            val password = c.getValue("password").jsonPrimitive.content
            assertEquals(password, c.getValue("valid").jsonPrimitive.boolean, ProfileRules.isValidPassword(password))
        }
    }

    private fun strings(element: JsonElement?): List<String> =
        element?.jsonArray.orEmpty().map { it.jsonPrimitive.content }

    @Test fun goldenDocuments() {
        val cases = root.getValue("document").jsonArray
        assertTrue(cases.size >= 3)
        for (case in cases) {
            val c = case.jsonObject
            val name = c.getValue("name").jsonPrimitive.content
            val doc = SettingsDocument.from(c.getValue("incoming"))
            val applied = c.getValue("applied").jsonObject
            val prefs = applied.getValue("channelPrefs").jsonObject
            assertEquals(
                name,
                ChannelPrefs(strings(prefs["order"]), strings(prefs["hidden"]).toSet(), strings(prefs["sourcesOff"]).toSet()),
                doc.channelPrefs,
            )
            assertEquals(name, applied.getValue("thumbnails").jsonPrimitive.boolean, doc.thumbnails)
            val exported = doc.with(thumbnails = c.getValue("changeThumbnails").jsonPrimitive.boolean).toJson()
            assertEquals(name, c.getValue("exported"), exported)
        }
    }

    @Test fun documentSurvivesStorageAndKeepsUnknownSources() {
        val doc = SettingsDocument.decode("""{"v":1,"channelPrefs":{"order":[],"hidden":[],"sourcesOff":["radio","psp"]},"x":[1]}""")
        assertEquals(setOf("psp"), doc.channelPrefs.sourcesOff)
        val switched = doc.with(channelPrefs = doc.channelPrefs.toggleSource("psp").toggleSource("ct"))
        val back = SettingsDocument.decode(switched.encode())
        assertEquals(switched, back)
        assertEquals(listOf("ct", "radio"), strings(back.toJson().getValue("channelPrefs").jsonObject["sourcesOff"]))
        assertEquals(Json.parseToJsonElement("[1]"), back.toJson()["x"])
        // Unreadable storage gives the defaults, never a crash.
        assertEquals(SettingsDocument.of(ChannelPrefs.DEFAULT, true), SettingsDocument.decode("{nope"))
    }

    // Sync: settings are labels in the golden cases.

    private fun label(element: JsonElement?): String? =
        if (element == null || element is JsonNull) null else element.jsonPrimitive.content

    private fun state(element: JsonElement): ProfileSyncState<String?> {
        val o = element.jsonObject
        return ProfileSyncState(
            label(o["profile"]),
            label(o["active"]),
            label(o["local"]),
            o.getValue("pending").jsonPrimitive.boolean,
            label(o["version"])?.toLong(),
        )
    }

    private fun event(o: JsonObject): ProfileEvent<String?> {
        fun s(key: String) = o.getValue(key).jsonPrimitive.content
        fun v() = o.getValue("version").jsonPrimitive.long
        return when (val type = s("type")) {
            "signIn" -> ProfileEvent.SignIn(s("name"), s("settings"), v())
            "create" -> ProfileEvent.Create(s("name"), v())
            "change" -> ProfileEvent.Change(s("settings"))
            "pushed" -> ProfileEvent.Pushed(v())
            "pushFailed" -> ProfileEvent.PushFailed
            "remote" -> ProfileEvent.Remote(s("settings"), v())
            "signOut" -> ProfileEvent.SignOut
            "unauthorized" -> ProfileEvent.Unauthorized
            else -> error("unknown event $type")
        }
    }

    @Test fun goldenSync() {
        val cases = root.getValue("sync").jsonArray
        assertTrue(cases.size >= 7)
        for (case in cases) {
            val c = case.jsonObject
            val name = c.getValue("name").jsonPrimitive.content
            var current = state(c.getValue("start"))
            c.getValue("steps").jsonArray.forEachIndexed { i, step ->
                val s = step.jsonObject
                val result = ProfileSync.reduce(current, event(s.getValue("event").jsonObject))
                assertEquals("$name step $i", state(s.getValue("state")), result.state)
                val message = s["message"]?.jsonPrimitive?.content
                assertEquals("$name step $i message", if (message == "signedOut") ProfileMessage.SIGNED_OUT else null, result.message)
                current = result.state
            }
        }
    }

    @Test fun signedOutEventsAreNoOps() {
        val out = ProfileSyncState.signedOut("A")
        for (event in listOf(ProfileEvent.Pushed(4), ProfileEvent.PushFailed, ProfileEvent.Remote("R", 9), ProfileEvent.SignOut, ProfileEvent.Unauthorized)) {
            val step = ProfileSync.reduce(out, event)
            assertEquals(event.toString(), out, step.state)
            assertNull(event.toString(), step.message)
        }
    }

    // Client

    private class Recorder(private vararg val answers: HttpResponse) : ProfileTransport {
        val calls = mutableListOf<List<Any?>>()
        override fun send(method: String, url: String, headers: Map<String, String>, body: String?): HttpResponse {
            calls += listOf(method, url, headers, body)
            return answers[calls.size - 1]
        }
    }

    private val config = DvbConfig("http://pi.local:8098", "k".repeat(32))

    @Test fun createSendsTheDocumentAndReadsTheSession() {
        val doc = SettingsDocument.decode("""{"v":1,"channelPrefs":{"order":["ct:CH_2"],"hidden":[],"sourcesOff":[]},"thumbnails":false,"future":1}""")
        val transport = Recorder(HttpResponse(201, """{"token":"t0k","name":"Jana","settings":${doc.encode()},"version":1}"""))
        val session = ProfileClient(config, transport).create("Jana", "1234", doc)
        assertEquals(ProfileSession("t0k", "Jana", doc, 1), session)
        val (method, url, headers, body) = transport.calls.single()
        assertEquals("POST", method)
        assertEquals("http://pi.local:8098/dvb/profiles", url)
        assertEquals("Bearer ${"k".repeat(32)}", (headers as Map<*, *>)["Authorization"])
        assertFalse(headers.containsKey(ProfileClient.TOKEN_HEADER))
        val sent = Json.parseToJsonElement(body as String).jsonObject
        assertEquals("1234", sent.getValue("password").jsonPrimitive.content)
        assertEquals(doc.toJson(), sent["settings"])
    }

    @Test fun profileCallsSendTheTokenHeader() {
        val transport = Recorder(
            HttpResponse(200, """{"name":"Jana","settings":{"v":1},"version":7,"updated":"2026-10-24T10:00:00Z"}"""),
            HttpResponse(200, """{"version":8,"updated":"2026-10-24T10:00:01Z"}"""),
            HttpResponse(204, ""),
        )
        val client = ProfileClient(config, transport)
        assertEquals(ProfileSnapshot("Jana", SettingsDocument.decode("""{"v":1}"""), 7), client.get("tok"))
        assertEquals(8L, client.put("tok", SettingsDocument.of(ChannelPrefs.DEFAULT, false)))
        client.logout("tok")
        assertEquals(listOf("GET", "PUT", "POST"), transport.calls.map { it[0] })
        assertEquals(
            listOf("/dvb/profile", "/dvb/profile", "/dvb/profile/logout"),
            transport.calls.map { (it[1] as String).removePrefix(config.baseUrl) },
        )
        for (call in transport.calls) {
            val headers = call[2] as Map<*, *>
            assertEquals("tok", headers[ProfileClient.TOKEN_HEADER])
            // The app key as the bearer header: the gateway refuses the cookie on profile calls (spec/apps.md "Profil").
            assertEquals("Bearer ${"k".repeat(32)}", headers["Authorization"])
            assertFalse(headers.keys.any { (it as String).equals("Cookie", ignoreCase = true) })
        }
        val put = Json.parseToJsonElement(transport.calls[1][3] as String).jsonObject
        assertEquals(false, put.getValue("settings").jsonObject.getValue("thumbnails").jsonPrimitive.boolean)
    }

    private fun failure(response: HttpResponse, call: (ProfileClient) -> Unit): ProfileException {
        try {
            call(ProfileClient(config, Recorder(response)))
        } catch (e: ProfileException) {
            return e
        }
        fail("no exception")
        error("unreachable")
    }

    @Test fun onlyTheProfileCodeSignsOut() {
        val ended = failure(HttpResponse(401, """{"error":"Profil byl odhlášen","code":"profileSignedOut"}""")) { it.get("tok") }
        val badKey = failure(HttpResponse(401, """{"error":"Chybí nebo nesedí klíč"}""")) { it.get("tok") }
        assertTrue(ended.signedOut)
        assertFalse(badKey.signedOut)
        assertEquals("Chybí nebo nesedí klíč", badKey.userMessage)
        assertEquals(DvbMessages.BAD_KEY, failure(HttpResponse(401, "")) { it.put("tok", SettingsDocument.decode(null)) }.userMessage)
    }

    @Test fun formErrorsCarryTheGatewayText() {
        val cases = mapOf(
            401 to "Nesprávné jméno nebo heslo",
            409 to "Profil s tímto jménem už existuje",
            429 to "Příliš mnoho pokusů, zkuste to za minutu",
            400 to "Neplatné jméno",
            507 to "Profilů je už 20",
        )
        for ((status, text) in cases) {
            val e = failure(HttpResponse(status, """{"error":"$text"}""")) { it.login("Jana", "1234") }
            assertEquals(text, e.userMessage)
            assertEquals(status, e.httpStatus)
            assertFalse(e.signedOut)
        }
        assertEquals(DvbMessages.GENERIC, failure(HttpResponse(500, "<html>")) { it.login("Jana", "1234") }.userMessage)
    }

    @Test fun malformedAnswersAndNetworkErrorsFail() {
        assertEquals(DvbMessages.GENERIC, failure(HttpResponse(200, """{"name":"Jana","settings":{}}""")) { it.login("Jana", "1234") }.userMessage)
        assertEquals(DvbMessages.GENERIC, failure(HttpResponse(200, """{"name":"Jana","settings":{},"version":"3"}""")) { it.get("t") }.userMessage)
        assertEquals(DvbMessages.GENERIC, failure(HttpResponse(200, """{"name":"Jana","version":3}""")) { it.get("t") }.userMessage)
        assertEquals(DvbMessages.GENERIC, failure(HttpResponse(200, "[]")) { it.put("t", SettingsDocument.decode(null)) }.userMessage)
        val offline = ProfileTransport { _, _, _, _ -> throw IOException("down") }
        try {
            ProfileClient(config, offline).put("t", SettingsDocument.decode(null))
            fail("no exception")
        } catch (e: ProfileException) {
            assertEquals(DvbMessages.UNREACHABLE, e.userMessage)
            assertFalse(e.signedOut)
            assertNull(e.httpStatus)
        }
    }

    @Test fun documentsWithDifferentSettingsDiffer() {
        val a = SettingsDocument.of(ChannelPrefs.DEFAULT, true)
        val b = SettingsDocument.of(ChannelPrefs.DEFAULT, false)
        assertNotEquals(a, b)
        assertEquals(a, SettingsDocument.decode(a.encode()))
    }
}
