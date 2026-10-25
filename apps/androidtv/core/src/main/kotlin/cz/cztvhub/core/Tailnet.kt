package cz.cztvhub.core

import cz.cztvhub.core.dvb.DvbParser
import cz.cztvhub.core.dvb.DvbTransport
import cz.cztvhub.core.profile.ProfileTransport
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.longOrNull
import java.io.IOException
import java.io.InterruptedIOException
import java.net.SocketTimeoutException
import java.time.Duration
import java.time.Instant
import java.util.concurrent.atomic.AtomicBoolean

/**
 * How the node reaches the gateway's peer (`Info().gateway`, T18): directly or through a DERP relay ([relay] = the
 * peer's DERP region code), and the last ping's round trip.
 */
data class TailnetGatewayPath(val direct: Boolean, val relay: String = "", val latencyMs: Int? = null)

/**
 * The bridge's `Info()` (spec/apps.md "Tailscale v aplikaci"): Tailscale's backend state as sent; an unknown state stays
 * a plain string and shows as "connecting".
 */
data class TailnetInfo(
    val state: String,
    val loginUrl: String = "",
    val self: String = "",
    val ip: String = "",
    val stateVersion: Long = 0,
    /** Null = unknown (not running, or the gateway is not a peer of the tailnet). */
    val gateway: TailnetGatewayPath? = null,
) {
    companion object {
        /** Null when the text is not the expected JSON object. */
        fun parse(json: String): TailnetInfo? {
            val o = try {
                Json.parseToJsonElement(json).jsonObject
            } catch (_: Exception) {
                return null
            }
            return TailnetInfo(
                o.text("state"),
                o.text("loginURL"),
                o.text("self"),
                o.text("ip"),
                (o["stateVersion"] as? JsonPrimitive)?.longOrNull ?: 0,
                (o["gateway"] as? JsonObject)?.let { g ->
                    TailnetGatewayPath(
                        (g["direct"] as? JsonPrimitive)?.booleanOrNull ?: false,
                        g.text("relay"),
                        (g["latencyMs"] as? JsonPrimitive)?.intOrNull,
                    )
                },
            )
        }

        private fun JsonObject.text(key: String): String =
            (this[key] as? JsonPrimitive)?.takeIf { it.isString }?.content.orEmpty()
    }
}

/** What Nastavení → Server shows for Tailscale (golden cases `tailnet_cases.json` "status"). */
data class TailnetStatus(val line: String, val qr: Boolean, val logout: Boolean, val usesBridge: Boolean) {
    companion object {
        const val OFF = "Vypnuto"
        const val CONNECTING = "Připojuji se k Tailscale…"
        const val SCAN = "Naskenujte QR kód telefonem a přihlaste se do Tailscale."
        const val NEEDS_APPROVAL = "Televize čeká na schválení v administraci Tailscale."

        /** The switch is on but no gateway address is saved, so there is nothing to bridge to. */
        const val NO_TARGET = "chybí adresa brány."

        fun failed(error: String) = "Tailscale se nepodařilo spustit: $error"

        /**
         * "Connection line (T18)" after "Připojeno jako …": " · k bráně přímo, 32 ms", " · přes relé fra, 85 ms";
         * empty when the path is unknown (also when neither direct nor through a named relay).
         */
        fun connectionLine(path: TailnetGatewayPath?): String {
            val route = when {
                path == null -> return ""
                path.direct -> "k bráně přímo"
                path.relay.isNotEmpty() -> "přes relé ${path.relay}"
                else -> return ""
            }
            return " · $route" + (path.latencyMs?.let { ", $it ms" } ?: "")
        }

        /**
         * "Start (T21)": Živě and the guide say [CONNECTING] instead of an empty Anténa group while the switch is on
         * and the node is not `Running` yet (also before it started), or while the bridge is being checked (T18).
         * A failed start shows its error instead.
         */
        fun connecting(enabled: Boolean, info: TailnetInfo?, error: String?, checking: Boolean): Boolean =
            enabled && error == null && (checking || info?.state != TailnetRunningWatch.RUNNING)

        /**
         * [info] is null until the node started; [error] is the start failure. The bridge is used as soon as the
         * node started (before `Running` it answers 502 with the gateway-style error).
         */
        fun of(enabled: Boolean, info: TailnetInfo?, error: String?): TailnetStatus {
            if (!enabled) return TailnetStatus(OFF, qr = false, logout = false, usesBridge = false)
            if (error != null) return TailnetStatus(failed(error), qr = false, logout = false, usesBridge = false)
            if (info == null) return TailnetStatus(CONNECTING, qr = false, logout = false, usesBridge = false)
            val qr = info.state == "NeedsLogin" && info.loginUrl.isNotEmpty()
            val line = when {
                info.state == "Running" -> "Připojeno jako ${info.self}" + (if (info.ip.isEmpty()) "" else " (${info.ip})") +
                    connectionLine(info.gateway)
                qr -> SCAN
                info.state == "NeedsMachineAuth" -> NEEDS_APPROVAL
                else -> CONNECTING
            }
            // A node that has a name has logged in at least once: logging out means something.
            return TailnetStatus(line, qr, logout = info.self.isNotEmpty(), usesBridge = true)
        }
    }
}

