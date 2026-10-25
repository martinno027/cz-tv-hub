package cz.cztvhub.core

import cz.cztvhub.core.dvb.DvbConfig
import cz.cztvhub.core.dvb.DvbTransport
import cz.cztvhub.core.dvb.GATEWAY_TIMEOUT_MS
import cz.cztvhub.core.dvb.UrlConnectionDvbTransport
import cz.cztvhub.core.profile.UrlConnectionProfileTransport
import cz.cztvhub.core.profile.ProfileTransport
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertSame
import org.junit.Assert.fail
import org.junit.Test
import java.io.IOException
import java.io.InterruptedIOException
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.time.Duration
import java.time.Instant

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
        assertTrue(cases.size >= 14)
        assertTrue(cases.count { "gateway" in it.jsonObject.getValue("info").let { i -> if (i is JsonNull) emptyMap() else i.jsonObject } } >= 4)
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

    @Test fun gatewayPathParsing() {
        val info = TailnetInfo.parse("""{"state":"Running","gateway":{"direct":false,"relay":"fra","latencyMs":85}}""")
        assertEquals(TailnetGatewayPath(direct = false, relay = "fra", latencyMs = 85), info?.gateway)
        assertNull(TailnetInfo.parse("""{"state":"Running","gateway":null}""")?.gateway)
        assertNull(TailnetInfo.parse("""{"state":"Running"}""")?.gateway)
        // Odd values stay unknown instead of guessed.
        assertEquals(TailnetGatewayPath(false), TailnetInfo.parse("""{"gateway":{"direct":"yes","latencyMs":"x"}}""")?.gateway)
        assertEquals("", TailnetStatus.connectionLine(TailnetGatewayPath(false)))
    }

    @Test fun bridgeFailureIsOnlyTheBridgesOwn502() {
        val bridge = """{"error": "Brána není přes Tailscale dostupná."}"""
        assertTrue(BridgeWatch.isBridgeFailure(502, bridge))
        assertFalse(BridgeWatch.isBridgeFailure(502, """{"error": "TVHeadend neodpovídá."}""")) // the gateway's 502
        assertFalse(BridgeWatch.isBridgeFailure(503, bridge))
        assertFalse(BridgeWatch.isBridgeFailure(502, "Bad Gateway"))
    }

    @Test fun watchedTransportsReportBridgeFailuresAndPassAnswersOn() {
        var failures = 0
        val watch = BridgeWatch { failures++ }
        val bridge502 = HttpResponse(502, """{"error": "Brána není přes Tailscale dostupná."}""")
        val ok = HttpResponse(200, "[]")

        assertSame(ok, watch.dvb { _, _ -> ok }.get("u", emptyMap()))
        assertEquals(0, failures)
        assertSame(bridge502, watch.dvb { _, _ -> bridge502 }.get("u", emptyMap()))
        assertEquals(1, failures)
        assertSame(bridge502, watch.profile { _, _, _, _ -> bridge502 }.send("PUT", "u", emptyMap(), "{}"))
        assertEquals(2, failures)

        for (error in listOf(SocketTimeoutException("read timed out"), ConnectException("refused"), IOException("reset"))) {
            val throwing = watch.dvb { _, _ -> throw error }
            try {
                throwing.get("u", emptyMap())
                fail("expected $error")
            } catch (e: IOException) {
                assertSame(error, e)
            }
        }
        assertEquals(5, failures)

        // The caller gave up (thread interrupted): not the bridge's fault.
        val cancelled: ProfileTransport = watch.profile { _, _, _, _ -> throw InterruptedIOException() }
        try {
            cancelled.send("GET", "u", emptyMap(), null)
            fail("expected an error")
        } catch (_: InterruptedIOException) {
        }
        assertEquals(5, failures)
    }

    @Test fun gatewayClientsWaitLongerThanTheBridgeDials() {
        // Otherwise a dead tailnet shows a client timeout instead of the bridge's own 502 and its text.
        assertEquals(10_000, GATEWAY_TIMEOUT_MS)
        assertTrue(GATEWAY_TIMEOUT_MS > BridgeWatch.BRIDGE_DIAL_TIMEOUT_MS)
        assertEquals(GATEWAY_TIMEOUT_MS, UrlConnectionDvbTransport().timeoutMs)
        assertEquals(GATEWAY_TIMEOUT_MS, UrlConnectionProfileTransport().timeoutMs)
    }

    @Test fun healthCountsOnly200() {
        val asked = mutableListOf<String>()
        fun answering(status: Int) = DvbTransport { url, _ -> asked += url; HttpResponse(status, "") }
        assertTrue(TailnetRecovery.bridgeAnswers("http://127.0.0.1:4711", answering(200)))
        assertEquals("http://127.0.0.1:4711/health", asked.last())
        assertFalse(TailnetRecovery.bridgeAnswers("http://127.0.0.1:4711/", answering(502)))
        assertEquals("http://127.0.0.1:4711/health", asked.last())
        assertFalse(TailnetRecovery.bridgeAnswers("http://127.0.0.1:4711", answering(401)))
        assertFalse(TailnetRecovery.bridgeAnswers("http://127.0.0.1:4711", DvbTransport { _, _ -> throw SocketTimeoutException() }))
    }

    /** Steps with scripted answers; every step is logged and moves the fake clock as the real ones would. */
    private class Script(
        val clock: FakeClock,
        var checks: MutableList<Boolean>,
        var restartOk: Boolean = true,
        /** isRunning answers false this many times, then true. */
        var notRunningFor: Int = 0,
    ) : TailnetRecovery.Steps {
        val log = mutableListOf<String>()
        var inside: (() -> Unit)? = null

        override fun check(): Boolean {
            log += "check"
            inside?.invoke()
            return checks.removeAt(0)
        }

        override fun restart(): Boolean {
            log += "restart"
            return restartOk
        }

        override fun isRunning(): Boolean {
            log += "running?"
            return notRunningFor-- <= 0
        }

        override fun reload() {
            log += "reload"
        }

        /** Reloads made by the node's change to `Running` meanwhile (T21). */
        var runningReloads = 0

        override fun runningReloads() = runningReloads
    }

    private class FakeClock {
        var now: Instant = Instant.parse("2026-10-24T18:00:00Z")
        val sleeps = mutableListOf<Duration>()
        fun sleep(d: Duration) {
            sleeps += d
            now = now.plus(d)
        }
    }

    private fun recovery(clock: FakeClock) = TailnetRecovery(clock = { clock.now }, sleep = clock::sleep)

    @Test fun healthyBridgeDoesNothing() {
        val clock = FakeClock()
        val steps = Script(clock, mutableListOf(true))
        assertEquals(TailnetRecovery.Outcome.HEALTHY, recovery(clock).run(steps))
        assertEquals(listOf("check"), steps.log)
    }

    @Test fun deadBridgeRestartsPollsUntilRunningChecksAndReloads() {
        val clock = FakeClock()
        val steps = Script(clock, mutableListOf(false, true), notRunningFor = 2)
        val r = recovery(clock)
        val flags = mutableListOf<Boolean>()
        r.onChecking = { flags += it }
        assertEquals(TailnetRecovery.Outcome.RECOVERED, r.run(steps))
        assertEquals(listOf("check", "restart", "running?", "running?", "running?", "check", "reload"), steps.log)
        assertEquals(listOf(Duration.ofSeconds(1), Duration.ofSeconds(1)), clock.sleeps) // 1 s since T21
        assertEquals(listOf(true, false), flags)
        assertFalse(r.checking)
    }

    @Test fun waitForRunningEndsAfter20Seconds() {
        val clock = FakeClock()
        val steps = Script(clock, mutableListOf(false, false), notRunningFor = Int.MAX_VALUE)
        assertEquals(TailnetRecovery.Outcome.FAILED, recovery(clock).run(steps))
        assertEquals(List(20) { Duration.ofSeconds(1) }, clock.sleeps) // 1 s steps up to 20 s (T21)
        assertEquals(listOf("check", "restart") + List(21) { "running?" } + listOf("check", "reload"), steps.log)
    }

    @Test fun failedRestartStillReloads() {
        val clock = FakeClock()
        val steps = Script(clock, mutableListOf(false), restartOk = false)
        assertEquals(TailnetRecovery.Outcome.FAILED, recovery(clock).run(steps))
        assertEquals(listOf("check", "restart", "reload"), steps.log)
    }

    @Test fun quietFor30SecondsAfterAnyNodeStart() {
        val clock = FakeClock()
        val r = recovery(clock)
        r.nodeStarted()
        clock.now = clock.now.plusSeconds(29)
        val steps = Script(clock, mutableListOf(true, true))
        assertEquals(TailnetRecovery.Outcome.SKIPPED, r.run(steps))
        assertEquals(emptyList<String>(), steps.log)
        clock.now = clock.now.plusSeconds(1)
        assertEquals(TailnetRecovery.Outcome.HEALTHY, r.run(steps))

        // A restart is a node start too.
        val dead = Script(clock, mutableListOf(false, true))
        assertEquals(TailnetRecovery.Outcome.RECOVERED, r.run(dead))
        clock.now = clock.now.plusSeconds(10)
        assertEquals(TailnetRecovery.Outcome.SKIPPED, r.run(Script(clock, mutableListOf(false))))
        clock.now = clock.now.plusSeconds(30)
        assertNotEquals(TailnetRecovery.Outcome.SKIPPED, r.run(Script(clock, mutableListOf(true))))
    }

    @Test fun oneCheckAtATime() {
        val clock = FakeClock()
        val r = recovery(clock)
        val steps = Script(clock, mutableListOf(true))
        var nested: TailnetRecovery.Outcome? = null
        steps.inside = {
            assertTrue(r.checking)
            nested = r.run(Script(clock, mutableListOf(true)))
        }
        assertEquals(TailnetRecovery.Outcome.HEALTHY, r.run(steps))
        assertEquals(TailnetRecovery.Outcome.SKIPPED, nested)
        assertFalse(r.checking)
    }

    @Test fun aReloadByTheChangeToRunningReplacesTheChecksOwn() {
        val clock = FakeClock()
        val steps = Script(clock, mutableListOf(false, true), notRunningFor = 1)
        // The status read that sees Running reloads (T21), so the check itself does not.
        val r = recovery(clock)
        val withWatch = object : TailnetRecovery.Steps by steps {
            override fun isRunning(): Boolean = steps.isRunning().also { if (it) steps.runningReloads++ }
            override fun runningReloads() = steps.runningReloads
        }
        assertEquals(TailnetRecovery.Outcome.RECOVERED, r.run(withWatch))
        assertEquals(listOf("check", "restart", "running?", "running?", "check"), steps.log)

        // Running not reached in time: no change to Running, so the check reloads as before.
        clock.now = clock.now.plusSeconds(31)
        val late = Script(clock, mutableListOf(false, false), notRunningFor = Int.MAX_VALUE)
        assertEquals(TailnetRecovery.Outcome.FAILED, r.run(late))
        assertEquals("reload", late.log.last())
    }

    private fun watch(clock: FakeClock) = TailnetRunningWatch(clock = { clock.now })

    @Test fun coldStartReloadsOnceWhenTheNodeReachesRunning() {
        // Seen on Apple TV: the app starts again, the first requests get the bridge's 502 and the T18 check is
        // quiet after the start; the change to Running is what brings Anténa back.
        val clock = FakeClock()
        val recovery = recovery(clock)
        val w = watch(clock)
        recovery.nodeStarted()
        w.nodeStarted()
        assertEquals(TailnetRecovery.Outcome.SKIPPED, recovery.run(Script(clock, mutableListOf(false))))
        assertFalse(w.observe("NoState"))
        assertFalse(w.observe("Starting"))
        clock.now = clock.now.plusSeconds(4)
        assertTrue(w.observe("Running"))
        assertFalse(w.observe("Running")) // at most once per change
        assertFalse(w.observe("Running"))
        assertEquals(1, w.reloads)
    }

    @Test fun everyChangeToRunningReloads() {
        val clock = FakeClock()
        val w = watch(clock)
        // Already Running at the first read after the start: still a change (from not started).
        w.nodeStarted()
        assertTrue(w.observe("Running"))
        // A later reconnect.
        assertFalse(w.observe("Starting"))
        assertTrue(w.observe("Running"))
        // A restart (T18), even when no other state is seen in between.
        w.nodeStarted()
        assertTrue(w.observe("Running"))
        // Switch off and on again.
        w.nodeStopped()
        w.nodeStarted()
        assertFalse(w.observe("NeedsLogin"))
        assertFalse(w.observe("NeedsMachineAuth"))
        assertTrue(w.observe("Running"))
        assertEquals(4, w.reloads)
    }

    @Test fun pollsEverySecondAfterAStartUntilRunningAtMost20Seconds() {
        val clock = FakeClock()
        val w = watch(clock)
        assertEquals(Duration.ofSeconds(30), w.pollInterval(settingsOpen = false)) // never started
        w.nodeStarted()
        assertEquals(Duration.ofSeconds(1), w.pollInterval(settingsOpen = false))
        assertEquals(Duration.ofSeconds(1), w.pollInterval(settingsOpen = true))
        w.observe("Starting")
        clock.now = clock.now.plusSeconds(19)
        assertEquals(Duration.ofSeconds(1), w.pollInterval(settingsOpen = false))
        clock.now = clock.now.plusSeconds(1)
        assertEquals(Duration.ofSeconds(30), w.pollInterval(settingsOpen = false))
        assertEquals(Duration.ofSeconds(2), w.pollInterval(settingsOpen = true))

        // Running ends the fast reads at once.
        w.nodeStarted()
        clock.now = clock.now.plusSeconds(3)
        w.observe("Running")
        assertEquals(Duration.ofSeconds(30), w.pollInterval(settingsOpen = false))
        assertEquals(Duration.ofSeconds(2), w.pollInterval(settingsOpen = true))

        // A stopped node is not polled fast.
        w.nodeStarted()
        w.nodeStopped()
        assertEquals(Duration.ofSeconds(30), w.pollInterval(settingsOpen = false))
    }

    @Test fun screenNoticeWhileTheSwitchIsOnAndTheNodeIsNotRunning() {
        val running = TailnetInfo("Running", self = "televize")
        val connecting = "Připojuji se k Tailscale…"
        val login = "Tailscale čeká na přihlášení (Nastavení → Server)."
        assertNull(TailnetStatus.screenNotice(false, null, null, checking = false))
        assertNull(TailnetStatus.screenNotice(false, null, null, checking = true))
        assertEquals(connecting, TailnetStatus.screenNotice(true, null, null, checking = false)) // not started yet
        assertEquals(connecting, TailnetStatus.screenNotice(true, TailnetInfo("Starting"), null, checking = false))
        assertEquals(connecting, TailnetStatus.screenNotice(true, TailnetInfo("NoState"), null, checking = false))
        assertEquals(login, TailnetStatus.screenNotice(true, TailnetInfo("NeedsLogin", "https://login.example/a"), null, false))
        assertEquals(login, TailnetStatus.screenNotice(true, TailnetInfo("NeedsLogin"), null, checking = false))
        assertEquals(login, TailnetStatus.screenNotice(true, TailnetInfo("NeedsMachineAuth"), null, checking = false))
        assertNull(TailnetStatus.screenNotice(true, running, null, checking = false))
        assertEquals(connecting, TailnetStatus.screenNotice(true, running, null, checking = true)) // T18
        assertNull(TailnetStatus.screenNotice(true, null, TailnetStatus.NO_TARGET, checking = false))
    }

    @Test fun restartWaitPollsEverySecond() {
        assertEquals(Duration.ofSeconds(1), TailnetRecovery.RUNNING_POLL)
        assertEquals(Duration.ofSeconds(20), TailnetRecovery.RUNNING_WAIT)
    }
}
