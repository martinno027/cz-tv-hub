package cz.cztvhub.core.dvb

import com.sun.net.httpserver.HttpServer
import cz.cztvhub.core.CtApi
import cz.cztvhub.core.HttpResponse
import cz.cztvhub.core.Planner
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.PspTransport
import cz.cztvhub.core.UrlConnectionTransport
import cz.cztvhub.core.archive.ArchiveSort
import cz.cztvhub.core.profile.ProfileClient
import cz.cztvhub.core.profile.ProfileTransport
import cz.cztvhub.core.profile.SettingsDocument
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.net.InetSocketAddress
import java.time.Instant
import java.time.LocalDate

/**
 * `X-Cztvhub-Client` (T37, spec/dvb-gateway.md "Sign-in log"): only the profile requests and `GET /dvb/status` carry
 * it; never other gateway requests (never playback), never ČT or the Chamber's CDN.
 */
class ClientHeaderTest {
    private val config = DvbConfig("http://10.0.2.2:8098", "k".repeat(32))
    private val value = "androidtv/0.1.0 (42)"

    @Test fun valueIsPlatformVersionAndBuild() {
        assertEquals(value, ClientHeader.value("0.1.0", "42"))
        assertEquals("X-Cztvhub-Client", ClientHeader.NAME)
        // a development build without version or build: just the platform
        assertEquals("androidtv", ClientHeader.value(null, null))
        assertEquals("androidtv", ClientHeader.value("", "42"))
        assertEquals("androidtv/0.1.0", ClientHeader.value("0.1.0", null))
        assertEquals("androidtv/0.1.0", ClientHeader.value("0.1.0", ""))
        // disallowed characters are dropped, the value is cut to 64, so the gateway never ignores it
        assertEquals("androidtv/0.1.0beta (42)", ClientHeader.value("0.1.0;beta\n", "4\"2"))
        val long = ClientHeader.value("1".repeat(80), "42")
        assertEquals(64, long.length)
        assertTrue(long.startsWith("androidtv/111"))
        val allowed = Regex("[A-Za-z0-9 ./()+_-]{1,64}")
        listOf(value, long, ClientHeader.value("ä€ 1.0", "x;y"), ClientHeader.value(null, null)).forEach {
            assertTrue(it, allowed.matches(it))
        }
        assertEquals(mapOf(ClientHeader.NAME to value), ClientHeader.of(value))
        assertEquals(emptyMap<String, String>(), ClientHeader.of(null))
    }

    /** Every DvbClient request with the path it went to and its headers. */
    private fun dvbRequests(client: String?): Map<String, Map<String, String>> {
        val seen = LinkedHashMap<String, Map<String, String>>()
        val dvb = DvbClient(config, client) { url, headers ->
            seen[url.removePrefix(config.baseUrl).substringBefore('?')] = headers
            HttpResponse(500, "{}")
        }
        val calls: List<() -> Unit> = listOf(
            { dvb.status() }, { dvb.channels() }, { dvb.epg(LocalDate.of(2026, 10, 18)) }, { dvb.defaults() },
            { dvb.details(DetailsQuery.Dvb("e1")) }, { dvb.archiveHome() }, { dvb.archiveCategories() },
            { dvb.archiveCategory("filmy", ArchiveSort.NEWEST, null, 1) }, { dvb.archiveSearch("x", 1) },
            { dvb.archiveShow("ct:abc") }, { dvb.breaks("r1") }, { dvb.timeline("b1") },
            { dvb.checkPlaylist(DvbTimeline("b1", "c1", "/dvb/timeline/b1.m3u8", true, Instant.EPOCH, Instant.EPOCH, emptyList(), emptyList(), null, null)) },
        )
        calls.forEach { runCatching(it) }
        assertEquals("every call reached the transport", calls.size, seen.size)
        return seen
    }

