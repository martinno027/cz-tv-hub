package cz.cztvhub.core.dvb

import com.sun.net.httpserver.HttpServer
import cz.cztvhub.core.PlayKind
import cz.cztvhub.core.PlayRequest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.net.InetSocketAddress
import java.time.Instant
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** T53 watch reports (spec/apps.md "Watch reports (T53)", spec/dvb-gateway.md "Watch log"). */
class WatchReporterTest {
    private val key = "k".repeat(32)
    private val config = DvbConfig("http://192.168.1.5:8098", key)
    private val clientValue = "androidtv/0.1.0 (42)"

    private data class Sent(val url: String, val headers: Map<String, String>, val body: JsonObject) {
        val event get() = (body["event"] as JsonPrimitive).content
        val session get() = (body["session"] as JsonPrimitive).content
    }

    /** Runs work at once; [advance] moves a fake clock and fires the repeating ticks that fell due. */
    private class FakeRunner : WatchRunner {
        var now = 0L
        private class Tick(val period: Long, var due: Long, val action: () -> Unit, var cancelled: Boolean = false)
        private val ticks = mutableListOf<Tick>()
        val active get() = ticks.count { !it.cancelled }

        override fun run(work: () -> Unit) = work()

        override fun every(periodMs: Long, action: () -> Unit): () -> Unit {
            val tick = Tick(periodMs, now + periodMs, action)
            ticks += tick
            return { tick.cancelled = true }
        }

        fun advance(ms: Long) {
            val until = now + ms
            while (true) {
                val next = ticks.filter { !it.cancelled && it.due <= until }.minByOrNull { it.due } ?: break
                now = next.due
                next.due += next.period
                next.action()
            }
            now = until
        }
    }

    private val sent = mutableListOf<Sent>()
    private var failing = false
    private val transport = WatchTransport { url, headers, body ->
        sent += Sent(url, headers, Json.parseToJsonElement(body) as JsonObject)
        if (failing) throw IOException("gateway away")
        204
    }
    private val runner = FakeRunner()
    private var enabled: () -> Boolean = { true }
    private var token: String? = "profile-token"
    private var gateway: DvbConfig? = config
    private var ids = 0

    private fun reporter(client: String? = clientValue) = WatchReporter(
        config = { gateway },
        profileToken = { token },
        client = client,
        enabled = { enabled() },
        transport = transport,
        runner = runner,
        newSessionId = { "session-${"%016d".format(++ids)}" },
    )

    private val ct1 = WatchItem.of(PlayRequest.Live("CH_1", "ČT1"), PlayKind.LIVE)
    private val ct2 = WatchItem.of(PlayRequest.Live("CH_2", "ČT2"), PlayKind.LIVE)

    @Test fun startCarriesEveryFieldAndTheHeaders() {
        val archive = PlayRequest.Programme("CH_2", "Návštěvníci", "21738", Instant.EPOCH, Instant.EPOCH, true)
        reporter().begin(WatchItem.of(archive, PlayKind.VOD)) { "Návštěvníci (1/15)" }
        val start = sent.single()
        assertEquals("http://192.168.1.5:8098/dvb/watch", start.url)
        assertEquals(
            mapOf(
                "event" to "start", "session" to "session-0000000000000001", "source" to "ct", "mode" to "archive",
                "channel" to "CH_2", "title" to "Návštěvníci (1/15)", "idec" to "21738",
            ),
            start.body.mapValues { (it.value as JsonPrimitive).content },
        )
        assertEquals("Bearer $key", start.headers["Authorization"])
        assertEquals(clientValue, start.headers[ClientHeader.NAME])
        assertEquals("profile-token", start.headers["X-Cztvhub-Profile"])
        assertEquals("application/json", start.headers["Content-Type"])
    }

    @Test fun signedOutAndDevelopmentBuildSendNeitherHeader() {
        token = null
        reporter(client = null).begin(ct1)
        val headers = sent.single().headers
        assertFalse(headers.containsKey("X-Cztvhub-Profile"))
        assertFalse(headers.containsKey(ClientHeader.NAME))
        assertEquals("Bearer $key", headers["Authorization"])
    }

