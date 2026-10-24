package cz.cztvhub.core.profile

import cz.cztvhub.core.ChannelPrefs
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject

/**
 * The settings that travel with a profile (spec/apps.md "Profil"): `{"v": 1, "channelPrefs": …, "thumbnails": …}`.
 * Fields this app does not know, also inside `channelPrefs`, are kept and exported unchanged, so a newer app's
 * settings survive an older TV (golden cases in spec/fixtures/profile_cases.json "document").
 */
class SettingsDocument private constructor(private val raw: JsonObject) {
    /** "Kanály"; missing or unreadable = the defaults. */
    val channelPrefs: ChannelPrefs = ChannelPrefs.decode((raw["channelPrefs"] as? JsonObject)?.toString())

    /** "Náhledy anténních pořadů"; missing = on (the TV switch's default). */
    val thumbnails: Boolean = (raw["thumbnails"] as? JsonPrimitive)?.booleanOrNull ?: true

    /** The same document with this TV's current values of the known fields. */
    fun with(channelPrefs: ChannelPrefs = this.channelPrefs, thumbnails: Boolean = this.thumbnails): SettingsDocument {
        val stored = (raw["channelPrefs"] as? JsonObject).orEmpty()
        val known = Json.parseToJsonElement(channelPrefs.encode()).jsonObject
        // A source a newer app knows stays off (this app can neither show nor switch it).
        val otherSourcesOff = (stored["sourcesOff"] as? JsonArray).orEmpty()
            .filter { val p = it as? JsonPrimitive; p != null && p.isString && p.content !in ChannelPrefs.SOURCES }
        val sourcesOff = JsonArray(known.getValue("sourcesOff").jsonArray + otherSourcesOff)
        val prefs = JsonObject(stored + known + ("sourcesOff" to sourcesOff))
        return SettingsDocument(JsonObject(raw + mapOf("channelPrefs" to prefs, "thumbnails" to JsonPrimitive(thumbnails))))
    }

    /** What `PUT /dvb/profile` sends: every field, the known ones as this TV has them. */
    fun toJson(): JsonObject {
        val full = with().raw
        return if (full["v"] is JsonPrimitive) full else JsonObject(mapOf("v" to JsonPrimitive(VERSION)) + full)
    }

    fun encode(): String = toJson().toString()

    override fun equals(other: Any?): Boolean = other is SettingsDocument && other.toJson() == toJson()

    override fun hashCode(): Int = toJson().hashCode()

    override fun toString(): String = encode()

    companion object {
        const val VERSION = 1

        /** A document from the gateway; anything but an object gives the defaults. */
        fun from(element: JsonElement?): SettingsDocument = SettingsDocument(element as? JsonObject ?: JsonObject(emptyMap()))

        /** A stored document; unreadable text gives the defaults. */
        fun decode(text: String?): SettingsDocument = from(
            try {
                text?.let(Json::parseToJsonElement)
            } catch (_: Exception) {
                null
            },
        )

        /** This TV's own settings as a fresh document. */
        fun of(channelPrefs: ChannelPrefs, thumbnails: Boolean): SettingsDocument =
            from(null).with(channelPrefs, thumbnails)
    }
}
