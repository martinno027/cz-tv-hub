package cz.cztvhub.tv.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import cz.cztvhub.core.ChannelPrefs
import cz.cztvhub.core.CtException
import cz.cztvhub.core.Days
import cz.cztvhub.core.ImageRequest
import cz.cztvhub.core.OnAir
import cz.cztvhub.core.OnAirLike
import cz.cztvhub.core.Programme
import cz.cztvhub.core.PspState
import cz.cztvhub.core.dvb.DetailsQuery
import cz.cztvhub.core.dvb.DvbChannel
import cz.cztvhub.core.dvb.DvbEvent
import cz.cztvhub.core.dvb.DvbThumbnails
import cz.cztvhub.core.dvb.ProgrammeDetails
import cz.cztvhub.tv.tvApp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.Duration
import java.time.Instant
import java.time.LocalDate

sealed interface Load<out T> {
    data object Loading : Load<Nothing>
    data class Ready<T>(val value: T) : Load<T>
    data class Failed(val message: String) : Load<Nothing>
}

/** Current and next programme keyed by channel id. */
typealias OnAirMap = Map<String, OnAir>

/** Programmes of one broadcast day keyed by channel id. */
typealias DayPlan = Map<String, List<Programme>>

/** "Anténa" data for the screens; [channels] empty while loading or failed. */
data class DvbState(
    val channels: List<DvbChannel> = emptyList(),
    val onAir: Map<String, OnAirLike<DvbEvent>> = emptyMap(),
    val error: String? = null,
    /** The gateway allows programme thumbnails (`/dvb/status`). */
    val thumbnails: Boolean = false,
)

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val app = application.tvApp
    private val schedules = app.schedules

    /** Null when "Anténa" is not set up: the group is hidden everywhere. */
    private val _dvb = MutableStateFlow<DvbState?>(null)
    val dvb: StateFlow<DvbState?> = _dvb.asStateFlow()

    /** "Anténa" events per day keyed by channel id; a failed day is just empty. */
    private val _dvbPlans = MutableStateFlow<Map<LocalDate, Map<String, List<DvbEvent>>>>(emptyMap())
    val dvbPlans: StateFlow<Map<LocalDate, Map<String, List<DvbEvent>>>> = _dvbPlans.asStateFlow()

    /**
     * Anténa is set up and the Tailscale node is not `Running` yet or the bridge is being checked (spec/apps.md
     * "Start (T21)", "Back in front (T18)"): what Živě and the guide say ("Připojuji se k Tailscale…", or the wait for
     * a login) instead of hiding Anténa or showing the bridge's error; null = nothing.
     */
    val tailnetNotice: StateFlow<String?> =
        combine(app.tailnet.notice, app.dvbSettings.config) { notice, config -> notice?.takeIf { config != null } }
            .stateIn(viewModelScope, SharingStarted.Eagerly,
                app.tailnet.notice.value?.takeIf { app.dvbSettings.config.value != null })

    /** "Kanály": hidden channels and their order on this device (or in the signed-in profile). */
    val channelPrefs: StateFlow<ChannelPrefs> = app.channelPrefs.value

    // Every settings change while signed in is saved in the profile (spec/apps.md "Profil"); signed out, the first
    // one makes the list the TV's own instead of the household default ("Kanály").
    fun updateChannelPrefs(change: (ChannelPrefs) -> ChannelPrefs) {
        if (!app.channelPrefs.update(change)) return
        app.defaults.changed()
        app.profiles.changed()
    }

    fun resetChannelPrefs() {
        if (app.defaults.reset()) app.profiles.changed()
    }

    /** "Náhledy anténních pořadů" on this TV. */
    val antennaThumbnails: StateFlow<Boolean> = app.thumbnailPrefs.on

    fun setAntennaThumbnails(on: Boolean) {
        if (app.thumbnailPrefs.set(on)) app.profiles.changed()
    }

    /** "Profil": the signed-in profile (null = signed out) and its sync state. */
    val profiles get() = app.profiles

    /** Nastavení opened: send a pending change or read a newer profile version, and a newer household default. */
    fun syncProfile() {
        app.profiles.sync()
        app.defaults.refresh()
    }

    /** Thumbnail URL and headers of an Anténa event when the gateway and this TV allow it (spec/apps.md). */
    fun thumbnail(event: DvbEvent): Pair<String, Map<String, String>>? {
        val client = app.dvb()?.client ?: return null
        val id = DvbThumbnails.recordingId(event, _dvb.value?.thumbnails == true, antennaThumbnails.value) ?: return null
        return client.thumbnailUrl(id) to client.config.authHeaders
    }

    /**
     * TMDb facts for the programme page (T15, spec/apps.md), from the gateway only: null without a gateway, for
     * a Poslanecká sněmovna stream, on `404` (details off, unknown programme) or any failure. [day] = the broadcast
     * day of the guide grid the CT programme came from.
     */
    suspend fun details(entry: Entry, day: LocalDate): ProgrammeDetails? {
        val client = app.dvb()?.client ?: return null
        val query = when (entry) {
            is CtEntry -> DetailsQuery.Ct(entry.channel.slug, day, entry.programme.idec)
            is DvbEntry -> DetailsQuery.Dvb(entry.event.id)
            is PspEntry -> return null
        }
        return withContext(Dispatchers.IO) {
            try {
                client.details(query)
            } catch (_: CtException) {
                null
            }
        }
    }

    /** A TMDb poster through the gateway (a gateway path, with the key); memory only like thumbnails. */
    fun poster(path: String): ImageRequest? {
        val client = app.dvb()?.client ?: return null
        return client.posterUrl(path)?.let { ImageRequest(it, client.config.authHeaders, gateway = true) }
    }

    private val _now = MutableStateFlow(Instant.now())
    val now: StateFlow<Instant> = _now.asStateFlow()

    private val _onAir = MutableStateFlow<Load<OnAirMap>>(Load.Loading)
    val onAir: StateFlow<Load<OnAirMap>> = _onAir.asStateFlow()

    /** The broadcast day the guide shows; every open starts on the one containing now (`Days.openDay`). */
    private val _selectedDay = MutableStateFlow(Days.openDay(Instant.now()))
    val selectedDay: StateFlow<LocalDate> = _selectedDay.asStateFlow()

    private val _plans = MutableStateFlow<Map<LocalDate, Load<DayPlan>>>(emptyMap())
    val plans: StateFlow<Map<LocalDate, Load<DayPlan>>> = _plans.asStateFlow()

    /** Poslanecká sněmovna: "is it on now?" per stream id, as shown (stale answers already [PspState.UNKNOWN]). */
    private val _psp = MutableStateFlow(app.psp.states())
    val psp: StateFlow<Map<String, PspState>> = _psp.asStateFlow()

    fun pspState(streamId: String): PspState = _psp.value[streamId] ?: PspState.UNKNOWN

    /**
     * Checks the visible streams one after another, each at most once a minute (spec/apps.md). Called only by Živě
     * and the guide while they are on screen and the app is visible; cancelling it stops all checks.
     */
    suspend fun pollPsp() {
        while (true) {
            val ids = ChannelOrder.visible(channelPrefs.value, emptyList()).mapNotNull { it.psp?.id }
            _psp.value = app.psp.states()
            val next = withContext(Dispatchers.IO) { app.psp.runDue(ids) }
            _psp.value = app.psp.states()
            val wait = next?.let { Duration.between(Instant.now(), it).toMillis() } ?: PSP_IDLE_MS
            // Wake at least every few seconds: the list may change and stale answers must stop showing.
            delay(wait.coerceIn(PSP_MIN_WAIT_MS, PSP_IDLE_MS))
        }
    }

    /**
     * Right before playing a stream (spec/apps.md): an answer younger than 60 s, else one more check of its
     * playlist; [then] gets the state on the main thread.
     */
    fun checkPsp(streamId: String, then: (PspState) -> Unit) {
        viewModelScope.launch {
            val state = withContext(Dispatchers.IO) { app.psp.stateForPlayback(streamId) }
            _psp.value = app.psp.states()
            then(state)
        }
    }

    /** A short Czech message over Živě and the guide (e.g. "Poslanecká sněmovna teď nevysílá."). */
    private val _notice = MutableStateFlow<String?>(null)
    val notice: StateFlow<String?> = _notice.asStateFlow()
    private var noticeJob: Job? = null

    fun showNotice(text: String, ms: Long = NOTICE_MS) {
        noticeJob?.cancel()
        _notice.value = text
        noticeJob = viewModelScope.launch {
            delay(ms)
            _notice.value = null
        }
    }

    /** The Živě screen grabs focus only on first display. */
    var initialFocusDone = false

    private var dayJob: Job? = null

    init {
        viewModelScope.launch {
            while (true) {
                _now.value = Instant.now()
                refreshOnAir()
                refreshDvb()
                delay(30_000)
            }
        }
        selectDay(_selectedDay.value)
        // App start (spec/apps.md "Profil"); the gateway's "signed out" is said once, over any screen.
        app.profiles.sync()
        app.defaults.follow() // the cached household default before the first frame, also offline
        viewModelScope.launch {
            app.profiles.message.collect { text ->
                if (text != null) {
                    showNotice(text, LONG_NOTICE_MS)
                    app.profiles.consumeMessage()
                }
            }
        }
        viewModelScope.launch {
            app.gateway.collect {
                _dvbPlans.value = emptyMap()
                _dvb.value = if (it == null) null else DvbState()
                refreshDvb()
                app.defaults.refresh() // app start and a new gateway
                loadDvbDay(_selectedDay.value)
            }
        }
        // The Tailscale node reached Running after a start, or restarted (same bridge port, so the gateway stays the
        // same): everything from it again, and the Anténa group comes back by itself.
        viewModelScope.launch {
            app.tailnet.reloads.drop(1).collect {
                _dvbPlans.value = emptyMap()
                refreshDvb()
                loadDvbDay(_selectedDay.value)
                app.defaults.refresh()
                app.profiles.sync()
            }
        }
    }

    fun refreshDvb() {
        val repo = app.dvb() ?: return
        viewModelScope.launch {
            val state = withContext(Dispatchers.IO) {
                try {
                    val thumbnails = try {
                        repo.status().thumbnails
                    } catch (_: CtException) {
                        _dvb.value?.thumbnails ?: false
                    }
                    DvbState(repo.channels(), repo.onAir(Instant.now()), thumbnails = thumbnails)
                } catch (e: CtException) {
                    DvbState(_dvb.value?.channels.orEmpty(), _dvb.value?.onAir.orEmpty(), e.userMessage,
                        _dvb.value?.thumbnails ?: false)
                }
            }
            if (app.dvb() === repo) _dvb.value = state
        }
    }

    private fun loadDvbDay(day: LocalDate) {
        val repo = app.dvb() ?: return
        viewModelScope.launch {
            val plan = withContext(Dispatchers.IO) {
                try {
                    repo.day(day)
                } catch (_: CtException) {
                    emptyMap() // days outside the gateway's range answer 400; CT rows still show
                }
            }
            if (app.dvb() === repo) _dvbPlans.update { it + (day to plan) }
        }
    }

    fun refreshOnAir() {
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                try {
                    Load.Ready(schedules.onAir(Instant.now()))
                } catch (e: CtException) {
                    Load.Failed(e.userMessage)
                }
            }
            // Keep showing the last good data if a later refresh fails.
            if (result is Load.Ready || _onAir.value !is Load.Ready) _onAir.value = result
        }
    }

    /** Every open of the guide (spec/apps.md): the grid of the broadcast day containing now, also 00:00–05:00. */
    fun openGuide() {
        _now.value = Instant.now()
        selectDay(Days.openDay(_now.value))
    }

    fun selectDay(day: LocalDate, force: Boolean = false) {
        _selectedDay.value = day
        loadDvbDay(day)
        val existing = _plans.value[day]
        if (!force && existing is Load.Ready) {
            // Re-read through the repository so today's plan refreshes after an hour.
            loadDay(day, showLoading = false)
            return
        }
        loadDay(day, showLoading = true)
    }

    private fun loadDay(day: LocalDate, showLoading: Boolean) {
        dayJob?.cancel()
        if (showLoading) _plans.update { it + (day to Load.Loading) }
        dayJob = viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                try {
                    Load.Ready(schedules.day(day))
                } catch (e: CtException) {
                    Load.Failed(e.userMessage)
                }
            }
            if (result is Load.Ready || showLoading) _plans.update { it + (day to result) }
        }
    }

    private companion object {
        const val PSP_IDLE_MS = 5_000L
        const val PSP_MIN_WAIT_MS = 200L
        const val NOTICE_MS = 3_000L
        const val LONG_NOTICE_MS = 8_000L
    }
}