/**
 * The bridge itself failed (spec/apps.md "Back in front (T18)"): its own 502, or no answer at all from 127.0.0.1.
 * [dvb] and [profile] wrap the gateway clients' transports; the answer or the error is passed on unchanged.
 */
class BridgeWatch(private val onFailure: () -> Unit) {
    fun dvb(inner: DvbTransport) = DvbTransport { url, headers -> watch { inner.get(url, headers) } }

    fun profile(inner: ProfileTransport) =
        ProfileTransport { method, url, headers, body -> watch { inner.send(method, url, headers, body) } }

    private fun watch(send: () -> HttpResponse): HttpResponse {
        val response = try {
            send()
        } catch (e: IOException) {
            if (isNoAnswer(e)) onFailure()
            throw e
        }
        if (isBridgeFailure(response.status, response.body)) onFailure()
        return response
    }

    companion object {
        /** The bridge's own 502 text (apps/tailnet `bridgeError`): the gateway itself was not reached. */
        const val BRIDGE_ERROR = "Brána není přes Tailscale dostupná."

        /** The bridge's dial to the gateway gives up after this (apps/tailnet `dialTimeout`) and answers its 502. */
        const val BRIDGE_DIAL_TIMEOUT_MS = 8_000

        /** The bridge's own 502, not the gateway's (whose text differs). */
        fun isBridgeFailure(status: Int, body: String): Boolean =
            status == 502 && DvbParser.errorText(body) == BRIDGE_ERROR

        /** A timeout or a connection error; an interrupted request (the caller gave up) is not the bridge's fault. */
        fun isNoAnswer(e: IOException): Boolean = e !is InterruptedIOException || e is SocketTimeoutException
    }
}

/**
 * "Back in front (T18)": the node's connections can die while the app is in the background and its state still says
 * `Running`. When the app comes to the front, or a request through the bridge fails, [run] checks the bridge
 * (`GET /health` through it within [CHECK_TIMEOUT]); not answered → the node restarts with its saved state, its status
 * is read every [RUNNING_POLL] until `Running` (at most [RUNNING_WAIT]), the bridge is checked again and the gateway
 * data reloaded (also after a failed restart, so the screens show the error instead of "connecting" for ever). One
 * check at a time; after a node start no check for [QUIET_AFTER_START]. Blocking: run it off the main thread.
 */
