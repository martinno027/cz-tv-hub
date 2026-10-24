package cz.cztvhub.core.profile

import cz.cztvhub.core.ChannelPrefs
import cz.cztvhub.core.HttpResponse
import cz.cztvhub.core.dvb.DvbConfig
import cz.cztvhub.core.dvb.DvbMessages
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

class ProfileCoordinatorTest {
    private val own = ChannelPrefs(order = listOf("ct:CH_24"))
    private val profilePrefs = ChannelPrefs(order = listOf("dvb:a"), hidden = setOf("ct:CH_1"))
    private val profileDoc = SettingsDocument.of(profilePrefs, false)

    /** The gateway: answers by "METHOD path"; null = network error. Records requests. */
    private val answers = mutableMapOf<String, ArrayDeque<HttpResponse?>>()
    private val requests = mutableListOf<Triple<String, Map<String, String>, String?>>()

    private fun answer(call: String, vararg responses: HttpResponse?) {
        answers.getOrPut(call) { ArrayDeque() }.addAll(responses)
    }

    private val transport = ProfileTransport { method, url, headers, body ->
        val call = "$method ${url.removePrefix(BASE)}"
        requests += Triple(call, headers, body)
        answers[call]?.removeFirstOrNull() ?: throw IOException("offline")
    }

    private inner class Host : ProfileHost {
        var login: ProfileLogin? = null
        var gateway = true
        var prefs = own
        var thumbnails = true
        val said = mutableListOf<String>()
        var persisted: ProfileSyncState<SettingsDocument>? = null

        override fun login() = login
        override fun saveLogin(login: ProfileLogin?) {
            this.login = login
        }
        override fun client() = if (gateway) ProfileClient(DvbConfig(BASE, "k".repeat(32)), transport) else null
        override fun currentPrefs() = prefs
        override fun currentThumbnails() = thumbnails
        override fun apply(settings: SettingsDocument) {
            prefs = settings.channelPrefs
            thumbnails = settings.thumbnails
        }
        override fun persist(state: ProfileSyncState<SettingsDocument>) {
            persisted = state
        }
        override fun changed(state: ProfileSyncState<SettingsDocument>, saveFailed: Boolean) {}
        override fun say(text: String) {
            said += text
        }
    }

    /** Background work waits in [queued] until [flush]; timers wait until [fire]. */
    private class Runner : ProfileRunner {
        val queued = ArrayDeque<() -> Unit>()
        val timers = mutableListOf<Pair<Long, () -> Unit>>()
        var autoFlush = true

        override fun <T> io(work: () -> T, done: (Result<T>) -> Unit) {
            queued += { done(runCatching(work)) }
            if (autoFlush) flush()
        }

        override fun later(ms: Long, action: () -> Unit): () -> Unit {
            val timer = ms to action
            timers += timer
            return { timers.remove(timer) }
        }

        fun flush() {
            while (queued.isNotEmpty()) queued.removeFirst()()
        }

        fun fire() {
            val due = timers.toList()
            timers.clear()
            due.forEach { it.second() }
        }
    }

    private val host = Host()
    private val runner = Runner()

    private fun coordinator(state: ProfileSyncState<SettingsDocument> = ProfileSyncState.signedOut(SettingsDocument.of(own, true))) =
        ProfileCoordinator(host, runner, state)

    private fun session(token: String, version: Int, settings: SettingsDocument = profileDoc) =
        HttpResponse(200, """{"token":"$token","name":"Jana","settings":${settings.encode()},"version":$version}""")

    private fun signedIn(): ProfileCoordinator {
        answer("POST /dvb/profiles/login", session("tok", 3))
        val c = coordinator()
        c.signIn("Jana", "1234") { assertNull(it) }
        requests.clear()
        return c
    }

    private fun sentPrefs(index: Int): ChannelPrefs =
        SettingsDocument.from(Json.parseToJsonElement(requests[index].third!!).jsonObject["settings"]).channelPrefs

    @Test fun signInAppliesTheProfileAndKeepsTheTvsOwn() {
        answer("POST /dvb/profiles/login", session("tok", 3))
        val c = coordinator()
        var error: String? = "not called"
        c.signIn("  Jana ", "1234") { error = it }
        assertNull(error)
        assertEquals(ProfileLogin("Jana", "tok"), host.login)
        assertEquals(profilePrefs, host.prefs)
        assertFalse(host.thumbnails)
        assertEquals(own, c.state.local?.channelPrefs)
        assertEquals(3L, c.state.version)
        assertEquals("Jana", Json.parseToJsonElement(requests.single().third!!).jsonObject["name"]?.jsonPrimitive?.content)
        assertEquals(c.state, host.persisted)
    }

    @Test fun aWrongPasswordIsShownAndNothingChanges() {
        answer("POST /dvb/profiles/login", HttpResponse(401, """{"error":"Nesprávné jméno nebo heslo"}"""))
        val c = coordinator()
        var error: String? = null
        c.signIn("Jana", "0000") { error = it }
        assertEquals("Nesprávné jméno nebo heslo", error)
        assertNull(host.login)
        assertFalse(c.state.signedIn)
        assertEquals(own, host.prefs)
    }

