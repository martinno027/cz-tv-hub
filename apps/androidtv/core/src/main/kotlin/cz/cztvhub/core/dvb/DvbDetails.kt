package cz.cztvhub.core.dvb

import java.net.URLEncoder
import java.time.LocalDate

/** Which programme `GET /dvb/details` is asked about: always a programme id, never free text. */
sealed interface DetailsQuery {
    /** An Anténa event id from `/dvb/epg`. */
    data class Dvb(val eventId: String) : DetailsQuery

    /** A CT programme: the channel's slug (`ct1`), the broadcast day of the schedule it came from and its idec. */
    data class Ct(val slug: String, val day: LocalDate, val idec: String) : DetailsQuery

    fun path(): String = when (this) {
        is Dvb -> "/dvb/details?source=dvb&id=${encode(eventId)}"
        is Ct -> "/dvb/details?source=ct&channel=${encode(slug)}&date=$day&idec=${encode(idec)}"
    }
}

private fun encode(value: String): String = URLEncoder.encode(value, "UTF-8")

data class TmdbRating(val value: Double, val votes: Int)

/** TMDb's facts about a programme, as the gateway matched them (spec/dvb-gateway.md "Programme details"). */
data class TmdbMatch(
    val kind: String,
    val title: String,
    val originalTitle: String?,
    val year: Int?,
    val overview: String?,
    val genres: List<String>,
    val runtimeMinutes: Int?,
    /** Null below 20 votes. */
    val rating: TmdbRating?,
    /** A gateway path (`/dvb/details/image/…`), loaded with the key; never a URL. */
    val poster: String?,
    val tmdbId: String?,
)

/** `GET /dvb/details`: [match] null = no (sure) match; [attribution] is shown whenever [match] is. */
data class ProgrammeDetails(val match: TmdbMatch?, val attribution: String?)
