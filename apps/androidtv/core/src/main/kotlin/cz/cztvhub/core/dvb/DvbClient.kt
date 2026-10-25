package cz.cztvhub.core.dvb

import cz.cztvhub.core.CtException
import cz.cztvhub.core.HouseholdDefaults
import cz.cztvhub.core.HttpResponse
import cz.cztvhub.core.archive.ArchiveHome
import cz.cztvhub.core.archive.ArchiveMessages
import cz.cztvhub.core.archive.ArchivePage
import cz.cztvhub.core.archive.ArchiveParser
import cz.cztvhub.core.archive.ArchiveRules
import cz.cztvhub.core.archive.ArchiveShow
import cz.cztvhub.core.archive.ArchiveSort
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import java.net.URLEncoder
import java.time.LocalDate

private val ARCHIVE_SLUG = Regex("^[a-z0-9-]{1,60}$")

/** Seam for tests: GET with headers. Throws [IOException] on network failure. */
fun interface DvbTransport {
    fun get(url: String, headers: Map<String, String>): HttpResponse
}

/** Client for the household gateway's `/dvb/…` JSON API. Blocking; call it off the main thread. */
class DvbClient(val config: DvbConfig, private val transport: DvbTransport = UrlConnectionDvbTransport()) {

    fun status(): DvbStatus = call("/dvb/status") { s, b -> DvbParser.parseStatus(s, b) }

    fun channels(): List<DvbChannel> = call("/dvb/channels") { s, b -> DvbParser.parseChannels(s, b) }

    /** Events of a Prague broadcast day (05:00–05:00). */
    fun epg(day: LocalDate): List<DvbEvent> = call("/dvb/epg?date=$day") { s, b -> DvbParser.parseEpg(s, b) }

    /** The household default channel list (T11); `404` from a gateway without it. */
    fun defaults(): HouseholdDefaults = call("/dvb/defaults") { s, b -> DvbParser.parseDefaults(s, b) }

    /** TMDb facts of a programme (T15); `404` when the gateway has no `[tmdb] key` or does not know the id. */
    fun details(query: DetailsQuery): ProgrammeDetails = call(query.path()) { s, b -> DvbParser.parseDetails(s, b) }

    /** A poster of [details] (needs [DvbConfig.authHeaders]); null for anything but a gateway image path. */
    fun posterUrl(path: String): String? = path.takeIf(DvbParser::isPosterPath)?.let(config::url)

    /** The Archiv tab's home rows (T13, spec/dvb-gateway.md "Archive"). */
    fun archiveHome(): ArchiveHome = call("/dvb/archive") { s, b -> ArchiveParser.parseHome(s, b) }

    /** A category (`recordings` = ours only), [genre] a sub-genre id, [page] from 1. */
    fun archiveCategory(slug: String, sort: ArchiveSort, genre: String?, page: Int): ArchivePage {
        if (!ARCHIVE_SLUG.matches(slug)) throw CtException("bad archive slug $slug", ArchiveMessages.GENERIC)
        val query = buildList {
            add("sort=${sort.query}")
            genre?.let { add("genre=${encode(it)}") }
            add("page=$page")
        }.joinToString("&")
        return call("/dvb/archive/category/$slug?$query") { s, b -> ArchiveParser.parsePage(s, b) }
    }

    /** Search by title; [text] is cut to [ArchiveRules.SEARCH_MAX_CHARS]. */
    fun archiveSearch(text: String, page: Int): ArchivePage =
        call("/dvb/archive/search?q=${encode(text.take(ArchiveRules.SEARCH_MAX_CHARS))}&page=$page") { s, b ->
            ArchiveParser.parsePage(s, b)
        }

    /** A show page; with [season] that season's episodes ([page] from 1). */
    fun archiveShow(id: String, season: String? = null, page: Int = 1): ArchiveShow {
        if (!ArchiveParser.isCardId(id)) throw CtException("bad archive id $id", ArchiveMessages.GENERIC)
        val query = season?.let { "?season=${encode(it)}&page=$page" }.orEmpty()
        // The id is checked above; only a non-ASCII letter of a normalised title needs escaping, the colon stays.
        return call("/dvb/archive/show/${encode(id).replace("%3A", ":")}$query") { s, b -> ArchiveParser.parseShow(s, b) }
    }

    /** A card image that is a gateway path (needs [DvbConfig.authHeaders]); null for anything else. */
    fun archiveImageUrl(path: String): String? = path.takeIf(ArchiveParser::isGatewayPath)?.let(config::url)

    /** Ad breaks of a finished recording (`[ads]` on the gateway); `404` when the feature is off. */
    fun breaks(recordingId: String): List<AdBreak> =
        call("/dvb/recording/$recordingId/breaks") { s, b -> DvbParser.parseBreaks(s, b) }

    /** The channel timeline window around a continuous-recording programme (`b…` id). */
    fun timeline(recordingId: String): DvbTimeline =
        call("/dvb/timeline/$recordingId") { s, b -> DvbParser.parseTimeline(s, b) }

    /** The window's playlist (needs [DvbConfig.authHeaders]). */
    fun timelineUrl(timeline: DvbTimeline): String = config.url(timeline.playlist)

    /**
     * Fetches the window's playlist once before a window switch, so a failure keeps the old window playing;
     * throws [CtException] with the gateway's Czech text.
     */
    fun checkPlaylist(timeline: DvbTimeline) {
        val response = try {
            transport.get(timelineUrl(timeline), config.authHeaders)
        } catch (e: IOException) {
            throw CtException("gateway playlist failed: $e", DvbMessages.UNREACHABLE)
        }
        if (response.status !in 200..299) {
            throw CtException(
                "gateway playlist HTTP ${response.status}",
                DvbParser.errorText(response.body) ?: DvbMessages.GENERIC,
                response.status.toString(),
            )
        }
    }

    fun liveUrl(channelId: String): String = config.url("/dvb/live/$channelId.m3u8")

    fun recordingUrl(recordingId: String): String = config.url("/dvb/recording/$recordingId.m3u8")

    /** One still of a recording (needs [DvbConfig.authHeaders]); never cache it on disk. */
    fun thumbnailUrl(recordingId: String): String = config.url("/dvb/recording/$recordingId/thumb.jpg")

    private fun encode(text: String): String = URLEncoder.encode(text, Charsets.UTF_8).replace("+", "%20")

    private fun <T> call(path: String, parse: (Int, String) -> T): T {
        val response = try {
            transport.get(config.url(path), config.authHeaders + ("Accept" to "application/json"))
        } catch (e: IOException) {
            throw CtException("gateway $path failed: $e", DvbMessages.UNREACHABLE)
        }
        return parse(response.status, response.body)
    }
}

/**
 * Connect/read timeout of the gateway clients: above the Tailscale bridge's 8 s dial timeout, so through the bridge
 * its 502 with the Czech text comes first instead of a client timeout (spec/apps.md "Back in front (T18)").
 */
const val GATEWAY_TIMEOUT_MS = 10_000

class UrlConnectionDvbTransport(val timeoutMs: Int = GATEWAY_TIMEOUT_MS) : DvbTransport {
    override fun get(url: String, headers: Map<String, String>): HttpResponse {
        val connection = URI(url).toURL().openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = timeoutMs
            connection.readTimeout = timeoutMs
            headers.forEach { (k, v) -> connection.setRequestProperty(k, v) }
            val status = connection.responseCode
            val stream = if (status >= 400) connection.errorStream else connection.inputStream
            return HttpResponse(status, stream?.use { it.readBytes().toString(Charsets.UTF_8) }.orEmpty())
        } finally {
            connection.disconnect()
        }
    }
}
