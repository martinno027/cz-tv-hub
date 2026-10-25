package cz.cztvhub.core

import java.math.BigDecimal
import java.math.RoundingMode
import java.time.Duration
import java.time.Instant

/** The programme page's buttons, in the order shown (spec/apps.md "Programme page (T15)"). */
enum class PageButton { RESUME, START_OVER, LIVE, OPEN_OFFICIAL_APP }

enum class PageSource { CT, DVB }

enum class PageState { FUTURE, AIRING, ENDED }

/** Decisions and texts of the programme page; golden cases in `spec/fixtures/programme_page_cases.json`. */
object ProgrammePage {
    /** TMDb ratings with fewer votes are not shown (the gateway drops them too). */
    const val MIN_VOTES = 20

    /**
     * [startable]: an airing programme can play from its start (CT buffer, Anténa recording in state `recording`);
     * [playable]: an ended Anténa programme was recorded (`completed`). An ended CT programme is always offered:
     * playing it shows CT's own text when it cannot be played (DRM, licence), as the decision rules do.
     * [resume]: a "Pokračovat" offer exists, shown only together with a playable start.
     */
    fun buttons(source: PageSource, state: PageState, startable: Boolean, playable: Boolean, resume: Boolean): List<PageButton> {
        val canStart = when (state) {
            PageState.FUTURE -> false
            PageState.AIRING -> startable
            PageState.ENDED -> playable || source == PageSource.CT
        }
        val list = ArrayList<PageButton>()
        if (canStart && resume) list += PageButton.RESUME
        if (canStart) list += PageButton.START_OVER
        if (state == PageState.AIRING) list += PageButton.LIVE
        if (source == PageSource.CT && state != PageState.FUTURE) list += PageButton.OPEN_OFFICIAL_APP
        return list
    }

    /** "Hodnocení TMDB 7,4 (1 234 hlasů)", or null below [MIN_VOTES] votes. */
    fun ratingLine(value: Double, votes: Int): String? {
        if (votes < MIN_VOTES || !value.isFinite()) return null
        return "Hodnocení TMDB ${ratingValue(value)} (${thousands(votes)} ${votesWord(votes)})"
    }

    /** One decimal, half up, decimal comma: `7,4` (also the archive cards' "★ 7,4"). */
    fun ratingValue(value: Double): String =
        BigDecimal(value.toString()).setScale(1, RoundingMode.HALF_UP).toPlainString().replace('.', ',')

    /** Czech plural of "hlas" (CLDR cs: 1 one, 2–4 few, else other; whole numbers only). */
    fun votesWord(n: Int): String = when (n) {
        1 -> "hlas"
        in 2..4 -> "hlasy"
        else -> "hlasů"
    }

    /**
     * "Původní název: …" when TMDb's original title differs from both the shown [title] and TMDb's Czech
     * [tmdbTitle] (case and surrounding spaces ignored), else null.
     */
    fun originalTitleLine(title: String, tmdbTitle: String?, originalTitle: String?): String? {
        val original = originalTitle?.trim().orEmpty()
        if (original.isEmpty()) return null
        if (listOfNotNull(title, tmdbTitle).any { original.equals(it.trim(), ignoreCase = true) }) return null
        return "Původní název: $original"
    }

    /** The facts line's genre: the guide's, else the first two of TMDb's. */
    fun genre(guideGenre: String, tmdbGenres: List<String>): String =
        guideGenre.trim().ifEmpty { tmdbGenres.filter(String::isNotBlank).take(2).joinToString(", ") }

    /** The facts line's length in whole minutes: broadcast end − start, rounded (never TMDb's runtime). */
    fun minutes(start: Instant, end: Instant): Long = (Duration.between(start, end).seconds.coerceAtLeast(0) + 30) / 60

    fun thousands(n: Int): String = n.toString().reversed().chunked(3).joinToString(" ").reversed()
}
