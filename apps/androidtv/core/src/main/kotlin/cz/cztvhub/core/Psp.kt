package cz.cztvhub.core

import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import java.time.Duration
import java.time.Instant

/**
 * A public live stream of the Chamber of Deputies ("Poslanecká sněmovna", spec/apps.md). Live only: played directly
 * from the published [url], no extra headers, never recorded, cached or rewound beyond the player's live window.
 */
data class PspStream(val id: String, val name: String, val url: String) {
    /** The "Kanály" key, `psp:<id>`. */
    val key: String get() = ChannelPrefs.pspKey(id)
}

/** The catalogue, the same in every app (spec/fixtures/psp_cases.json "streams"); no other URLs, no mirrors. */
object PspStreams {
    private const val BASE = "https://pspcr-ott-live.ssl.cdn.cra.cz/channels"

    val all: List<PspStream> = listOf(
        PspStream("stream1", "Schůze PS", "$BASE/ps-stream1/playlist/cze.m3u8"),
        PspStream("stream2", "Tiskové konference", "$BASE/ps-stream2/playlist/cze.m3u8"),
        PspStream("stream3", "Výbory a semináře 1", "$BASE/ps-stream3/playlist/cze.m3u8"),
        PspStream("stream4", "Výbory a semináře 2", "$BASE/ps-stream4/playlist/cze.m3u8"),
        PspStream("stream5", "Výbory a semináře 3", "$BASE/ps-stream5/playlist/cze.m3u8"),
    )

    fun byId(id: String): PspStream? = all.firstOrNull { it.id == id }

    fun byKey(key: String): PspStream? = all.firstOrNull { it.key == key }
}

object PspMessages {
    const val NOT_ON = "Poslanecká sněmovna teď nevysílá."
    const val LIVE = "Přímý přenos"
    const val OFF = "Nevysílá se"
    const val CHECKING = "Zjišťuji…"
    const val SOURCE = "Poslanecká sněmovna PČR"
    /** A stream's guide cell on a day that does not contain now (dimmed). */
    const val LIVE_ONLY_CELL = "Jen přímé přenosy"
    /** Selecting that cell. */
    const val LIVE_ONLY = "Poslanecká sněmovna: jen přímé přenosy, bez záznamů."
}

/** What Živě, the guide and the zap overlay show for a stream. */
enum class PspState { UNKNOWN, LIVE, OFF }

/**
 * "Is it on now?" (spec/apps.md "Poslanecká sněmovna"): only the stream's playlist is read, never a segment.
 * The app runs [PspMonitor.runDue] only while Živě or the guide is on screen.
 */
object PspLive {
    val CHECK_INTERVAL: Duration = Duration.ofSeconds(60)
    val ERROR_INTERVAL: Duration = Duration.ofSeconds(120)

    /** A "live" answer older than this without a confirming check is shown as unknown, never as live. */
    val STALE_AFTER: Duration = Duration.ofSeconds(180)

    /** HTTP 200 and an HLS playlist with at least one media segment or variant; anything else = not on. */
    fun isLive(status: Int, body: String?): Boolean {
        if (status != 200 || body == null) return false
        val text = body.removePrefix("﻿").trimStart()
        if (!text.startsWith("#EXTM3U")) return false
        return text.lineSequence().map(String::trim).any { it.startsWith("#EXTINF") || it.startsWith("#EXT-X-STREAM-INF") }
    }
}

/**
 * Remembers the last check of each stream and decides which one is due next: one after another, each at most once
 * per [PspLive.CHECK_INTERVAL]; after an error (no answer, or a 5xx) [PspLive.ERROR_INTERVAL]. Any answer
 * decides on/off ([PspLive.isLive]); a network error (no answer) keeps the last known state, but [state] never shows
 * "live" older than [PspLive.STALE_AFTER]. Thread-safe.
 */
