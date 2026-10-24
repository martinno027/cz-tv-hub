package cz.cztvhub.tv.dvb

import android.content.Context
import cz.cztvhub.core.dvb.DvbConfig
import cz.cztvhub.core.profile.ProfileLogin
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Gateway address and bearer key, and the "Profil" login (never the password). The key, the profile name and its
 * token are encrypted by [SecretBox]; only the ciphertext is in SharedPreferences (excluded from backups).
 */
class DvbSettings(context: Context) {
    private val prefs = context.getSharedPreferences("dvb", Context.MODE_PRIVATE)
    private val _config = MutableStateFlow(load())
    val config: StateFlow<DvbConfig?> = _config.asStateFlow()

    val address: String get() = prefs.getString(ADDRESS, "").orEmpty()

    private val _profile = MutableStateFlow(loadProfile())
    val profile: StateFlow<ProfileLogin?> = _profile.asStateFlow()

    fun saveProfile(login: ProfileLogin) {
        prefs.edit()
            .putString(PROFILE_NAME, encrypt(login.name))
            .putString(PROFILE_TOKEN, encrypt(login.token))
            .apply()
        _profile.value = login
    }

    fun clearProfile() {
        prefs.edit().remove(PROFILE_NAME).remove(PROFILE_TOKEN).apply()
        _profile.value = null
    }

    fun save(config: DvbConfig, typedAddress: String) {
        prefs.edit()
            .putString(ADDRESS, typedAddress)
            .putString(BASE_URL, config.baseUrl)
            .putString(KEY, encrypt(config.key))
            .apply()
        _config.value = config
    }

    fun clear() {
        prefs.edit().clear().apply()
        _config.value = null
        _profile.value = null
    }

    private fun loadProfile(): ProfileLogin? {
        val name = prefs.getString(PROFILE_NAME, null)?.let(::decrypt) ?: return null
        val token = prefs.getString(PROFILE_TOKEN, null)?.let(::decrypt) ?: return null
        return ProfileLogin(name, token)
    }

    private fun load(): DvbConfig? {
        val base = prefs.getString(BASE_URL, null) ?: return null
        val key = prefs.getString(KEY, null)?.let(::decrypt) ?: return null
        return DvbConfig(base, key)
    }

    private fun encrypt(plain: String) = SecretBox.encrypt(plain)

    private fun decrypt(stored: String) = SecretBox.decrypt(stored)

    private companion object {
        const val ADDRESS = "address"
        const val BASE_URL = "baseUrl"
        const val KEY = "key"
        const val PROFILE_NAME = "profileName"
        const val PROFILE_TOKEN = "profileToken"
    }
}
