package cz.cztvhub.tv

import android.content.Context
import cz.cztvhub.core.KeyValueBackend

/**
 * "Pokračovat" positions in SharedPreferences: on this device only (backups are off in the manifest),
 * never a stream URL.
 */
class ResumePrefs(context: Context) : KeyValueBackend {
    private val prefs = context.getSharedPreferences("resume", Context.MODE_PRIVATE)

    override fun all(): Map<String, String> =
        prefs.all.mapNotNull { (key, value) -> (value as? String)?.let { key to it } }.toMap()

    override fun put(key: String, value: String) {
        prefs.edit().putString(key, value).apply()
    }

    override fun remove(keys: Collection<String>) {
        if (keys.isEmpty()) return
        val editor = prefs.edit()
        keys.forEach { editor.remove(it) }
        editor.apply()
    }
}
