package cz.cztvhub.core.dvb

import cz.cztvhub.core.CtException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.time.Instant

/** Parsing of the gateway's `/dvb/…` JSON; unknown or missing fields never crash. */
object DvbParser {

    fun parseChannels(status: Int, body: String): List<DvbChannel> {
        val root = ok(status, body)
        return (root["channels"] as? JsonArray).orEmpty().mapNotNull { item ->
            val o = item as? JsonObject ?: return@mapNotNull null
            val id = o.str("id") ?: return@mapNotNull null
            DvbChannel(id, o.str("number")?.toIntOrNull() ?: 0, o.str("name") ?: id, o.str("icon"))
        }.sortedBy { it.number }
    }

    fun parseEpg(status: Int, body: String): List<DvbEvent> {
        val root = ok(status, body)
        return (root["events"] as? JsonArray).orEmpty().mapNotNull { item ->
            val o = item as? JsonObject ?: return@mapNotNull null
            val start = o.str("start")?.let(::instant) ?: return@mapNotNull null
            val end = o.str("end")?.let(::instant) ?: return@mapNotNull null
            DvbEvent(
                id = o.str("id") ?: return@mapNotNull null,
                channelId = o.str("channel") ?: return@mapNotNull null,
                title = o.str("title").orEmpty(),
                start = start,
                end = end,
                subtitle = o.str("subtitle").orEmpty(),
                description = o.str("description").orEmpty(),
                genre = o.str("genre").orEmpty(),
                recording = (o["recording"] as? JsonObject)?.let(::recording),
            )
        }
    }

    fun parseStatus(status: Int, body: String): DvbStatus {
        val root = ok(status, body)
        val tuners = (root["tuners"] as? JsonArray).orEmpty().mapNotNull { t ->
            val o = t as? JsonObject ?: return@mapNotNull null
            DvbTuner(o.str("name").orEmpty(), o.str("inUse") == "true")
        }
        return DvbStatus(
            enabled = root.str("enabled") == "true",
            retentionDays = root.str("retentionDays")?.toIntOrNull() ?: 0,
            tuners = tuners,
            tvheadendReachable = root.str("tvheadendReachable") == "true",
            thumbnails = root.str("thumbnails") == "true",
        )
    }

    /**
     * `GET /dvb/recording/<id>/breaks`: entries without numeric `start < end` are dropped, the rest sorted by
     * start; `confidence` defaults to 1. `pending` comes with an empty list.
     */
    fun parseBreaks(status: Int, body: String): List<AdBreak> {
        val root = ok(status, body)
        return (root["breaks"] as? JsonArray).orEmpty().mapNotNull { item ->
            val o = item as? JsonObject ?: return@mapNotNull null
            val start = o.num("start") ?: return@mapNotNull null
            val end = o.num("end") ?: return@mapNotNull null
            if (start >= end) return@mapNotNull null
            AdBreak(start, end, o.num("confidence") ?: 1.0)
        }.sortedBy { it.start }
    }

    /**
     * `GET /dvb/timeline/<id>`: the window's map must be usable (non-empty `pieces` with numeric, non-negative,
     * ordered positions and valid times, a gateway path as `playlist`), else an error; programmes without valid
     * times are dropped, neighbours that are no `b…` id become null, unknown fields are ignored.
     */
    fun parseTimeline(status: Int, body: String): DvbTimeline {
        val root = ok(status, body)
        fun bad(what: String): Nothing = throw CtException("gateway timeline: $what", DvbMessages.GENERIC)
        val id = root.str("id") ?: bad("no id")
        val playlist = root.str("playlist")?.takeIf { it.startsWith("/") && !it.startsWith("//") }
            ?: bad("bad playlist")
        val complete = when (root.str("complete")) {
            "true" -> true
            "false" -> false
            else -> bad("bad complete")
        }
        val programme = root["programme"] as? JsonObject ?: bad("no programme")
        val programmeStart = programme.str("start")?.let(::instant) ?: bad("bad programme start")
        val programmeEnd = programme.str("end")?.let(::instant) ?: bad("bad programme end")
        val pieces = (root["pieces"] as? JsonArray ?: bad("no pieces")).map { item ->
            val o = item as? JsonObject ?: bad("bad piece")
            val position = o.num("position")?.takeIf { it >= 0 } ?: bad("bad piece position")
            val duration = o.num("duration")?.takeIf { it >= 0 } ?: bad("bad piece duration")
            val time = o.str("time")?.let(::instant) ?: bad("bad piece time")
            TimelinePiece(position, time, duration)
        }
        if (pieces.isEmpty()) bad("no pieces")
        if (pieces.zipWithNext().any { (a, b) -> b.position < a.position }) bad("pieces out of order")
        val programmes = (root["programmes"] as? JsonArray).orEmpty().mapNotNull { item ->
            val o = item as? JsonObject ?: return@mapNotNull null
            val start = o.str("start")?.let(::instant) ?: return@mapNotNull null
            val end = o.str("end")?.let(::instant) ?: return@mapNotNull null
            if (!end.isAfter(start)) return@mapNotNull null
            TimelineProgramme(
                id = o.str("id")?.takeIf(TimelineRules::isTimelineId),
                title = o.str("title").orEmpty(),
                subtitle = o.str("subtitle").orEmpty(),
                start = start,
                end = end,
                breaks = o.str("breaks") == "true",
            )
        }.sortedBy { it.start }
        return DvbTimeline(
            id = id,
            channel = root.str("channel").orEmpty(),
            playlist = playlist,
            complete = complete,
            programmeStart = programmeStart,
            programmeEnd = programmeEnd,
            pieces = pieces,
            programmes = programmes,
            previous = root.str("previous")?.takeIf(TimelineRules::isTimelineId),
            next = root.str("next")?.takeIf(TimelineRules::isTimelineId),
        )
    }

    /** The gateway's Czech `error` text from a JSON error body, if any. */
    fun errorText(body: String): String? = (parse(body))?.str("error")

    private fun recording(o: JsonObject): DvbRecording? {
        val id = o.str("id") ?: return null
        val state = when (o.str("state")) {
            "completed" -> RecordingState.COMPLETED
            "recording" -> RecordingState.RECORDING
            "scheduled" -> RecordingState.SCHEDULED
            else -> RecordingState.FAILED
        }
        return DvbRecording(id, state)
    }

    private fun ok(status: Int, body: String): JsonObject {
        val root = parse(body)
        if (status == 401) throw CtException("gateway HTTP 401", DvbMessages.BAD_KEY, "401")
        if (status !in 200..299) {
            throw CtException("gateway HTTP $status", root?.str("error") ?: DvbMessages.GENERIC, status.toString())
        }
        return root ?: throw CtException("gateway: not JSON", DvbMessages.GENERIC)
    }

    private fun parse(body: String): JsonObject? =
        try {
            Json.parseToJsonElement(body) as? JsonObject
        } catch (_: Exception) {
            null
        }

    private fun instant(value: String): Instant? =
        try {
            Instant.parse(value)
        } catch (_: Exception) {
            null
        }

    private fun JsonObject.str(key: String): String? {
        val v = this[key]
        if (v == null || v is JsonNull) return null
        return (v as? JsonPrimitive)?.content?.ifEmpty { null }
    }

    private fun JsonObject.num(key: String): Double? {
        val v = this[key] as? JsonPrimitive ?: return null
        if (v is JsonNull || v.isString) return null
        return v.content.toDoubleOrNull()?.takeIf { it.isFinite() }
    }

    private fun JsonArray?.orEmpty(): List<kotlinx.serialization.json.JsonElement> = this ?: emptyList()
}
