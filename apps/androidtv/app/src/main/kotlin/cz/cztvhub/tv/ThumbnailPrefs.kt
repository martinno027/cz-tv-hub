package cz.cztvhub.tv

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** "Náhledy anténních pořadů" (Nastavení → Server), this TV's own or a signed-in profile's; default on. */
class ThumbnailPrefs(context: Context) {
    private val prefs = context.getSharedPreferences("tv_settings", Context.MODE_PRIVATE)
    private val _on = MutableStateFlow(prefs.getBoolean(KEY, true))
    val on: StateFlow<Boolean> = _on.asStateFlow()

    /** True when the switch changed. */
    fun set(on: Boolean): Boolean {
        if (on == _on.value) return false
        prefs.edit().putBoolean(KEY, on).apply()
        _on.value = on
        return true
    }

    private companion object {
        const val KEY = "antenna_thumbnails"
    }
}