    @Test fun beatsEverySixtySecondsWithOnlyEventAndSession() {
        reporter().begin(ct1)
        runner.advance(59_999)
        assertEquals(listOf("start"), sent.map { it.event })
        runner.advance(1)
        runner.advance(60_000)
        assertEquals(listOf("start", "beat", "beat"), sent.map { it.event })
        sent.drop(1).forEach {
            assertEquals(setOf("event", "session"), it.body.keys)
            assertEquals(sent[0].session, it.session)
        }
        assertEquals(60_000L, WatchReporter.BEAT_MS)
    }

    @Test fun endStopsTheSessionAndTheBeats() {
        val r = reporter()
        r.begin(ct1)
        runner.advance(60_000)
        r.end()
        assertEquals(listOf("start", "beat", "stop"), sent.map { it.event })
        assertEquals(setOf("event", "session"), sent.last().body.keys)
        assertEquals(sent[0].session, sent.last().session)
        runner.advance(600_000)
        r.end()
        assertEquals(3, sent.size)
        assertEquals(0, runner.active)
    }

    @Test fun anotherChannelStopsTheOldSessionFirstWithANewId() {
        val r = reporter()
        r.begin(ct1)
        runner.advance(30_000)
        r.begin(ct2)
        assertEquals(listOf("start", "stop", "start"), sent.map { it.event })
        assertEquals(sent[0].session, sent[1].session)
        assertNotEquals(sent[0].session, sent[2].session)
        assertEquals("CH_2", (sent[2].body["channel"] as JsonPrimitive).content)
        // the beat follows the new start, not the old timer
        runner.advance(30_000)
        assertEquals(3, sent.size)
        runner.advance(30_000)
        assertEquals("beat", sent.last().event)
        assertEquals(sent[2].session, sent.last().session)
        assertEquals(1, runner.active)
    }

    @Test fun theSameItemAgainKeepsItsSession() {
        val r = reporter()
        r.begin(ct1)
        r.begin(WatchItem.of(PlayRequest.Live("CH_1", "ČT1"), PlayKind.LIVE)) // a re-resolved URL
        assertEquals(listOf("start"), sent.map { it.event })
        // back from the background is a new session
        r.end()
        r.begin(ct1)
        assertEquals(listOf("start", "stop", "start"), sent.map { it.event })
        assertNotEquals(sent[0].session, sent[2].session)
    }

    @Test fun nothingWhileTheLogIsOff() {
        enabled = { false }
        val r = reporter()
        r.begin(ct1)
        runner.advance(300_000)
        r.end()
        assertEquals(emptyList<Sent>(), sent)
    }

    @Test fun switchedOnDuringPlaybackStartsAtTheNextBeat() {
        var on = false
        enabled = { on }
        reporter().begin(ct1) { "Události" }
        runner.advance(60_000)
        assertTrue(sent.isEmpty())
        on = true
        runner.advance(60_000)
        assertEquals(listOf("start"), sent.map { it.event })
        assertEquals("Události", (sent[0].body["title"] as JsonPrimitive).content)
        runner.advance(60_000)
        assertEquals(listOf("start", "beat"), sent.map { it.event })
    }

    @Test fun switchedOffDropsTheSessionAndOnAgainIsANewOne() {
        var on = true
        enabled = { on }
        val r = reporter()
        r.begin(ct1)
        on = false
        runner.advance(120_000)
        assertEquals(listOf("start"), sent.map { it.event })
        on = true
        runner.advance(60_000)
        assertEquals(listOf("start", "start"), sent.map { it.event })
        assertNotEquals(sent[0].session, sent[1].session)
        r.end()
        assertEquals("stop", sent.last().event)
    }

    @Test fun aFailedStatusCheckKeepsTheLastAnswer() {
        var answer: Boolean? = true
        enabled = { answer ?: throw cz.cztvhub.core.CtException("gateway away") }
        reporter().begin(ct1)
        answer = null
        runner.advance(60_000)
        assertEquals(listOf("start", "beat"), sent.map { it.event })
        // never known: off
        sent.clear()
        answer = null
        reporter().begin(ct2)
        runner.advance(60_000)
        assertEquals("only the first reporter's beat", listOf("beat"), sent.map { it.event })
    }

