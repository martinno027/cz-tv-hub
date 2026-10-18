package cz.cztvhub.tv.dvb

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import cz.cztvhub.core.dvb.DvbConfig
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Gateway address and bearer key. The key is encrypted with an AES key that never leaves the
 * Android Keystore; only the ciphertext is in SharedPreferences (excluded from backups).
 */
class DvbSettings(context: Context) {
    private val prefs = context.getSharedPreferences("dvb", Context.MODE_PRIVATE)
    private val _config = MutableStateFlow(load())
    val config: StateFlow<DvbConfig?> = _config.asStateFlow()

    val address: String get() = prefs.getString(ADDRESS, "").orEmpty()

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
    }
}