    @Test fun withoutAGatewayTheFormSaysSo() {
        host.gateway = false
        var error: String? = null
        coordinator().signIn("Jana", "1234") { error = it }
        assertEquals(ProfileCoordinator.NEEDS_GATEWAY, error)
        assertTrue(requests.isEmpty())
    }

    @Test fun changesAreSentOnceAfterTheDebounce() {
        val c = signedIn()
        answer("PUT /dvb/profile", HttpResponse(200, """{"version":4,"updated":"x"}"""))
        host.prefs = host.prefs.toggle("ct:CH_2")
        c.changed()
        host.prefs = host.prefs.toggle("ct:CH_4")
        c.changed()
        assertTrue(requests.isEmpty())
        assertTrue(c.state.pending)
        assertEquals(listOf(ProfileSync.PUSH_DEBOUNCE_MS), runner.timers.map { it.first })
        runner.fire()
        assertEquals("PUT /dvb/profile", requests.single().first)
        assertEquals("tok", requests.single().second[ProfileClient.TOKEN_HEADER])
        assertEquals(host.prefs, sentPrefs(0))
        assertFalse(c.state.pending)
        assertEquals(4L, c.state.version)
        assertEquals(own, c.state.local?.channelPrefs) // the TV's own settings are untouched
    }

    @Test fun aFailedSendStaysPendingAndIsRetried() {
        val c = signedIn()
        host.thumbnails = true
        c.changed()
        runner.fire() // offline
        assertTrue(c.state.pending)
        assertTrue(c.saveFailed)
        answer("PUT /dvb/profile", HttpResponse(200, """{"version":9}"""))
        c.retry()
        assertFalse(c.state.pending)
        assertFalse(c.saveFailed)
        assertEquals(9L, c.state.version)
        assertEquals(2, requests.size)
    }

    @Test fun aChangeDuringASendIsSentAfterIt() {
        val c = signedIn()
        answer("PUT /dvb/profile", HttpResponse(200, """{"version":4}"""), HttpResponse(200, """{"version":5}"""))
        runner.autoFlush = false
        host.prefs = host.prefs.toggle("ct:CH_2")
        c.changed()
        runner.fire() // the first send is under way
        host.prefs = host.prefs.toggle("ct:CH_5")
        c.changed()
        runner.flush() // the first send answers
        assertTrue("still pending after the first send", c.state.pending)
        assertEquals(4L, c.state.version)
        runner.fire()
        runner.flush()
        assertFalse(c.state.pending)
        assertEquals(5L, c.state.version)
        assertEquals(2, requests.size)
        assertNotEquals(sentPrefs(0), sentPrefs(1))
        assertEquals(host.prefs, sentPrefs(1))
    }

    @Test fun syncSendsAPendingChangeInsteadOfReading() {
        val c = coordinator(ProfileSyncState("Jana", profileDoc, SettingsDocument.of(own, true), true, 3))
        host.login = ProfileLogin("Jana", "tok")
        answer("PUT /dvb/profile", HttpResponse(200, """{"version":4}"""))
        c.sync()
        assertEquals(listOf("PUT /dvb/profile"), requests.map { it.first })
        assertFalse(c.state.pending)
    }

    @Test fun syncAppliesANewVersionOnly() {
        val c = signedIn()
        val other = SettingsDocument.of(ChannelPrefs(hidden = setOf("dvb:z")), true)
        answer(
            "GET /dvb/profile",
            HttpResponse(200, """{"name":"Jana","settings":${other.encode()},"version":3}"""),
            HttpResponse(200, """{"name":"Jana","settings":${other.encode()},"version":4}"""),
        )
        c.sync()
        assertEquals(profilePrefs, host.prefs) // the same version: this TV already has it
        c.sync()
        assertEquals(other.channelPrefs, host.prefs)
        assertEquals(4L, c.state.version)
        assertEquals(listOf("GET /dvb/profile", "GET /dvb/profile"), requests.map { it.first })
    }

    @Test fun theProfileCodeSignsOutOnceAndRestoresTheTvsOwn() {
        val c = signedIn()
        answer("GET /dvb/profile", HttpResponse(401, """{"error":"Profil byl odhlášen","code":"profileSignedOut"}"""))
        c.sync()
        assertFalse(c.state.signedIn)
        assertNull(host.login)
        assertEquals(own, host.prefs)
        assertTrue(host.thumbnails)
        assertEquals(listOf(ProfileSync.SIGNED_OUT_TEXT), host.said)
        c.sync() // signed out: nothing more is sent or said
        assertEquals(1, requests.size)
        assertEquals(1, host.said.size)
    }

    @Test fun aBadAppKeyKeepsTheProfile() {
        val c = signedIn()
        answer("PUT /dvb/profile", HttpResponse(401, """{"error":"Chybí nebo nesedí klíč"}"""))
        host.prefs = host.prefs.toggle("ct:CH_2")
        c.changed()
        runner.fire()
        assertTrue(c.state.signedIn)
        assertTrue(c.state.pending)
        assertEquals(ProfileLogin("Jana", "tok"), host.login)
        assertTrue(host.said.isEmpty())
    }