    @Test fun errorsAreIgnoredAndNeverRetried() {
        failing = true
        val r = reporter()
        r.begin(ct1)
        runner.advance(60_000)
        r.end()
        assertEquals("one request each, no retry", listOf("start", "beat", "stop"), sent.map { it.event })
    }

    @Test fun onlyToTheGatewayNeverToCtOrTheChamber() {
        val r = reporter()
        r.begin(WatchItem.of(PlayRequest.PspLive("stream1", "Schůze"), PlayKind.LIVE)) { "Schůze" }
        r.begin(ct1)
        r.begin(WatchItem.of(PlayRequest.DvbLive("c1", "ČT1 HD"), PlayKind.LIVE))
        runner.advance(60_000)
        r.end()
        assertEquals(7, sent.size)
        sent.forEach { assertEquals(config.url("/dvb/watch"), it.url) }
        // a gateway address that is no LAN/tailnet host (it could only be one by mistake) gets nothing
        sent.clear()
        listOf("https://api.ceskatelevize.cz", "https://pspcr-ott-live.ssl.cdn.cra.cz", "https://api.themoviedb.org")
            .forEach { base ->
                gateway = DvbConfig(base, key)
                reporter().apply { begin(ct1); end() }
            }
        gateway = null
        reporter().apply { begin(ct1); end() }
        assertEquals(emptyList<Sent>(), sent)
    }

    @Test fun titleIsCutBlankOrFailingIsLeftOut() {
        val r = reporter()
        r.begin(ct1) { "Á".repeat(250) }
        assertEquals("Á".repeat(200), (sent.last().body["title"] as JsonPrimitive).content)
        r.begin(ct2) { "   " }
        assertFalse(sent.last().body.containsKey("title"))
        r.begin(ct1) { throw IllegalStateException("guide away") }
        assertEquals("start", sent.last().event)
        assertFalse(sent.last().body.containsKey("title"))
        // whole code points: an emoji at the cut is not split
        assertEquals("a".repeat(199) + "📺", WatchReporter.cutTitle("a".repeat(199) + "📺" + "b"))
        assertNull(WatchReporter.cutTitle(null))
    }

    @Test fun beginAndEndOnlyQueueWork() {
        val queued = mutableListOf<() -> Unit>()
        var asked = 0
        val r = WatchReporter(
            config = { config }, profileToken = { null }, client = null,
            enabled = { asked++; true }, transport = transport,
            runner = object : WatchRunner {
                override fun run(work: () -> Unit) { queued += work }
                override fun every(periodMs: Long, action: () -> Unit): () -> Unit = {}
            },
        )
        r.begin(ct1)
        r.end()
        assertEquals("neither the status check nor a request on the caller's thread", 0, asked + sent.size)
        queued.forEach { it() }
        assertEquals(listOf("start", "stop"), sent.map { it.event })
    }

    @Test fun itemsOfEverySource() {
        val t0 = Instant.parse("2026-11-02T18:00:00Z")
        fun of(r: PlayRequest, kind: PlayKind) = WatchItem.of(r, kind)
        assertEquals(WatchItem("ct", "live", "CH_24"), of(PlayRequest.Live("CH_24", "ČT24"), PlayKind.LIVE))
        assertEquals(
            WatchItem("ct", "startover", "CH_1"),
            of(PlayRequest.Startover("CH_1", "Zprávy", t0, idec = "1"), PlayKind.TIMESHIFT),
        )
        val programme = PlayRequest.Programme("CH_1", "Film", "20956", t0, t0.plusSeconds(3600), true)
        assertEquals(WatchItem("ct", "archive", "CH_1", idec = "20956"), of(programme, PlayKind.VOD))
        // the decision rules played it from the rewind buffer (aired < 3 h ago, or still airing)
        assertEquals(WatchItem("ct", "startover", "CH_1"), of(programme, PlayKind.TIMESHIFT))
        assertEquals(WatchItem("psp", "live", "stream1"), of(PlayRequest.PspLive("stream1", "Schůze"), PlayKind.LIVE))
        assertEquals(WatchItem("dvb", "live", "c1"), of(PlayRequest.DvbLive("c1", "ČT1 HD"), PlayKind.LIVE))
        assertEquals(
            WatchItem("dvb", "recording", "c1", recording = "r1"),
            of(PlayRequest.DvbRecorded("c1", "Film", "r1", fromStart = true), PlayKind.VOD),
        )
        assertEquals(
            WatchItem("dvb", "timeline", "c1", recording = "bc1-1792832400-1792834200"),
            of(PlayRequest.DvbRecorded("c1", "Film", "bc1-1792832400-1792834200", fromStart = false), PlayKind.VOD),
        )
    }

