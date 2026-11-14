package cz.cztvhub.tv

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * "Počasí podle polohy této televize" (Nastavení → Server, T59): asked once on Domů (Povolit / Nepoužívat), then the
 * switch. Only the choice is stored on this TV, never a location; not part of a profile (it is about this TV).
 */
class WeatherPrefs(context: Context) {
    private val prefs = context.getSharedPreferences("tv_settings", Context.MODE_PRIVATE)
    private val _asked = MutableStateFlow(prefs.contains(KEY))
    private val _on = MutableStateFlow(prefs.getBoolean(KEY, false))

    /** The one-time question was answered (here or in Nastavení). */
    val asked: StateFlow<Boolean> = _asked.asStateFlow()

    /** Off (or never asked) = the TV never asks its OS for a location; the gateway's household place is used. */
    val on: StateFlow<Boolean> = _on.asStateFlow()

    /** Povolit / Nepoužívat, or the switch; true when the switch changed. */
    fun set(on: Boolean): Boolean {
        val changed = on != _on.value
        prefs.edit().putBoolean(KEY, on).apply()
        _asked.value = true
        _on.value = on
        return changed
    }

    private companion object {
        const val KEY = "weather_location"
    }
}