class TailnetRecovery(
    private val clock: () -> Instant = Instant::now,
    private val sleep: (Duration) -> Unit = { Thread.sleep(it.toMillis()) },
) {
    enum class Outcome {
        /** A check runs already, or the node (re)started less than [QUIET_AFTER_START] ago. */
        SKIPPED,

        /** The bridge answered: nothing to do. */
        HEALTHY,

        /** Restarted, and the bridge answers again. */
        RECOVERED,

        /** Restarted (or the restart failed), and the bridge still does not answer. */
        FAILED,
    }

    /** What a check does, supplied by the app (the node and the network live there). All blocking. */
    interface Steps {
        /** `GET /health` through the bridge within [CHECK_TIMEOUT]: true when it answered 200. */
        fun check(): Boolean

        /** Stop + start with the saved state, the bridge on the same port; false when it could not start. */
        fun restart(): Boolean

        /** Reads the node's status: true once `Running`. */
        fun isRunning(): Boolean

        /** Loads the gateway data the screens need again. */
        fun reload()

        /**
         * How many reloads the node's changes to `Running` made so far ([TailnetRunningWatch.reloads], T21): when one
         * came during the check, the check's own reload is left out (one reload, not two).
         */
        fun runningReloads(): Int = 0
    }

    private val busy = AtomicBoolean(false)

    @Volatile
    private var lastStart: Instant? = null

    /** A check (and what follows it) runs: Živě and the guide say "Připojuji se k Tailscale…" instead of hiding Anténa. */
    val checking: Boolean get() = busy.get()

    /** Called with [checking] when a check begins and ends (on the thread running it). */
    var onChecking: (Boolean) -> Unit = {}

    /** The node was started (app start, switch on, new address): it connects by itself first. */
    fun nodeStarted() {
        lastStart = clock()
    }

    fun run(steps: Steps): Outcome {
        if (quiet()) return Outcome.SKIPPED
        if (!busy.compareAndSet(false, true)) return Outcome.SKIPPED
        onChecking(true)
        try {
            if (steps.check()) return Outcome.HEALTHY
            lastStart = clock()
            val reloadsBefore = steps.runningReloads()
            var answered = false
            if (steps.restart()) {
                val deadline = clock().plus(RUNNING_WAIT)
                while (!steps.isRunning() && clock().isBefore(deadline)) sleep(RUNNING_POLL)
                answered = steps.check()
            }
            if (steps.runningReloads() == reloadsBefore) steps.reload()
            return if (answered) Outcome.RECOVERED else Outcome.FAILED
        } finally {
            busy.set(false)
            onChecking(false)
        }
    }

    private fun quiet(): Boolean {
        val start = lastStart ?: return false
        return Duration.between(start, clock()) < QUIET_AFTER_START
    }

    companion object {
        val CHECK_TIMEOUT: Duration = Duration.ofSeconds(4)
        val RUNNING_POLL: Duration = Duration.ofSeconds(2)
        val RUNNING_WAIT: Duration = Duration.ofSeconds(20)
        val QUIET_AFTER_START: Duration = Duration.ofSeconds(30)

        /** `GET <bridge>/health` (the gateway's health, no key needed): only HTTP 200 counts as an answer. */
        fun bridgeAnswers(baseUrl: String, transport: DvbTransport): Boolean = try {
            transport.get("${baseUrl.trimEnd('/')}/health", emptyMap()).status == 200
        } catch (_: IOException) {
            false
        }
    }
}

/**
 * "Start (T21)": after a node start (app start, switch on, new address, restart) the node connects within a few
 * seconds, and the gateway requests made meanwhile got the bridge's 502. Every status read goes through [observe]: a
 * change of the state to `Running` from any other state asks for one reload of the gateway data. [pollInterval] reads
 * the status every [FAST_POLL] until `Running`, at most [FAST_WAIT] after the start. Thread-safe.
 */
class TailnetRunningWatch(private val clock: () -> Instant = Instant::now) {
    private var state: String? = null
    private var startedAt: Instant? = null
    private var count = 0

    /** Reloads asked for by changes to `Running` so far. */
    val reloads: Int @Synchronized get() = count

    /** The node (re)started: its state is unknown again, so the next `Running` is a change. */
    @Synchronized
    fun nodeStarted() {
        state = null
        startedAt = clock()
    }

    /** The node stopped (switch off, logout). */
    @Synchronized
    fun nodeStopped() {
        state = null
        startedAt = null
    }

    /** A status read ([TailnetInfo.state]); true = the state changed to `Running`, so reload the gateway data now. */
    @Synchronized
    fun observe(newState: String): Boolean {
        val changed = newState == RUNNING && state != RUNNING
        state = newState
        if (changed) count++
        return changed
    }

    /** How long until the next status read: [FAST_POLL] after a start until `Running`, else as before. */
    @Synchronized
    fun pollInterval(settingsOpen: Boolean): Duration {
        val start = startedAt
        val starting = state != RUNNING && start != null && Duration.between(start, clock()) < FAST_WAIT
        return when {
            starting -> FAST_POLL
            settingsOpen -> SETTINGS_POLL
            else -> SLOW_POLL
        }
    }

    companion object {
        const val RUNNING = "Running"
        val FAST_POLL: Duration = Duration.ofSeconds(1)
        val FAST_WAIT: Duration = Duration.ofSeconds(20)

        /** Nastavení → Server is open. */
        val SETTINGS_POLL: Duration = Duration.ofSeconds(2)
        val SLOW_POLL: Duration = Duration.ofSeconds(30)
    }
}
