package cz.cztvhub.core.archive

/**
 * How a part of the archive waits for the gateway (spec/apps.md "Loading is always visible", T52): not at all, for
 * new content that replaces what it lists (a menu item, sub-genre, sort, search, category; a season, year or month
 * chip), or for its next page.
 */
enum class Wait { NONE, REPLACE, MORE }

/** Where a part draws its loader (T52): none, a ring over its dimmed content, or a small ring below its last row. */
enum class LoaderPlace { NONE, OVER, BELOW }

/** The loader rule of T52, shared by the grid, the search results and the show page. */
object ArchiveLoading {
    /** A loader shows only after this long: a fast answer shows none, no flash. */
    const val DELAY_MS = 150L

    /** The opacity of a part's old content while its loader shows. */
    const val DIM_ALPHA = 0.35f

    /**
     * The ring's size, and the small one below a list's last row (half). The spec's 64 / 32 are 1080p screen pixels
     * (the Tizen/tvOS space); Android TV lays out about 960×540 dp, so half of them in dp (lead's decision, T52).
     */
    const val SIZE_DP = 32
    const val SMALL_SIZE_DP = SIZE_DP / 2

    /** The loader of a part that has waited [waitedMs] in [wait]. */
    fun place(wait: Wait, waitedMs: Long): LoaderPlace = when {
        wait == Wait.NONE || waitedMs < DELAY_MS -> LoaderPlace.NONE
        wait == Wait.MORE -> LoaderPlace.BELOW
        else -> LoaderPlace.OVER
    }

    /** The old content is dimmed only while a loader is over it (a page below is added to what stays bright). */
    fun dimmed(place: LoaderPlace): Boolean = place == LoaderPlace.OVER

    /**
     * What a failed episode load returns the chips to: the choice whose episodes are listed. While a load waits the
     * chips already show the waited choice, so a newer chip chosen meanwhile must return to [listed] (kept from the
     * first wait), not to the waited one that never arrived.
     */
    fun listedChoice(waiting: Boolean, listed: EpisodeChoice?, selected: EpisodeChoice): EpisodeChoice =
        if (waiting) listed ?: selected else selected
}

/** The season and period chips an episode list was asked with (T32, T52). */
data class EpisodeChoice(val season: String?, val year: Int?, val month: Int?)

/**
 * A paged grid of cards (Naše nahrávky, a category, the search results; T23) with its T52 wait. New content keeps
 * the old cards listed (dimmed by the UI) until it arrives; only the newest request ([request]) is taken, an older
 * answer is dropped. [listing] counts the replacements shown (the UI starts a replaced grid from its top).
 */
data class ArchiveGrid(
    val title: String = "",
    val cards: List<ArchiveCard> = emptyList(),
    val nextPage: Int? = 1,
    val wait: Wait = Wait.NONE,
    /** The last request failed: with no cards the grid says so (with "Zkusit znovu"). */
    val error: String? = null,
    /** iVysílání did not answer (`200` with an `error`): said above what is listed (our recordings). */
    val notice: String? = null,
    val attribution: String? = null,
    /** An answer (or a failure) is listed; before the first one an empty grid is not "Nic nenalezeno". */
    val loaded: Boolean = false,
    val request: Int = 0,
    val listing: Int = 0,
) {
    val loading: Boolean get() = wait != Wait.NONE

    /** New content was chosen: page 1 is asked as request [request] + 1; the cards stay until it answers. */
    fun replacing(): ArchiveGrid = copy(wait = Wait.REPLACE, request = request + 1)

    /** The next page, or null when there is none or the grid already waits (a new choice wins over paging). */
    fun more(): ArchiveGrid? = if (nextPage == null || wait != Wait.NONE) null else copy(wait = Wait.MORE, error = null, request = request + 1)

    /** The page asked by the newest request. */
    val pageAsked: Int get() = if (wait == Wait.MORE) nextPage ?: 1 else 1

    /** [page] answered request [request]; an older request's answer changes nothing. */
    fun answered(request: Int, page: ArchivePage): ArchiveGrid {
        if (request != this.request) return this
        return when (wait) {
            Wait.NONE -> this
            Wait.REPLACE -> ArchiveGrid(
                title = page.title,
                cards = page.cards.distinctBy(ArchiveCard::id),
                nextPage = page.nextPage,
                notice = page.error,
                attribution = page.attribution,
                loaded = true,
                request = request,
                listing = listing + 1,
            )
            Wait.MORE -> copy(
                cards = (cards + page.cards).distinctBy(ArchiveCard::id),
                nextPage = page.nextPage,
                wait = Wait.NONE,
                notice = page.error,
                attribution = page.attribution ?: attribution,
            )
        }
    }

    /**
     * Request [request] failed with [message]: new content lists nothing but the message (its page 1 can be asked
     * again); a next page keeps what is listed (asked again when the focus nears the end).
     */
    fun failed(request: Int, message: String): ArchiveGrid {
        if (request != this.request) return this
        return when (wait) {
            Wait.NONE -> this
            Wait.REPLACE -> ArchiveGrid(error = message, loaded = true, request = request, listing = listing + 1)
            Wait.MORE -> copy(wait = Wait.NONE, error = message)
        }
    }
}
