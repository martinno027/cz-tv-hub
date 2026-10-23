package cz.cztvhub.tv

import android.content.Context
import cz.cztvhub.core.ChannelPrefs
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** "Kanály" (hidden channels and their order) in SharedPreferences: this device only, backups are off. */
class ChannelPrefsStore(context: Context) {
    private val prefs = context.getSharedPreferences("channel_prefs", Context.MODE_PRIVATE)
    private val _value = MutableStateFlow(ChannelPrefs.decode(prefs.getString(KEY, null)))
    val value: StateFlow<ChannelPrefs> = _value.asStateFlow()

    fun update(change: (ChannelPrefs) -> ChannelPrefs) {
        val next = change(_value.value)
        if (next == _value.value) return
        prefs.edit().putString(KEY, next.encode()).apply()
        _value.value = next
    }

    fun reset() = update { ChannelPrefs.DEFAULT }

    private companion object {
        const val KEY = "prefs"
    }
}