    @Test fun signOutTellsTheGatewayEvenWhenItFails() {
        val c = signedIn()
        host.prefs = host.prefs.toggle("ct:CH_2")
        c.changed() // a pending change is dropped with the session
        var done = false
        c.signOut { done = true } // offline
        assertTrue(done)
        assertEquals("POST /dvb/profile/logout", requests.single().first)
        assertEquals("tok", requests.single().second[ProfileClient.TOKEN_HEADER])
        assertFalse(c.state.signedIn)
        assertNull(host.login)
        assertEquals(own, host.prefs)
        assertTrue(runner.timers.isEmpty())
    }

    @Test fun removingTheGatewaySignsOutAtOnce() {
        val c = signedIn()
        runner.autoFlush = false
        c.signOutLocally()
        assertNull(host.login)
        assertEquals(own, host.prefs)
        assertFalse(c.state.signedIn)
    }

    @Test fun createStartsTheProfileWithTheCurrentSettings() {
        val c = coordinator()
        var error: String? = null
        c.create("Petr", "1234", "1235") { error = it }
        assertEquals(ProfileCoordinator.PASSWORDS_DIFFER, error)
        assertTrue(requests.isEmpty())
        answer("POST /dvb/profiles", HttpResponse(201, """{"token":"t2","name":"Petr","settings":{"v":1},"version":1}"""))
        c.create("Petr", "1234", "1234") { error = it }
        assertNull(error)
        assertEquals(own, sentPrefs(0))
        assertEquals(own, host.prefs)
        assertEquals(own, c.state.local?.channelPrefs)
        assertEquals("Petr", c.state.profile)
        assertEquals(ProfileLogin("Petr", "t2"), host.login)
    }

    @Test fun signedOutChangesStayOnTheTv() {
        val c = coordinator()
        host.prefs = host.prefs.toggle("ct:CH_2")
        c.changed()
        assertTrue(runner.timers.isEmpty())
        assertFalse(c.state.pending)
        assertEquals(host.prefs, c.state.active.channelPrefs)
        c.retry()
        c.sync()
        assertTrue(requests.isEmpty())
    }

    @Test fun restoreAfterRestart() {
        val local = SettingsDocument.of(own, true)
        val active = SettingsDocument.decode("""{"v":1,"future":1}""")
        val signedIn = ProfileCoordinator.restore(ProfileLogin("Jana", "t"), active, local, true, 4, profilePrefs, false)
        assertEquals(ProfileSyncState("Jana", active.with(profilePrefs, false), local, true, 4L), signedIn)
        assertEquals("future survives", Json.parseToJsonElement("1"), signedIn.active.toJson()["future"])
        // The login is gone (lost secure storage): the TV's own settings again.
        assertEquals(ProfileSyncState.signedOut(local), ProfileCoordinator.restore(null, active, local, true, 4, profilePrefs, false))
        assertEquals(
            ProfileSyncState.signedOut(active.with(own, true)),
            ProfileCoordinator.restore(null, active, null, false, null, own, true),
        )
    }

    @Test fun nameAndPasswordAreCheckedBeforeSending() {
        val c = coordinator()
        val errors = mutableListOf<String?>()
        c.signIn("   ", "1234") { errors += it }
        c.signIn("a".repeat(33), "1234") { errors += it }
        c.signIn("Jana", "123") { errors += it }
        c.create("a\tb", "1234", "1234") { errors += it }
        c.create("Petr", "p".repeat(65), "p".repeat(65)) { errors += it }
        assertEquals(
            listOf(
                ProfileCoordinator.BAD_NAME, ProfileCoordinator.BAD_NAME, ProfileCoordinator.BAD_PASSWORD,
                ProfileCoordinator.BAD_NAME, ProfileCoordinator.BAD_PASSWORD,
            ),
            errors,
        )
        assertEquals("Jméno musí mít 1 až 32 znaků.", ProfileCoordinator.BAD_NAME)
        assertEquals("Heslo musí mít 4 až 64 znaků.", ProfileCoordinator.BAD_PASSWORD)
        assertTrue(requests.isEmpty())
        // A valid pair goes out: the name trimmed (NFC), the password exactly as typed.
        answer("POST /dvb/profiles/login", HttpResponse(400, """{"error":"Neplatné jméno"}"""))
        c.signIn(" Jana ", " heslo ") { errors += it }
        assertEquals("Neplatné jméno", errors.last()) // the gateway's 400 text stays the fallback
        val sent = Json.parseToJsonElement(requests.single().third!!).jsonObject
        assertEquals("Jana", sent["name"]?.jsonPrimitive?.content)
        assertEquals(" heslo ", sent["password"]?.jsonPrimitive?.content)
    }

    @Test fun networkErrorsGiveTheUnreachableText() {
        var error: String? = null
        coordinator().signIn("Jana", "1234") { error = it }
        assertEquals(DvbMessages.UNREACHABLE, error)
    }

    private companion object {
        const val BASE = "http://pi.local:8098"
    }
}
