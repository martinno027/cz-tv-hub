package cz.cztvhub.tv

import android.content.Context
import cz.cztvhub.core.ChannelPrefs
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * "Kanály" (hidden channels and their order) in SharedPreferences, backups are off: this TV's own, or a signed-in
 * profile's copy ([ProfileManager] keeps the TV's own aside meanwhile).
 */
class ChannelPrefsStore(context: Context) {
    private val prefs = context.getSharedPreferences("channel_prefs", Context.MODE_PRIVATE)
    private val _value = MutableStateFlow(ChannelPrefs.decode(prefs.getString(KEY, null)))
    val value: StateFlow<ChannelPrefs> = _value.asStateFlow()

    /** True when [change] changed anything. */
    fun update(change: (ChannelPrefs) -> ChannelPrefs): Boolean {
        val next = change(_value.value)
        if (next == _value.value) return false
        prefs.edit().putString(KEY, next.encode()).apply()
        _value.value = next
        return true
    }

    fun set(value: ChannelPrefs) = update { value }

    fun reset() = update { ChannelPrefs.DEFAULT }

    private companion object {
        const val KEY = "prefs"
    }
}