class PspMonitor(
    private val transport: HttpTransport = PspTransport(),
    private val clock: () -> Instant = Instant::now,
) {
    private data class Record(
        val live: Boolean? = null,
        /** When [live] was last confirmed by an answer from the server. */
        val confirmedAt: Instant? = null,
        val nextCheckAt: Instant = Instant.MIN,
    )

    private val records = HashMap<String, Record>()

    /** The stream of [ids] (in that order) whose check is due first, with its due time; null for none. */
    @Synchronized
    fun next(ids: List<String>): Pair<PspStream, Instant>? = ids
        .mapNotNull(PspStreams::byId)
        .map { it to (records[it.id]?.nextCheckAt ?: Instant.MIN) }
        .minByOrNull { it.second }

    /**
     * Checks the first due stream of [ids] if one is due now (blocking; call off the main thread). Returns when the
     * next check of [ids] is due, null when there is nothing to check.
     */
    fun runDue(ids: List<String>): Instant? = synchronized(runLock) {
        val (stream, due) = next(ids) ?: return null
        if (!due.isAfter(clock())) check(stream)
        next(ids)?.second
    }

    /** One check at a time, even with two callers; separate from the state lock, so reads never wait for I/O. */
    private val runLock = Any()

    /**
     * Right before playback (spec/apps.md): reuses an answer younger than [PspLive.CHECK_INTERVAL], else makes one
     * more check of the same playlist. Blocking; call off the main thread. An unknown id is [PspState.UNKNOWN].
     */
    fun stateForPlayback(id: String): PspState {
        val stream = PspStreams.byId(id) ?: return PspState.UNKNOWN
        synchronized(runLock) {
            val confirmed = synchronized(this) { records[id]?.confirmedAt }
            val now = clock()
            if (confirmed == null || Duration.between(confirmed, now) >= PspLive.CHECK_INTERVAL) check(stream)
        }
        return state(id)
    }

    /** One GET of the stream's playlist; never a segment. */
    fun check(stream: PspStream) {
        val result = try {
            transport.request(stream.url, null)
        } catch (_: IOException) {
            null
        }
        val at = clock()
        synchronized(this) {
            val old = records[stream.id] ?: Record()
            records[stream.id] = if (result == null) {
                old.copy(nextCheckAt = at + PspLive.ERROR_INTERVAL)
            } else {
                // A normal answer (200, the 404 "not processed now", any other 4xx) → 60 s; only a 5xx → 120 s.
                val interval = if (result.status >= 500) PspLive.ERROR_INTERVAL else PspLive.CHECK_INTERVAL
                Record(PspLive.isLive(result.status, result.body), at, at + interval)
            }
        }
    }

    @Synchronized
    fun state(id: String, now: Instant = clock()): PspState {
        val record = records[id] ?: return PspState.UNKNOWN
        val live = record.live ?: return PspState.UNKNOWN
        if (!live) return PspState.OFF
        val confirmed = record.confirmedAt ?: return PspState.UNKNOWN
        return if (Duration.between(confirmed, now) > PspLive.STALE_AFTER) PspState.UNKNOWN else PspState.LIVE
    }

    /** [state] of every catalogue stream. */
    fun states(now: Instant = clock()): Map<String, PspState> = PspStreams.all.associate { it.id to state(it.id, now) }
}

/**
 * Plain GET without any extra or faked header (no User-Agent override, no referer, no key); reads at most
 * [maxBytes] of the playlist. Never used for segments.
 */
class PspTransport(private val timeoutMs: Int = 10_000, private val maxBytes: Int = 256 * 1024) : HttpTransport {
    override fun request(url: String, jsonBody: String?): HttpResponse {
        require(jsonBody == null) { "GET only" }
        val connection = URI(url).toURL().openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = timeoutMs
            connection.readTimeout = timeoutMs
            connection.useCaches = false
            val status = connection.responseCode
            val stream = if (status >= 400) connection.errorStream else connection.inputStream
            // No InputStream.readNBytes: the app's minSdk 26 lacks it.
            val body = stream?.use { input ->
                val out = java.io.ByteArrayOutputStream()
                val buffer = ByteArray(8192)
                while (out.size() < maxBytes) {
                    val n = input.read(buffer, 0, minOf(buffer.size, maxBytes - out.size()))
                    if (n < 0) break
                    out.write(buffer, 0, n)
                }
                out.toString("UTF-8")
            }.orEmpty()
            return HttpResponse(status, body)
        } finally {
            connection.disconnect()
        }
    }
}
