package cz.cztvhub.core.archive

import cz.cztvhub.core.CtException
import cz.cztvhub.core.ProgrammePage
import cz.cztvhub.core.dvb.DvbMessages
import cz.cztvhub.core.dvb.DvbParser
import cz.cztvhub.core.dvb.TmdbRating
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.time.Instant

/**
 * Parsing of `/dvb/archive…` (spec/dvb-gateway.md "Archive"). Defensive: an item without what it needs is dropped,
 * an unknown `source`/`kind` stays unknown (null), never guessed; images are kept only as an `https://` URL or a
 * gateway path, ids only when they are safe in a path.
 */
object ArchiveParser {
    private val ID = Regex("^(ct|rec):[\\p{L}\\p{N}_.-]{1,80}$")
    private val IDEC = Regex("^[A-Za-z0-9_-]{1,40}$")
    private val RECORDING = Regex("^[A-Za-z0-9_-]{1,64}$")
    private val CHANNEL_ID = Regex("^CH_[0-9A-Za-z]{1,10}$")
    private val THUMBNAIL = Regex("^/dvb/recording/[A-Za-z0-9_-]{1,64}/thumb\\.jpg$")

    fun parseHome(status: Int, body: String): ArchiveHome {
        val root = ok(status, body)
        val rows = root.array("rows").mapNotNull { item ->
            val o = item as? JsonObject ?: return@mapNotNull null
            val id = o.str("id") ?: return@mapNotNull null
            val cards = cards(o)
            if (cards.isEmpty()) return@mapNotNull null
            ArchiveRow(id, o.str("title") ?: id, cards, o.bool("more"))
        }
        return ArchiveHome(rows, root.str("error"), root.str("attribution"))
    }

    /** A category page or a search page (search has no `title` and `genres`). */
    fun parsePage(status: Int, body: String): ArchivePage {
        val root = ok(status, body)
        val genres = root.array("genres").mapNotNull { item ->
            val o = item as? JsonObject ?: return@mapNotNull null
            val id = o.str("id") ?: return@mapNotNull null
            ArchiveGenre(id, o.str("title") ?: return@mapNotNull null)
        }
        return ArchivePage(
            root.str("title").orEmpty(), genres, cards(root), nextPage(root), root.str("error"), root.str("attribution"),
        )
    }

    fun parseShow(status: Int, body: String): ArchiveShow {
        val root = ok(status, body)
        val card = (root["card"] as? JsonObject)?.let(::card)
            ?: throw CtException("archive show: no card", ArchiveMessages.GENERIC)
        val seasons = root.array("seasons").mapNotNull { item ->
            val o = item as? JsonObject ?: return@mapNotNull null
            val id = o.str("id") ?: return@mapNotNull null
            ArchiveSeason(id, o.str("title") ?: id)
        }
        val groups = root.array("groups").mapNotNull { item ->
            val o = item as? JsonObject ?: return@mapNotNull null
            EpisodeGroup(
                o.str("title").orEmpty(),
                o.array("episodes").mapNotNull { (it as? JsonObject)?.let(::episode) },
                o.str("season"),
                nextPage(o),
            )
        }
        return ArchiveShow(
            card = card,
            description = root.str("description").orEmpty(),
            creators = root.strings("creators").joinToString(" "),
            countries = root.strings("countries"),
            seasons = seasons,
            groups = groups,
            attribution = root.str("attribution"),
            error = root.str("error"),
        )
    }

    /** A card id the app may put into `/dvb/archive/show/<id>`. */
    fun isCardId(id: String): Boolean = ID.matches(id)

    /** An image the app may load: ČT's `https://` URL, a TMDb poster or a recording thumbnail through the gateway. */
    fun isImage(value: String): Boolean =
        value.startsWith("https://") || DvbParser.isPosterPath(value) || THUMBNAIL.matches(value)

    /** True for a gateway path (loaded with the key), false for ČT's URL. */
    fun isGatewayPath(value: String): Boolean = value.startsWith("/") && isImage(value)

    /** True for a recording thumbnail (shown only where this TV and the gateway allow thumbnails). */
    fun isThumbnail(value: String): Boolean = THUMBNAIL.matches(value)

    private fun cards(o: JsonObject): List<ArchiveCard> =
        o.array("cards").mapNotNull { (it as? JsonObject)?.let(::card) }.distinctBy { it.id }

