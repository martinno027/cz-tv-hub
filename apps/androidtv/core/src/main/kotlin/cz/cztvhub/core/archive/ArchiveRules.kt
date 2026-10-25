package cz.cztvhub.core.archive

import cz.cztvhub.core.Channels
import cz.cztvhub.core.ContinueItem
import cz.cztvhub.core.Days
import cz.cztvhub.core.PageSource
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.ProgrammePage
import cz.cztvhub.core.ResumeCard
import cz.cztvhub.core.ResumeRules
import cz.cztvhub.core.dvb.TmdbRating
import java.time.Instant

/** What the Archiv tab shows and plays (spec/apps.md "Archiv (T13)"); pure, tested in `ArchiveRulesTest`. */
object ArchiveRules {
    /** Search starts after this long without typing, from [SEARCH_MIN_CHARS] characters; the gateway takes ≤ 100. */
    const val SEARCH_DEBOUNCE_MS = 500L
    const val SEARCH_MIN_CHARS = 2
    const val SEARCH_MAX_CHARS = 100

    /** A grid asks for the next page when the focus is this many cards from its end. */
    const val PAGE_AHEAD = 12

    /** The text to search for, or null when there is nothing to search yet. */
    fun searchQuery(text: String): String? = text.trim().take(SEARCH_MAX_CHARS).takeIf { it.length >= SEARCH_MIN_CHARS }

    /** A card's second line: `2019 · film`, `seriál`; empty when both are unknown. */
    fun metaLine(card: ArchiveCard): String = listOfNotNull(
        card.year?.toString(),
        when (card.kind) {
            CardKind.MOVIE -> "film"
            CardKind.SERIES -> "seriál"
            null -> null
        },
    ).joinToString(" · ")

    /** "★ 7,4" for a TMDb rating with at least [ProgrammePage.MIN_VOTES] votes, else null (as the page's line). */
    fun star(rating: TmdbRating?): String? {
        if (rating == null || rating.votes < ProgrammePage.MIN_VOTES || !rating.value.isFinite()) return null
        return "★ ${ProgrammePage.ratingValue(rating.value)}"
    }

    /** Source icons of a card in order: globe = iVysílání, antenna = our recording, both for `both`. */
    fun icons(source: CardSource?): List<PageSource> = when (source) {
        CardSource.CT -> listOf(PageSource.CT)
        CardSource.REC -> listOf(PageSource.DVB)
        CardSource.BOTH -> listOf(PageSource.CT, PageSource.DVB)
        null -> emptyList()
    }

    /** TMDb's standard sentence, used when the answer carries no `attribution`. */
    const val TMDB_ATTRIBUTION =
        "This application uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB."

    /** The TMDB logo and sentence show when any card on screen has `"tmdb": true` (the gateway decides). */
    fun showsTmdb(cards: List<ArchiveCard>): Boolean = cards.any { it.tmdb }

    /** The answer's attribution, else TMDb's standard sentence. */
    fun attribution(fromAnswer: String?): String = fromAnswer?.trim()?.ifEmpty { null } ?: TMDB_ATTRIBUTION

    /** Czech plural of "díl" (1 díl, 2–4 díly, else dílů). */
    fun episodesWord(n: Int): String = when (n) {
        1 -> "díl"
        in 2..4 -> "díly"
        else -> "dílů"
    }

    /** The show page's facts: source · year · kind · countries · episode count. */
    fun factsLine(show: ArchiveShow): String {
        val card = show.card
        val source = when (card.source) {
            CardSource.CT -> "iVysílání"
            CardSource.REC -> "Z antény"
            CardSource.BOTH -> "iVysílání a z antény"
            null -> null
        }
        return listOfNotNull(
            source,
            card.year?.toString(),
            when (card.kind) {
                CardKind.MOVIE -> "film"
                CardKind.SERIES -> "seriál"
                null -> null
            },
            show.countries.joinToString(", ").ifEmpty { null },
            card.episodeCount?.let { "$it ${episodesWord(it)}" },
        ).joinToString(" · ")
    }

    /**
     * `1/26 Od začátku · 53 min · ČT1 9. 1.` (aired day in Prague, `9. 1. 2005` when not in [now]'s year); unknown
     * parts are left out.
     */
    fun episodeLine(episode: ArchiveEpisode, now: Instant): String {
        val minutes = ((episode.durationSeconds + 30) / 60).toLong()
        val year = now.atZone(Days.ZONE).year
        val aired = episode.aired?.atZone(Days.ZONE)?.let {
            "${it.dayOfMonth}. ${it.monthValue}." + if (it.year != year) " ${it.year}" else ""
        }
        return listOfNotNull(
            episode.title,
            minutes.takeIf { it > 0 }?.let { "$it min" },
            listOfNotNull(episode.channel.ifBlank { null }, aired).joinToString(" ").ifEmpty { null },
        ).joinToString(" · ")
    }

    /** A group of iVysílání episodes (it has a season, or ČT episodes), not "Nahráno z antény". */
    fun isCtGroup(group: EpisodeGroup): Boolean =
        group.season != null || group.episodes.any { it.source == EpisodeSource.CT }

