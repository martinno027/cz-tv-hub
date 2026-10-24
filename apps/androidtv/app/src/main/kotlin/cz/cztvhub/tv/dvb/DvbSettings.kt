package cz.cztvhub.tv.dvb

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import cz.cztvhub.core.dvb.DvbConfig
import cz.cztvhub.core.profile.ProfileLogin
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Gateway address and bearer key, and the "Profil" login (never the password). The key, the profile name and its
 * token are encrypted with an AES key that never leaves the Android Keystore; only the ciphertext is in
 * SharedPreferences (excluded from backups).
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

    private fun secretKey(): SecretKey {
        val store = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (store.getEntry(ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build(),
        )
        return generator.generateKey()
    }

    private fun encrypt(plain: String): String {
        val cipher = Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.ENCRYPT_MODE, secretKey()) }
        val bytes = cipher.iv + cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(bytes, Base64.NO_WRAP)
    }

    private fun decrypt(stored: String): String? = try {
        val bytes = Base64.decode(stored, Base64.NO_WRAP)
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, secretKey(), GCMParameterSpec(128, bytes, 0, IV_SIZE))
        String(cipher.doFinal(bytes, IV_SIZE, bytes.size - IV_SIZE), Charsets.UTF_8)
    } catch (_: Exception) {
        null // Keystore key lost (e.g. restored device): ask for the key again
    }

    private companion object {
        const val KEYSTORE = "AndroidKeyStore"
        const val ALIAS = "dvb-gateway-key"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val IV_SIZE = 12
        const val ADDRESS = "address"
        const val BASE_URL = "baseUrl"
        const val KEY = "key"
        const val PROFILE_NAME = "profileName"
        const val PROFILE_TOKEN = "profileToken"
    }
}