    private fun card(o: JsonObject): ArchiveCard? {
        val id = o.str("id")?.takeIf(::isCardId) ?: return null
        val title = o.str("title")?.trim()?.ifEmpty { null } ?: return null
        return ArchiveCard(
            id = id,
            source = when (o.str("source")) {
                "ct" -> CardSource.CT
                "rec" -> CardSource.REC
                "both" -> CardSource.BOTH
                else -> null
            },
            title = title,
            kind = when (o.str("kind")) {
                "movie" -> CardKind.MOVIE
                "series" -> CardKind.SERIES
                else -> null
            },
            year = o.int("year")?.takeIf { it in 1800..3000 },
            image = o.str("image")?.takeIf(::isImage),
            rating = (o["rating"] as? JsonObject)?.let(::rating),
            genres = o.strings("genres"),
            episodeCount = o.int("episodeCount")?.takeIf { it > 0 },
            durationMinutes = o.int("durationMinutes")?.takeIf { it > 0 },
            tmdb = o.bool("tmdb"),
        )
    }

    /** Below [ProgrammePage.MIN_VOTES] votes, or not a whole number of votes, there is no rating. */
    private fun rating(o: JsonObject): TmdbRating? {
        val value = o.num("value") ?: return null
        val votes = o.num("votes")?.takeIf { it >= ProgrammePage.MIN_VOTES && it == Math.floor(it) } ?: return null
        return TmdbRating(value, votes.toInt())
    }

    private fun episode(o: JsonObject): ArchiveEpisode? {
        val source = when (o.str("source")) {
            "ct" -> EpisodeSource.CT
            "rec" -> EpisodeSource.REC
            else -> return null
        }
        val idec = o.str("idec")?.takeIf(IDEC::matches)
        val recordingId = o.str("recordingId")?.takeIf(RECORDING::matches)
        if (source == EpisodeSource.CT && idec == null) return null
        if (source == EpisodeSource.REC && recordingId == null) return null
        return ArchiveEpisode(
            source = source,
            idec = idec.takeIf { source == EpisodeSource.CT },
            recordingId = recordingId.takeIf { source == EpisodeSource.REC },
            title = o.str("title")?.trim()?.ifEmpty { null } ?: return null,
            description = o.str("description").orEmpty(),
            durationSeconds = o.num("durationSeconds")?.takeIf { it >= 0 } ?: 0.0,
            aired = o.str("aired")?.let(::instant),
            channel = o.str("channel").orEmpty(),
            playable = o.bool("playable"),
            channelId = o.str("channelId")?.takeIf(CHANNEL_ID::matches),
        )
    }

    private fun nextPage(o: JsonObject): Int? = o.int("nextPage")?.takeIf { it > 0 }

    private fun ok(status: Int, body: String): JsonObject {
        val root = try {
            Json.parseToJsonElement(body) as? JsonObject
        } catch (_: Exception) {
            null
        }
        if (status == 401) throw CtException("gateway HTTP 401", DvbMessages.BAD_KEY, "401")
        if (status !in 200..299) {
            throw CtException("gateway archive HTTP $status", root?.str("error") ?: ArchiveMessages.GENERIC, status.toString())
        }
        return root ?: throw CtException("gateway archive: not JSON", ArchiveMessages.GENERIC)
    }

    private fun instant(value: String): Instant? = try {
        Instant.parse(value)
    } catch (_: Exception) {
        null
    }

    private fun JsonObject.array(key: String): List<JsonElement> = (this[key] as? JsonArray) ?: emptyList()

    private fun JsonObject.str(key: String): String? {
        val v = this[key] as? JsonPrimitive ?: return null
        if (v is JsonNull || !v.isString) return null
        return v.content.ifEmpty { null }
    }

    private fun JsonObject.num(key: String): Double? {
        val v = this[key] as? JsonPrimitive ?: return null
        if (v is JsonNull || v.isString) return null
        return v.content.toDoubleOrNull()?.takeIf { it.isFinite() }
    }

    private fun JsonObject.int(key: String): Int? = num(key)?.takeIf { it == Math.floor(it) && it <= Int.MAX_VALUE }?.toInt()

    private fun JsonObject.bool(key: String): Boolean = (this[key] as? JsonPrimitive)?.let { !it.isString && it.content == "true" } == true

    /** A list of strings; a single string counts as a list of one. */
    private fun JsonObject.strings(key: String): List<String> = when (val v = this[key]) {
        is JsonArray -> v.mapNotNull { (it as? JsonPrimitive)?.takeIf { p -> p.isString }?.content?.trim()?.ifEmpty { null } }
        is JsonPrimitive -> listOfNotNull(v.takeIf { it.isString }?.content?.trim()?.ifEmpty { null })
        else -> emptyList()
    }
}
