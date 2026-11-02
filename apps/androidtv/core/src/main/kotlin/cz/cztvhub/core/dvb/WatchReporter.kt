package cz.cztvhub.core.dvb

import cz.cztvhub.core.PlayKind
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.profile.ProfileClient
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.net.HttpURLConnection
import java.net.URI
import java.security.SecureRandom
import java.util.Base64
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

/**
 * What is being watched, as `POST /dvb/watch` names it (T53, spec/dvb-gateway.md "Watch log"): [source] `ct` | `psp`
 * | `dvb`, [mode] `live` | `startover` | `archive` | `recording` | `timeline`, [channel] the source's id (`CH_*`, the
 * Chamber's stream id, the gateway's channel id), [idec] for a ČT archive item, [recording] for a gateway recording.
 * The title is not part of it (it is looked up when the session starts).
 */
data class WatchItem(
    val source: String,
    val mode: String,
    val channel: String,
    val idec: String? = null,
    val recording: String? = null,
) {
    companion object {
        /**
         * The item of [request] as it plays now ([kind] of the resolved target): a ČT programme from the rewind
         * buffer is `startover` (also a resume, also an archive item still airing), from iVysílání `archive` with
         * its idec. The app never plays ČT's `timeshift` mode on its own (its live player stays on `main`).
         */
        fun of(request: PlayRequest, kind: PlayKind): WatchItem = when (request) {
            is PlayRequest.Live -> WatchItem(CT, "live", request.channelId)
            is PlayRequest.Startover -> WatchItem(CT, "startover", request.channelId)
            is PlayRequest.Programme ->
                if (kind == PlayKind.VOD) WatchItem(CT, "archive", request.channelId, idec = request.idec)
                else WatchItem(CT, "startover", request.channelId)
            is PlayRequest.PspLive -> WatchItem(PSP, "live", request.channelId)
            is PlayRequest.DvbLive -> WatchItem(DVB, "live", request.channelId)
            is PlayRequest.DvbRecorded -> WatchItem(
                DVB,
                if (TimelineRules.isTimelineId(request.recordingId)) "timeline" else "recording",
                request.channelId,
                recording = request.recordingId,
            )
        }

        const val CT = "ct"
        const val PSP = "psp"
        const val DVB = "dvb"
    }
}

/** Seam for tests: one `POST` with a JSON body; returns the HTTP status. May throw anything (it is ignored). */
fun interface WatchTransport {
    fun post(url: String, headers: Map<String, String>, body: String): Int
}

/** Where the reporter's work runs: [run] one serial background thread, [every] a repeating tick on it. */
interface WatchRunner {
    fun run(work: () -> Unit)

    /** Repeats [action] every [periodMs] (first after [periodMs]) until the returned function is called. */
    fun every(periodMs: Long, action: () -> Unit): () -> Unit
}

/**
 * Watch reports to the household gateway (T53, spec/apps.md "Watch reports (T53)"): `start` when playback begins,
 * `beat` every [BEAT_MS] while the player is open (paused included), `stop` when it closes, the channel changes or
 * the app goes to the background. Only while the gateway's `GET /dvb/status` says `"watchLog": true` ([enabled];
 * a failed check keeps the last answer, which starts as false); a session id is new per start.
 *
 * Fire-and-forget: [begin] and [end] only queue work on [runner]; requests have a 5 s timeout
 * ([UrlConnectionWatchTransport]), are never retried, and every error is ignored, so playback never waits for them.
 * Requests go only to the gateway ([config], a LAN/tailnet host), never to ČT, the Chamber or TMDb.
 *
 * Call [begin] and [end] from one thread (the main thread); everything else happens on [runner]'s thread.
 */