    /**
     * [show] with a season page ([page] = the answer of `?season=&page=`, its iVysílání group first): page 1
     * replaces the show's iVysílání group, a later page ([append]) adds its episodes; "Nahráno z antény" stays.
     */
    fun withSeasonPage(show: ArchiveShow, page: ArchiveShow, season: String, append: Boolean): ArchiveShow {
        val fetched = page.groups.firstOrNull { it.season == season } ?: page.groups.firstOrNull(::isCtGroup)
        val groups = show.groups.toMutableList()
        val index = groups.indexOfFirst(::isCtGroup)
        val current = groups.getOrNull(index)
        val episodes = fetched?.episodes.orEmpty()
        val merged = if (append && current != null) current.episodes + episodes else episodes
        val group = EpisodeGroup(fetched?.title ?: current?.title.orEmpty(), merged, season, fetched?.nextPage)
        if (index >= 0) groups[index] = group else groups.add(0, group)
        return show.copy(groups = groups)
    }

    /** The resume key of an episode (`ct:<idec>`, `dvb:<recording id>`), as the guide uses it. */
    fun resumeKey(episode: ArchiveEpisode): String = when (episode.source) {
        EpisodeSource.CT -> ResumeRules.ctKey(episode.idec.orEmpty())
        EpisodeSource.REC -> ResumeRules.dvbKey(episode.recordingId.orEmpty())
    }

    /** The CT channel an episode plays from within 3 h: its `channelId`, else its display name mapped; null = VOD only. */
    fun channelOf(episode: ArchiveEpisode): String? =
        episode.channelId?.takeIf { Channels.byId(it) != null } ?: ctChannelId(episode.channel)

    /** The CT channel id (`CH_1`) of an episode's channel, given as id, name (`ČT1`) or slug (`ct1`); else null. */
    fun ctChannelId(channel: String): String? {
        val wanted = channel.trim()
        return Channels.all.firstOrNull {
            it.id == wanted || it.name.equals(wanted, ignoreCase = true) || it.slug.equals(wanted, ignoreCase = true)
        }?.id
    }

    /**
     * What playing an episode asks for: an iVysílání episode as a CT programme (the decision rules choose VOD or,
     * within 3 h of airing, the buffer), our recording as a gateway recording (the T05 timeline player for `b…` ids).
     * [resumeSeconds] = "Pokračovat od …".
     */
    fun request(episode: ArchiveEpisode, resumeSeconds: Double? = null): PlayRequest {
        val start = episode.aired ?: Instant.EPOCH
        return when (episode.source) {
            EpisodeSource.CT -> PlayRequest.Programme(
                channelId = channelOf(episode).orEmpty(),
                title = episode.title,
                idec = episode.idec.orEmpty(),
                start = start,
                end = start.plusMillis((episode.durationSeconds * 1000).toLong()),
                isPlayableNow = episode.playable,
                resumeSeconds = resumeSeconds,
            )
            EpisodeSource.REC -> PlayRequest.DvbRecorded(
                channelId = episode.channel,
                title = episode.title,
                recordingId = episode.recordingId.orEmpty(),
                fromStart = false,
                lengthSeconds = episode.durationSeconds.takeIf { it > 0 },
                resumeSeconds = resumeSeconds,
            )
        }
    }

    /** What the "Pokračovat ve sledování" row keeps with the entry of an episode of [card]. */
    fun resumeCard(episode: ArchiveEpisode, card: ArchiveCard): ResumeCard = ResumeCard(
        title = episode.title,
        showTitle = card.title.takeIf { it != episode.title },
        image = card.image,
        source = ResumeRules.source(resumeKey(episode)),
        start = episode.aired,
        channelId = when (episode.source) {
            EpisodeSource.CT -> channelOf(episode)
            EpisodeSource.REC -> episode.channel.ifBlank { null }
        },
    )

    /**
     * Selecting a card of "Pokračovat ve sledování" plays at once from the saved position (as "Pokračovat od …"):
     * a CT entry as a programme (the decision rules pick VOD or the buffer by its start), an Anténa entry as its
     * recording. Null for a key of another kind.
     */
    fun continueRequest(item: ContinueItem): PlayRequest? {
        val card = item.entry.card ?: return null
        val progress = item.entry.progressSeconds
        return when (ResumeRules.source(item.key)) {
            "ct" -> {
                val start = card.start ?: Instant.EPOCH
                PlayRequest.Programme(
                    channelId = card.channelId.orEmpty(),
                    title = card.title,
                    idec = item.key.substringAfter(':'),
                    start = start,
                    end = start.plusMillis((item.entry.lengthSeconds * 1000).toLong()),
                    isPlayableNow = true, // not known here: an unplayable one shows ČT's text
                    resumeSeconds = progress,
                )
            }
            "dvb" -> PlayRequest.DvbRecorded(
                channelId = card.channelId.orEmpty(),
                title = card.title,
                recordingId = item.key.substringAfter(':'),
                fromStart = false,
                lengthSeconds = item.entry.lengthSeconds,
                resumeSeconds = progress,
            )
            else -> null
        }
    }
}
