package cz.cztvhub.tv.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import cz.cztvhub.core.ChannelPrefs
import cz.cztvhub.core.CtException
import cz.cztvhub.core.Days
import cz.cztvhub.core.OnAir
import cz.cztvhub.core.OnAirLike
import cz.cztvhub.core.Programme
import cz.cztvhub.core.dvb.DvbChannel
import cz.cztvhub.core.dvb.DvbEvent
import cz.cztvhub.tv.tvApp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
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

    /** "Kanály": hidden channels and their order on this device. */
    val channelPrefs: StateFlow<ChannelPrefs> = app.channelPrefs.value

    fun updateChannelPrefs(change: (ChannelPrefs) -> ChannelPrefs) = app.channelPrefs.update(change)

    fun resetChannelPrefs() = app.channelPrefs.reset()

    private val _now = MutableStateFlow(Instant.now())
    val now: StateFlow<Instant> = _now.asStateFlow()

    private val _onAir = MutableStateFlow<Load<OnAirMap>>(Load.Loading)
    val onAir: StateFlow<Load<OnAirMap>> = _onAir.asStateFlow()

    val today: LocalDate get() = Days.today(_now.value)

    private val _selectedDay = MutableStateFlow(Days.today(Instant.now()))
    val selectedDay: StateFlow<LocalDate> = _selectedDay.asStateFlow()

    private val _plans = MutableStateFlow<Map<LocalDate, Load<DayPlan>>>(emptyMap())
    val plans: StateFlow<Map<LocalDate, Load<DayPlan>>> = _plans.asStateFlow()

    /** The Živě screen grabs focus only on first display. */
    var initialFocusDone = false

    private var dayJob: Job? = null
    private var pickedInitialDay = false

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
        viewModelScope.launch {
            app.dvbSettings.config.collect {
                _dvbPlans.value = emptyMap()
                _dvb.value = if (it == null) null else DvbState()
                refreshDvb()
                loadDvbDay(_selectedDay.value)
            }
        }
    }

    fun refreshDvb() {
        val repo = app.dvb() ?: return
        viewModelScope.launch {
            val state = withContext(Dispatchers.IO) {
                try {
                    DvbState(repo.channels(), repo.onAir(Instant.now()))
                } catch (e: CtException) {
                    DvbState(_dvb.value?.channels.orEmpty(), _dvb.value?.onAir.orEmpty(), e.userMessage)
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
            maybeStartOnYesterday(day, result)
        }
    }

    /** After midnight the programme on air still belongs to yesterday's broadcast day. */
    private fun maybeStartOnYesterday(day: LocalDate, result: Load<DayPlan>) {
        if (pickedInitialDay) return
        pickedInitialDay = true
        val plan = (result as? Load.Ready)?.value ?: return
        val firstStart = plan.values.flatten().minOfOrNull { it.start } ?: return
        if (day == today && _now.value.isBefore(firstStart)) selectDay(day.minusDays(1))
    }
}