class WatchReporter(
    private val config: () -> DvbConfig?,
    private val profileToken: () -> String?,
    private val client: String?,
    private val enabled: () -> Boolean,
    private val transport: WatchTransport = UrlConnectionWatchTransport(),
    private val runner: WatchRunner,
    private val newSessionId: () -> String = ::randomSessionId,
) {
    /** Main thread: what [begin] was last given and the beat timer. */
    private var current: WatchItem? = null
    private var cancelBeats: (() -> Unit)? = null

    /** Runner thread: the item playing, its title lookup, the session the gateway knows, the last [enabled] answer. */
    private var playing: Pair<WatchItem, () -> String?>? = null
    private var session: String? = null
    private var lastEnabled = false

    /**
     * Playback of [item] began; [title] (the programme shown, looked up on the runner's thread when the session
     * starts) may be null. The same item again (a re-resolve of an expired URL) keeps its session; another one stops
     * the old session first.
     */
    fun begin(item: WatchItem, title: () -> String? = { null }) {
        if (item == current) return
        current = item
        cancelBeats?.invoke()
        runner.run { start(item, title) }
        cancelBeats = runner.every(BEAT_MS) { tick() }
    }

    /** The player closed, the channel changes or the app went to the background. */
    fun end() {
        cancelBeats?.invoke()
        cancelBeats = null
        if (current == null) return
        current = null
        runner.run {
            playing = null
            stopSession()
        }
    }

    private fun start(item: WatchItem, title: () -> String?) {
        stopSession()
        playing = item to title
        if (isEnabled()) startSession(item, title)
    }

    private fun tick() {
        val (item, title) = playing ?: return
        if (!isEnabled()) {
            session = null // the gateway ends sessions itself when the log is switched off
            return
        }
        val id = session
        if (id == null) startSession(item, title) else send(event("beat", id))
    }

    private fun startSession(item: WatchItem, title: () -> String?) {
        val id = newSessionId()
        session = id
        val fields = linkedMapOf(
            "event" to "start", "session" to id, "source" to item.source, "mode" to item.mode, "channel" to item.channel,
        )
        cutTitle(runCatching(title).getOrNull())?.let { fields["title"] = it }
        item.idec?.let { fields["idec"] = it }
        item.recording?.let { fields["recording"] = it }
        send(JsonObject(fields.mapValues { JsonPrimitive(it.value) }).toString())
    }

    private fun stopSession() {
        val id = session ?: return
        session = null
        send(event("stop", id))
    }

    private fun isEnabled(): Boolean {
        lastEnabled = try {
            enabled()
        } catch (_: Exception) {
            lastEnabled
        }
        return lastEnabled
    }

    private fun send(body: String) {
        val gateway = config() ?: return
        val host = runCatching { URI(gateway.baseUrl).host }.getOrNull() ?: return
        if (!DvbConfig.isPrivateHost(host)) return
        val headers = gateway.authHeaders + ("Content-Type" to "application/json") + ClientHeader.of(client) +
            (profileToken()?.let { mapOf(ProfileClient.TOKEN_HEADER to it) } ?: emptyMap())
        try {
            transport.post(gateway.url(PATH), headers, body)
        } catch (_: Exception) {
            // fire-and-forget: the report is lost, playback goes on
        }
    }

    private fun event(name: String, id: String) =
        JsonObject(mapOf("event" to JsonPrimitive(name), "session" to JsonPrimitive(id))).toString()

    companion object {
        const val PATH = "/dvb/watch"
        const val BEAT_MS = 60_000L
        const val TIMEOUT_MS = 5_000
        const val TITLE_MAX = 200

        private val random = SecureRandom()

        /** 22 characters of `[A-Za-z0-9_-]` (16 random bytes, base64url); the gateway takes 16–64. */
        fun randomSessionId(): String {
            val bytes = ByteArray(16).also(random::nextBytes)
            return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
        }

        /** Blank = none; else at most [TITLE_MAX] characters (whole code points). */
        fun cutTitle(title: String?): String? {
            val text = title?.trim()?.takeIf { it.isNotEmpty() } ?: return null
            if (text.codePointCount(0, text.length) <= TITLE_MAX) return text
            return text.substring(0, text.offsetByCodePoints(0, TITLE_MAX))
        }
    }
}

/** [WatchRunner] on one daemon thread, shared by every player of the process. */
class ExecutorWatchRunner(
    private val executor: ScheduledExecutorService = Executors.newSingleThreadScheduledExecutor { r ->
        Thread(r, "watch-reports").apply { isDaemon = true }
    },
) : WatchRunner {
    override fun run(work: () -> Unit) {
        executor.execute { runCatching(work) }
    }

    override fun every(periodMs: Long, action: () -> Unit): () -> Unit {
        val future = executor.scheduleWithFixedDelay({ runCatching(action) }, periodMs, periodMs, TimeUnit.MILLISECONDS)
        return { future.cancel(false) }
    }
}

/** `POST` with [WatchReporter.TIMEOUT_MS] for connect and read; the answer body is not read. */
class UrlConnectionWatchTransport(val timeoutMs: Int = WatchReporter.TIMEOUT_MS) : WatchTransport {
    override fun post(url: String, headers: Map<String, String>, body: String): Int {
        val connection = URI(url).toURL().openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = timeoutMs
            connection.readTimeout = timeoutMs
            connection.instanceFollowRedirects = false
            connection.doOutput = true
            headers.forEach { (k, v) -> connection.setRequestProperty(k, v) }
            connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            return connection.responseCode
        } finally {
            connection.disconnect()
        }
    }
}
