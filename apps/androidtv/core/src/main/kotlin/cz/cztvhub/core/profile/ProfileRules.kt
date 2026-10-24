package cz.cztvhub.core.profile

import java.text.Normalizer
import java.util.Locale

/**
 * Profile names and passwords (spec/dvb-gateway.md "Profiles"; golden cases in spec/fixtures/profile_cases.json
 * "names" and "passwords"). The gateway decides; the app checks first so the form can say so without a request.
 */
object ProfileRules {
    const val NAME_MAX = 32
    const val PASSWORD_MIN = 4
    const val PASSWORD_MAX = 64

    /** The name as the gateway keeps it (trimmed, NFC), or null when it is empty, too long or has a control character. */
    fun normalizeName(name: String): String? {
        val normalized = Normalizer.normalize(name.trim(), Normalizer.Form.NFC)
        val length = normalized.codePointCount(0, normalized.length)
        if (length !in 1..NAME_MAX) return null
        if (normalized.codePoints().anyMatch { Character.getType(it) == Character.CONTROL.toInt() }) return null
        return normalized
    }

    /** What makes two names the same profile: lower case without diacritics ("Jana" = "jána"). */
    fun nameKey(name: String): String {
        val decomposed = Normalizer.normalize(name.trim(), Normalizer.Form.NFD)
        return decomposed.filter { Character.getType(it) != Character.NON_SPACING_MARK.toInt() }.lowercase(Locale.ROOT)
    }

    fun isValidPassword(password: String): Boolean =
        password.codePointCount(0, password.length) in PASSWORD_MIN..PASSWORD_MAX
}
