package cz.cztvhub.core.archive

import cz.cztvhub.core.dvb.TmdbRating
import java.time.Instant

/** Where a card's show comes from (`source`): iVysílání, our recordings, or both merged into one card. */
enum class CardSource { CT, REC, BOTH }

enum class CardKind { MOVIE, SERIES }

/**
 * One show of the archive (spec/dvb-gateway.md "Archive", **card**). [image] is an `https://` URL (ČT) or a gateway
 * path (`/dvb/…`), anything else is dropped; [source]/[kind] null when the gateway sent an unknown value.
 */
data class ArchiveCard(
    val id: String,
    val source: CardSource?,
    val title: String,
    val kind: CardKind?,
    val year: Int? = null,
    val image: String? = null,
    val rating: TmdbRating? = null,
    val genres: List<String> = emptyList(),
    val episodeCount: Int? = null,
    val durationMinutes: Int? = null,
    /** The card shows TMDb data (the gateway decides): the TMDB logo and attribution then show. */
    val tmdb: Boolean = false,
)

/** A row of the archive home: "recordings" (Naše nahrávky) or a ČT category slug. */
data class ArchiveRow(val id: String, val title: String, val cards: List<ArchiveCard>, val more: Boolean)

/**
 * [error]: the gateway's Czech text when iVysílání did not answer (`200` with our recordings still listed);
 * [attribution]: TMDb's sentence as the gateway sends it.
 */
data class ArchiveHome(val rows: List<ArchiveRow>, val error: String? = null, val attribution: String? = null)

data class ArchiveGenre(val id: String, val title: String)

/** One page of a category or a search; [nextPage] null at the end. */
data class ArchivePage(
    val title: String,
    val genres: List<ArchiveGenre>,
    val cards: List<ArchiveCard>,
    val nextPage: Int?,
    val error: String? = null,
    val attribution: String? = null,
)

enum class EpisodeSource { CT, REC }

/** An episode: iVysílání ([idec]) or our recording ([recordingId]). [aired] null when the gateway sent none. */
data class ArchiveEpisode(
    val source: EpisodeSource,
    val idec: String?,
    val recordingId: String?,
    val title: String,
    val description: String,
    val durationSeconds: Double,
    val aired: Instant?,
    val channel: String,
    val playable: Boolean,
    /** The CT API id (`CH_1`) to play a recent episode from the buffer; [channel] is only the display name. */
    val channelId: String? = null,
)

/** A ČT group carries its [season] (id or null) and [nextPage]; "Nahráno z antény" only title and episodes. */
data class EpisodeGroup(
    val title: String,
    val episodes: List<ArchiveEpisode>,
    val season: String? = null,
    val nextPage: Int? = null,
)

data class ArchiveSeason(val id: String, val title: String)

/** `GET /dvb/archive/show/<id>`; with `?season=&page=` the [groups] hold that season's page. */
data class ArchiveShow(
    val card: ArchiveCard,
    val description: String,
    /** One display string ("Režie: … Hrají: …"), shown as it is; empty when none. */
    val creators: String,
    val countries: List<String>,
    val seasons: List<ArchiveSeason>,
    val groups: List<EpisodeGroup>,
    val attribution: String? = null,
    /** ČT did not answer: a `both` show comes with only "Nahráno z antény" and this text. */
    val error: String? = null,
)

enum class ArchiveSort(val query: String) { POPULAR("popular"), NEWEST("newest"), AZ("az") }

object ArchiveMessages {
    /** Owner, 2026-10-25 (spec/apps.md "Archiv"). */
    const val NO_GATEWAY = "Archiv potřebuje připojení k serveru. Nastavte ho v Nastavení → Server."
    const val GENERIC = "Archiv se nepodařilo načíst"
    const val EMPTY = "Archiv je zatím prázdný"
    const val NO_RESULTS = "Nic nenalezeno"
}
