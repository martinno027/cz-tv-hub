package cz.cztvhub.tv.dvb

import android.content.Context
import android.util.Log
import cz.cztvhub.core.TailnetInfo
import cz.cztvhub.core.TailnetRecovery
import cz.cztvhub.core.TailnetStatus
import cz.cztvhub.core.dvb.DvbConfig
import cz.cztvhub.core.dvb.UrlConnectionDvbTransport
import cz.cztvhub.bridge.tailnet.Node
import cz.cztvhub.bridge.tailnet.Tailnet
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.io.File
import java.util.concurrent.Executors

/**
 * "Připojovat přes Tailscale" (spec/apps.md "Tailscale v aplikaci"): the only place that calls the tailnet bridge
 * (apps/tailnet, gomobile AAR), always on its own thread. The switch is in SharedPreferences `tailnet`; the node's
 * state (its keys) only encrypted by [SecretBox], never as a plain file. [route] tells [cz.cztvhub.tv.TvApp] how to
 * reach the gateway. Main thread.
 */
class TailnetController(context: Context, private val settings: DvbSettings) {
    /** How the app reaches the gateway now. */
    sealed interface Route {
        /** The address from Nastavení → Server as typed (switch off, or the node could not start). */
        data object Direct : Route

        /** The switch is on and the node is starting: no gateway request yet, so none bypasses the tailnet. */
        data object Pending : Route

        data class Bridge(val port: Int) : Route {
            val baseUrl: String get() = "http://127.0.0.1:$port"
        }
    }

    data class State(val enabled: Boolean, val info: TailnetInfo?, val error: String?, val route: Route) {
        val status: TailnetStatus get() = TailnetStatus.of(enabled, info, error)
    }

    private val prefs = context.getSharedPreferences("tailnet", Context.MODE_PRIVATE)
    private val workDir = File(context.cacheDir, "tailnet")
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val bridgeThread = Executors.newSingleThreadExecutor { Thread(it, "tailnet") }.asCoroutineDispatcher()
    private val wake = Channel<Unit>(Channel.CONFLATED)

    /** One start, stop or logout at a time. */
    private val lifecycle = Mutex()

    private var node: Node? = null
    private var nodeTarget: String? = null
    private var savedVersion = 0L

    private val _state = MutableStateFlow(
        State(prefs.getBoolean(ENABLED, false), null, null, if (prefs.getBoolean(ENABLED, false)) Route.Pending else Route.Direct),
    )
    val state: StateFlow<State> = _state.asStateFlow()

    /** "Back in front (T18)": checks of the bridge and restarts of the node. */
    private val recovery = TailnetRecovery()

    private val _checking = MutableStateFlow(false)

    /** The bridge is being checked (or the node restarted): Živě and the guide say "Připojuji se k Tailscale…". */
    val checking: StateFlow<Boolean> = _checking.asStateFlow()

    private val _reloads = MutableStateFlow(0)

    /** Counts restarts of the node: the gateway data the screens need is loaded again on every change. */
    val reloads: StateFlow<Int> = _reloads.asStateFlow()

    /** Nastavení → Server is open: poll every 2 s instead of every 30 s until `Running`. */
    var settingsOpen = false
        set(value) {
            field = value
            wake.trySend(Unit)
        }

    init {
        recovery.onChecking = { _checking.value = it }
        if (_state.value.enabled) scope.launch { lifecycle.withLock { start(null) } }
        scope.launch { pollLoop() }
    }

    /** The switch. Off stops the node and keeps its state, so switching on again needs no login. */
    fun setEnabled(on: Boolean) {
        if (on == _state.value.enabled) return
        prefs.edit().putBoolean(ENABLED, on).apply()
        scope.launch {
            lifecycle.withLock {
                if (on) {
                    _state.value = State(true, null, null, Route.Pending)
                    start(null)
                } else {
                    stop()
                    _state.value = State(false, null, null, Route.Direct)
                }
            }
        }
    }

    /** "Přihlásit klíčem": restarts the node with an auth key, used once and never stored. */
    fun loginWithKey(authKey: String) {
        if (!_state.value.enabled) return
        scope.launch { lifecycle.withLock { start(authKey.trim()) } }
    }

    /** "Odhlásit z Tailscale": logs out, deletes the saved state and turns the switch off. */
    fun logout() {
        prefs.edit().putBoolean(ENABLED, false).apply()
        scope.launch {
            lifecycle.withLock {
                node?.let { n -> withContext(bridgeThread) { runCatching { n.logout() } } }
                stop()
                prefs.edit().remove(STATE).apply()
                withContext(bridgeThread) { workDir.deleteRecursively() }
                _state.value = State(false, null, null, Route.Direct)
            }
        }
    }

    /**
     * "Uložit a ověřit": a new gateway address restarts the bridge to it; then waits (at most [READY_WAIT_MS]) until
     * the node is `Running`, so the check goes through the bridge instead of meeting its 502 right after the start.
     */
    suspend fun ready() {
        lifecycle.withLock {
            if (!_state.value.enabled) return
            if (node == null || nodeTarget != DvbConfig.tailnetTarget(settings.address)) start(null)
            if (node == null) return
        }
        withTimeoutOrNull(READY_WAIT_MS) {
            while (_state.value.info?.state != RUNNING && node != null) {
                poll()
                if (_state.value.info?.state != RUNNING) delay(FAST_POLL_MS)
            }
        }
    }