    @Test fun sessionIdsAreRandomAndOfTheGatewaysShape() {
        val ids = List(50) { WatchReporter.randomSessionId() }
        ids.forEach { assertTrue(it, Regex("[A-Za-z0-9_-]{16,64}").matches(it)) }
        assertEquals(50, ids.toSet().size)
    }

    @Test fun statusWatchLogFlag() {
        val base = """"enabled": true, "retentionDays": 3, "tuners": []"""
        assertTrue(DvbParser.parseStatus(200, "{$base, \"watchLog\": true}").watchLog)
        assertFalse(DvbParser.parseStatus(200, "{$base, \"watchLog\": false}").watchLog)
        assertFalse("a missing field = false", DvbParser.parseStatus(200, "{$base}").watchLog)
    }

    /** The real transport against a local server (no network): POST, headers and body on the wire; its timeout. */
    @Test fun realTransportPostsAndTimesOut() {
        val received = mutableListOf<Triple<String, String?, String>>()
        val hang = CountDownLatch(1)
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/dvb/watch") { exchange ->
            received += Triple(
                exchange.requestMethod,
                exchange.requestHeaders.getFirst("Authorization"),
                exchange.requestBody.readBytes().toString(Charsets.UTF_8),
            )
            exchange.sendResponseHeaders(204, -1)
            exchange.close()
        }
        server.createContext("/slow") { exchange ->
            hang.await(5, TimeUnit.SECONDS)
            exchange.sendResponseHeaders(204, -1)
            exchange.close()
        }
        server.executor = java.util.concurrent.Executors.newCachedThreadPool()
        server.start()
        try {
            val base = "http://127.0.0.1:${server.address.port}"
            assertEquals(204, UrlConnectionWatchTransport().post("$base/dvb/watch", mapOf("Authorization" to "Bearer x"), """{"event":"beat"}"""))
            assertEquals(listOf(Triple("POST", "Bearer x", """{"event":"beat"}""")), received)
            assertEquals(5_000, UrlConnectionWatchTransport().timeoutMs)
            val began = System.nanoTime()
            val failed = runCatching { UrlConnectionWatchTransport(300).post("$base/slow", emptyMap(), "{}") }
            assertTrue(failed.exceptionOrNull().toString(), failed.exceptionOrNull() is IOException)
            assertTrue("timed out", System.nanoTime() - began < TimeUnit.SECONDS.toNanos(3))
        } finally {
            hang.countDown()
            server.stop(0)
        }
    }

    /** The real runner: a gateway that hangs never holds up the caller. */
    @Test fun realRunnerNeverBlocksTheCaller() {
        val release = CountDownLatch(1)
        val done = CountDownLatch(2)
        val r = WatchReporter(
            config = { config }, profileToken = { null }, client = null, enabled = { true },
            transport = { _, _, _ -> release.await(5, TimeUnit.SECONDS); done.countDown(); 204 },
            runner = ExecutorWatchRunner(),
        )
        val began = System.nanoTime()
        r.begin(ct1)
        r.end()
        assertTrue("begin/end returned at once", System.nanoTime() - began < TimeUnit.MILLISECONDS.toNanos(200))
        release.countDown()
        assertTrue("start and stop still went out", done.await(5, TimeUnit.SECONDS))
    }
}
