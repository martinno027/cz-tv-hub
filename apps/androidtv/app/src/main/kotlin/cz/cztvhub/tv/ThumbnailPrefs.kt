package cz.cztvhub.tv

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** "Náhledy anténních pořadů" (Nastavení → Server), this TV only; default on. */
class ThumbnailPrefs(context: Context) {
    private val prefs = context.getSharedPreferences("tv_settings", Context.MODE_PRIVATE)
    private val _on = MutableStateFlow(prefs.getBoolean(KEY, true))
    val on: StateFlow<Boolean> = _on.asStateFlow()

    fun set(on: Boolean) {
        prefs.edit().putBoolean(KEY, on).apply()
        _on.value = on
    }

    private companion object {
        const val KEY = "antenna_thumbnails"
    }
}