    private suspend fun start(authKey: String?) {
        stop()
        val target = DvbConfig.tailnetTarget(settings.address)
        if (target == null) {
            _state.value = State(true, null, TailnetStatus.NO_TARGET, Route.Direct)
            return
        }
        _state.value = State(true, null, null, Route.Pending)
        val saved = prefs.getString(STATE, null)?.let(SecretBox::decrypt).orEmpty()
        val started = withContext(bridgeThread) {
            workDir.mkdirs()
            runCatching { Tailnet.start(workDir.path, saved, HOSTNAME, authKey.orEmpty(), target) }
        }
        val n = started.getOrElse { e ->
            _state.value = State(true, null, e.message ?: e.toString(), Route.Direct)
            return
        }
        if (!_state.value.enabled) { // switched off while starting
            withContext(bridgeThread) { n.close() }
            return
        }
        node = n
        nodeTarget = target
        savedVersion = 0 // the store counts changes since this start
        recovery.nodeStarted()
        _state.value = State(true, null, null, Route.Bridge(withContext(bridgeThread) { n.proxyPort().toInt() }))
        poll()
    }

    /**
     * "Back in front (T18)": the app came to the front, or a request through the bridge got no answer from the
     * gateway. The bridge is checked; not answered → the node restarts with its saved state and the gateway data is
     * loaded again. Any thread.
     */
    fun checkBridge() {
        scope.launch {
            val bridge = _state.value.route as? Route.Bridge ?: return@launch
            if (!_state.value.enabled || recovery.checking) return@launch
            val outcome = withContext(Dispatchers.IO) { recovery.run(steps(bridge.baseUrl)) }
            if (outcome != TailnetRecovery.Outcome.HEALTHY && outcome != TailnetRecovery.Outcome.SKIPPED) {
                Log.i(TAG, "bridge check: $outcome")
            }
        }
    }

    /** The recovery's steps, run on an IO thread; what touches the node goes back to the main thread. */
    private fun steps(baseUrl: String) = object : TailnetRecovery.Steps {
        private val health = UrlConnectionDvbTransport(TailnetRecovery.CHECK_TIMEOUT.toMillis().toInt())

        override fun check() = TailnetRecovery.bridgeAnswers(baseUrl, health)

        override fun restart() = runBlocking(Dispatchers.Main.immediate) { restartNode() }

        override fun isRunning() = runBlocking(Dispatchers.Main.immediate) {
            poll()
            _state.value.info?.state == RUNNING
        }

        override fun reload() = _reloads.update { it + 1 }
    }

    /** The node again with its state; the bridge keeps its port, so the gateway URL and a player's URLs stay valid. */
    private suspend fun restartNode(): Boolean = lifecycle.withLock {
        val n = node ?: return@withLock false
        val restarted = withContext(bridgeThread) {
            runCatching {
                n.restart()
                n.proxyPort().toInt()
            }
        }
        if (node !== n) return@withLock false // stopped or started anew meanwhile
        val port = restarted.getOrElse { e ->
            Log.w(TAG, "restart failed: ${e.message ?: e}")
            return@withLock false
        }
        Log.i(TAG, "restarted, bridge on port $port")
        if ((_state.value.route as? Route.Bridge)?.port != port) _state.value = _state.value.copy(route = Route.Bridge(port))
        wake.trySend(Unit) // polling until Running again
        true
    }

    private suspend fun stop() {
        val n = node ?: return
        node = null
        nodeTarget = null
        withContext(bridgeThread) { n.close() }
    }

    /** The app went to the background: save keys that changed after `Running` (polling has stopped by then). */
    fun saveNow() {
        scope.launch { poll() }
    }

    /** Reads the node's state and saves its keys when they changed. */
    private suspend fun poll() {
        val n = node ?: return
        val (info, saved) = withContext(bridgeThread) {
            val info = TailnetInfo.parse(n.info())
            val saved = if (info != null && info.stateVersion != savedVersion) n.savedState() else null
            info to saved
        }
        if (node !== n) return
        if (info != null && saved != null) {
            prefs.edit().putString(STATE, SecretBox.encrypt(saved)).apply()
            savedVersion = info.stateVersion
        }
        _state.value = _state.value.copy(info = info)
    }

    private suspend fun pollLoop() {
        while (true) {
            withTimeoutOrNull(if (settingsOpen) FAST_POLL_MS else SLOW_POLL_MS) { wake.receive() }
            if (node != null && (settingsOpen || _state.value.info?.state != RUNNING)) poll()
        }
    }

    private companion object {
        const val TAG = "Tailnet"
        const val ENABLED = "enabled"
        const val STATE = "state"
        const val HOSTNAME = "televize-androidtv"
        const val RUNNING = "Running"
        const val FAST_POLL_MS = 2_000L
        const val SLOW_POLL_MS = 30_000L
        const val READY_WAIT_MS = 20_000L
    }
}
