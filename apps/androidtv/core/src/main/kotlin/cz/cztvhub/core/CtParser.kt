package cz.cztvhub.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.time.Instant

/**
 * Parsing of CT API responses. The API is undocumented, so every field is optional:
 * unknown or missing data must end in a [CtException], never a crash.
 */
object CtParser {

    fun parseLive(channelId: String, status: Int, body: String): LiveStreams {
        val root = parseOrNull(body)
        if (status !in 200..299) throw httpError(status, root)
        val urls = root.obj("streamUrls")
        if (urls.bool("isBlocked")) {
            throw CtException("$channelId is blocked (geo restriction?)", Messages.BLOCKED)
        }
        val main = urls.str("main") ?: throw CtException("$channelId: no stream URL in API response")
        return LiveStreams(main = main, timeshift = urls.str("timeshift"))
    }

    /** HLS URL of an archive item; throws [StillLiveException] when it is airing right now. */
    fun parseVod(idec: String, status: Int, body: String): String {
        val root = parseOrNull(body)
        if (status !in 200..299) {
            val error = httpError(status, root)
            // DRM-only items are never retried through another stream; point to the official app.
            if (error.status == "UNSUPPORTED_DRM") {
                throw CtException(error.message.orEmpty(), "${error.userMessage}\n${Messages.DRM_HINT}", error.status)
            }
            throw error
        }
        (root?.get("streams") as? JsonArray)?.forEach { stream ->
            (stream as? JsonObject).str("url")?.let { return it }
        }
        root.str("platformChannel")?.let { throw StillLiveException(idec, it) }
        throw CtException("$idec: no stream URL in API response", Messages.VIDEO_UNAVAILABLE)
    }

    /** Programmes of one broadcast day keyed by channel slug; malformed entries are skipped. */
    fun parseSchedule(status: Int, body: String): Map<String, List<Programme>> {
        val root = parseOrNull(body)
        if (status !in 200..299) throw CtException("schedule HTTP $status", Messages.SCHEDULE_FAILED)
        val plans = root.obj("data")?.get("TVProgramDailyChannelsPlanV2") as? JsonArray
            ?: throw CtException("schedule query failed: ${root?.get("errors")}", Messages.SCHEDULE_FAILED)
        val result = LinkedHashMap<String, List<Programme>>()
        for (plan in plans) {
            val planObj = plan as? JsonObject ?: continue
            val slug = planObj.str("channel") ?: continue
            val items = planObj["program"] as? JsonArray ?: JsonArray(emptyList())
            result[slug] = items.mapNotNull { parseProgramme(it as? JsonObject) }
        }
        return result
    }

    private fun parseProgramme(item: JsonObject?): Programme? {
        item ?: return null
        val idec = item.str("idec") ?: return null
        val start = item.str("start")?.let(::parseInstant) ?: return null
        val end = item.str("end")?.let(::parseInstant) ?: return null
        return Programme(
            idec = idec,
            title = item.str("title").orEmpty(),
            start = start,
            end = end,
            isPlayableNow = item.bool("isPlayableNow"),
            liveOnly = item.bool("liveOnly"),
            episodeTitle = item.str("episodeTitle").orEmpty(),
            description = item.str("description").orEmpty(),
            genre = item.str("genre").orEmpty(),
            imageUrl = item.str("imageUrl").orEmpty(),
        )
    }

    private fun parseInstant(value: String): Instant? =
        try {
            Instant.parse(value)
        } catch (_: Exception) {
            null
        }

    /** Keep CT's Czech explanation (`message.labels.textShort`) when the error body has one. */
    private fun httpError(status: Int, root: JsonObject?): CtException {
        val message = root.obj("message")
        val name = message.obj("status").str("name")
        val text = message.obj("labels").str("textShort")
        return CtException("HTTP $status ${name.orEmpty()}".trim(), text ?: Messages.VIDEO_UNAVAILABLE, name)
    }

    private fun parseOrNull(body: String): JsonObject? =
        try {
            Json.parseToJsonElement(body) as? JsonObject
        } catch (_: Exception) {
            null
        }

    private fun JsonObject?.obj(key: String): JsonObject? = this?.get(key) as? JsonObject

    private fun JsonObject?.str(key: String): String? {
        val value: JsonElement? = this?.get(key)
        if (value == null || value is JsonNull) return null
        val text = (value as? JsonPrimitive)?.content ?: return null
        return text.ifEmpty { null }
    }

    private fun JsonObject?.bool(key: String): Boolean {
        val value = this?.get(key) as? JsonPrimitive ?: return false
        return value.content == "true"
    }
}