    @Test fun dvbClientSendsItOnlyOnStatus() {
        val seen = dvbRequests(value)
        assertEquals(value, seen.getValue("/dvb/status")[ClientHeader.NAME])
        val others = seen - "/dvb/status"
        assertTrue(others.keys.toString(), others.keys.containsAll(listOf("/dvb/channels", "/dvb/epg", "/dvb/archive")))
        others.forEach { (path, headers) -> assertFalse(path, headers.containsKey(ClientHeader.NAME)) }
        // the key goes everywhere, so the variants differ only in the client header
        seen.values.forEach { assertEquals("Bearer ${"k".repeat(32)}", it["Authorization"]) }
        // without a value nothing is sent, also on status
        assertFalse(dvbRequests(null).getValue("/dvb/status").containsKey(ClientHeader.NAME))
    }

    @Test fun everyProfileRequestCarriesIt() {
        val calls = mutableListOf<Pair<String, Map<String, String>>>()
        val transport = ProfileTransport { method, url, headers, _ ->
            calls += "$method ${url.removePrefix(config.baseUrl)}" to headers
            HttpResponse(200, """{"token":"t","name":"Jana","settings":{"v":1},"version":1}""")
        }
        val profiles = ProfileClient(config, transport, value)
        profiles.create("Jana", "1234", SettingsDocument.decode(null))
        profiles.login("Jana", "1234")
        profiles.get("t")
        profiles.put("t", SettingsDocument.decode(null))
        profiles.logout("t")
        assertEquals(
            listOf("POST /dvb/profiles", "POST /dvb/profiles/login", "GET /dvb/profile", "PUT /dvb/profile", "POST /dvb/profile/logout"),
            calls.map { it.first },
        )
        calls.forEach { (call, headers) -> assertEquals(call, value, headers[ClientHeader.NAME]) }
        calls.clear()
        ProfileClient(config, transport).get("t")
        assertFalse(calls.single().second.containsKey(ClientHeader.NAME))
    }

    @Test fun playbackNeverCarriesIt() {
        val dvb = DvbClient(config, value) { _, _ -> throw IOException("no gateway call expected") }
        val api = CtApi { _, _ -> HttpResponse(200, """{"streamUrls":{"main":"https://ct.example/live.m3u8"}}""") }
        val planner = Planner(api, dvb = { dvb })
        val gateway = planner.plan(PlayRequest.DvbLive("c1", "ČT1 HD")).headers
        assertEquals("Bearer ${"k".repeat(32)}", gateway["Authorization"])
        assertFalse(gateway.containsKey(ClientHeader.NAME))
        assertFalse(planner.plan(PlayRequest.DvbRecorded("c1", "x", "r1", fromStart = true)).headers.containsKey(ClientHeader.NAME))
        // ČT and the Chamber: no headers of ours at all
        assertEquals(emptyMap<String, String>(), planner.plan(PlayRequest.Live("CH_1", "ČT1")).headers)
        assertEquals(emptyMap<String, String>(), planner.plan(PlayRequest.PspLive("stream1", "Schůze PS")).headers)
    }

    /** What the real ČT and Chamber transports put on the wire, read by a local server (no network). */
    @Test fun ctAndChamberTransportsNeverSendIt() {
        val received = mutableListOf<Set<String>>()
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") { exchange ->
            synchronized(received) { received += exchange.requestHeaders.keys.map { it.lowercase() }.toSet() }
            exchange.sendResponseHeaders(200, 2)
            exchange.responseBody.use { it.write("{}".toByteArray()) }
        }
        server.start()
        try {
            val url = "http://127.0.0.1:${server.address.port}/x"
            assertEquals(200, UrlConnectionTransport().request(url, null).status)
            assertEquals(200, UrlConnectionTransport().request(url, "{}").status)
            assertEquals(200, PspTransport().request(url, null).status)
        } finally {
            server.stop(0)
        }
        assertEquals(3, received.size)
        received.forEach { headers ->
            assertTrue("a real request was read: $headers", "host" in headers)
            assertFalse(headers.toString(), ClientHeader.NAME.lowercase() in headers)
        }
    }
}
