package cz.cztvhub.core.profile

import cz.cztvhub.core.CtException
import cz.cztvhub.core.HttpResponse
import cz.cztvhub.core.dvb.DvbConfig
import cz.cztvhub.core.dvb.DvbMessages
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.longOrNull
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI

/** Seam for tests: one request with an optional JSON body. Throws [IOException] on network failure. */
fun interface ProfileTransport {
    fun send(method: String, url: String, headers: Map<String, String>, body: String?): HttpResponse
}

/** A session from create or sign-in. The token goes only to the platform's secure storage. */
data class ProfileSession(val token: String, val name: String, val settings: SettingsDocument, val version: Long)

/** `GET /dvb/profile`. */
data class ProfileSnapshot(val name: String, val settings: SettingsDocument, val version: Long)

/**
 * A failed profile call with the text for the form ([userMessage]: the gateway's Czech `error`, else a generic
 * one). [signedOut] = the gateway ended this session (`401` with `"code": "profileSignedOut"`); a `401` of the app
 * key has no such code and keeps the profile.
 */
class ProfileException(
    message: String,
    userMessage: String,
    val signedOut: Boolean = false,
    val httpStatus: Int? = null,
) : CtException(message, userMessage, httpStatus?.toString())

/**
 * Client for the gateway's profiles (spec/dvb-gateway.md "Profiles"). Blocking; call it off the main thread.
 * Passwords are only sent, never kept or logged.
 */
class ProfileClient(val config: DvbConfig, private val transport: ProfileTransport = UrlConnectionProfileTransport()) {

    fun create(name: String, password: String, settings: SettingsDocument): ProfileSession = session(
        call("POST", "/dvb/profiles", null, body("name" to JsonPrimitive(name), "password" to JsonPrimitive(password), "settings" to settings.toJson())),
    )

    fun login(name: String, password: String): ProfileSession =
        session(call("POST", "/dvb/profiles/login", null, body("name" to JsonPrimitive(name), "password" to JsonPrimitive(password))))

    fun get(token: String): ProfileSnapshot {
        val root = call("GET", "/dvb/profile", token, null)
        return ProfileSnapshot(
            root.str("name") ?: bad("no name"),
            settings(root),
            root.long("version") ?: bad("no version"),
        )
    }

    /** Saves [settings]; returns the profile's new version. */
    fun put(token: String, settings: SettingsDocument): Long =
        call("PUT", "/dvb/profile", token, body("settings" to settings.toJson())).long("version") ?: bad("no version")

    /** Ends this session on the gateway. */
    fun logout(token: String) {
        call("POST", "/dvb/profile/logout", token, null, expectJson = false)
    }

    private fun session(root: JsonObject) = ProfileSession(
        root.str("token") ?: bad("no token"),
        root.str("name") ?: bad("no name"),
        settings(root),
        root.long("version") ?: bad("no version"),
    )

    private fun settings(root: JsonObject): SettingsDocument =
        SettingsDocument.from(root["settings"] as? JsonObject ?: bad("no settings"))

    private fun body(vararg fields: Pair<String, kotlinx.serialization.json.JsonElement>): String =
        JsonObject(mapOf(*fields)).toString()

    private fun call(method: String, path: String, token: String?, body: String?, expectJson: Boolean = true): JsonObject {
        val headers = config.authHeaders + ("Accept" to "application/json") +
            (if (body != null) mapOf("Content-Type" to "application/json") else emptyMap()) +
            (if (token != null) mapOf(TOKEN_HEADER to token) else emptyMap())
        val response = try {
            transport.send(method, config.url(path), headers, body)
        } catch (e: IOException) {
            throw ProfileException("profile $method $path failed: $e", DvbMessages.UNREACHABLE)
        }
        val root = parse(response.body)
        if (response.status !in 200..299) {
            val signedOut = response.status == 401 && root?.str("code") == SIGNED_OUT_CODE
            val fallback = if (response.status == 401) DvbMessages.BAD_KEY else DvbMessages.GENERIC
            throw ProfileException(
                "profile $method $path HTTP ${response.status}",
                root?.str("error") ?: fallback,
                signedOut,
                response.status,
            )
        }
        if (!expectJson) return JsonObject(emptyMap())
        return root ?: bad("not JSON")
    }

    private fun bad(what: String): Nothing = throw ProfileException("profile: $what", DvbMessages.GENERIC)

    private fun parse(body: String): JsonObject? =
        try {
            Json.parseToJsonElement(body) as? JsonObject
        } catch (_: Exception) {
            null
        }

    private fun JsonObject.str(key: String): String? {
        val v = this[key]
        if (v == null || v is JsonNull) return null
        return (v as? JsonPrimitive)?.takeIf { it.isString }?.content?.ifEmpty { null }
    }

    private fun JsonObject.long(key: String): Long? {
        val v = this[key] as? JsonPrimitive ?: return null
        if (v is JsonNull || v.isString) return null
        return v.longOrNull
    }

    companion object {
        const val TOKEN_HEADER = "X-Cztvhub-Profile"
        const val SIGNED_OUT_CODE = "profileSignedOut"
    }
}

class UrlConnectionProfileTransport(private val timeoutMs: Int = 8_000) : ProfileTransport {
    override fun send(method: String, url: String, headers: Map<String, String>, body: String?): HttpResponse {
        val connection = URI(url).toURL().openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = timeoutMs
            connection.readTimeout = timeoutMs
            headers.forEach { (k, v) -> connection.setRequestProperty(k, v) }
            if (body != null) {
                connection.doOutput = true
                connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val status = connection.responseCode
            val stream = if (status >= 400) connection.errorStream else connection.inputStream
            return HttpResponse(status, stream?.use { it.readBytes().toString(Charsets.UTF_8) }.orEmpty())
        } finally {
            connection.disconnect()
        }
    }
}
