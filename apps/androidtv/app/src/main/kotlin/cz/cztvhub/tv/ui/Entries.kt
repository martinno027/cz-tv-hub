package cz.cztvhub.tv.ui

import cz.cztvhub.core.Action
import cz.cztvhub.core.Channel
import cz.cztvhub.core.Decisions
import cz.cztvhub.core.PageButton
import cz.cztvhub.core.PageSource
import cz.cztvhub.core.PageState
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.Programme
import cz.cztvhub.core.ProgrammePage
import cz.cztvhub.core.PspMessages
import cz.cztvhub.core.PspState
import cz.cztvhub.core.PspStream
import cz.cztvhub.core.ResumeRules
import cz.cztvhub.core.Timed
import cz.cztvhub.core.dvb.DvbAction
import cz.cztvhub.core.dvb.DvbChannel
import cz.cztvhub.core.dvb.DvbDecisions
import cz.cztvhub.core.dvb.DvbEvent
import java.time.Duration
import java.time.Instant

/** A guide row: a CT internet channel, an "Anténa" channel or a Poslanecká sněmovna stream. */
data class GuideRow(val key: String, val name: String, val entries: List<Entry>)

/** What a guide cell shows, whichever source it comes from. */
sealed interface Entry : Timed {
    val title: String
    val subtitle: String
    val description: String
    val genre: String
    val imageUrl: String
    val channelName: String

    fun isAiring(now: Instant): Boolean = !now.isBefore(start) && now.isBefore(end)
}

data class CtEntry(val channel: Channel, val programme: Programme) : Entry {
    override val start get() = programme.start
    override val end get() = programme.end
    override val title get() = programme.title
    override val subtitle get() = programme.episodeTitle
    override val description get() = programme.description
    override val genre get() = programme.genre
    override val imageUrl get() = programme.imageUrl
    override val channelName get() = channel.name
}

data class DvbEntry(val channel: DvbChannel, val event: DvbEvent) : Entry {
    override val start get() = event.start
    override val end get() = event.end
    override val title get() = event.title
    override val subtitle get() = event.subtitle
    override val description get() = event.description
    override val genre get() = event.genre
    override val imageUrl get() = ""
    override val channelName get() = channel.name
}

/**
 * A Poslanecká sněmovna stream in the guide: one cell over the whole broadcast day ([start]–[end] = the grid's
 * axis), no programme data. Its on/off state is not part of the entry (it changes every minute and must not rebuild
 * the grid); [look] and [select] take it. Only the grid containing now ([isAiring]) offers the live stream.
 */
data class PspEntry(val stream: PspStream, override val start: Instant, override val end: Instant) : Entry {
    override val title get() = stream.name
    override val subtitle get() = ""
    override val description get() = ""
    override val genre get() = ""
    override val imageUrl get() = ""
    override val channelName get() = stream.name

    /** The cell's text: "Přímý přenos" / "Nevysílá se" / "Zjišťuji…" today, "Jen přímé přenosy" on other days. */
    fun cellTitle(now: Instant, state: PspState): String = when {
        !isAiring(now) -> PspMessages.LIVE_ONLY_CELL
        state == PspState.LIVE -> PspMessages.LIVE
        state == PspState.OFF -> PspMessages.OFF
        else -> PspMessages.CHECKING
    }
}

/** How a cell looks: dimmed when it cannot be played. */
enum class EntryLook { AIRING, PLAYABLE, FUTURE, DIMMED }

/** [psp] = the stream's state, for a [PspEntry] only. */
fun Entry.look(now: Instant, psp: PspState = PspState.UNKNOWN): EntryLook = when (this) {
    is CtEntry -> when {
        isAiring(now) -> EntryLook.AIRING
        else -> when (Decisions.decide(programme, now).action) {
            Action.FUTURE -> EntryLook.FUTURE
            Action.VOD_UNAVAILABLE -> EntryLook.DIMMED
            Action.VOD, Action.STARTOVER -> EntryLook.PLAYABLE
        }
    }
    is DvbEntry -> when (DvbDecisions.decide(event, now)) {
        DvbAction.LIVE, DvbAction.LIVE_OR_STARTOVER -> EntryLook.AIRING
        DvbAction.RECORDING -> EntryLook.PLAYABLE
        DvbAction.FUTURE -> EntryLook.FUTURE
        DvbAction.NOT_RECORDED -> EntryLook.DIMMED
    }
    is PspEntry -> when {
        !isAiring(now) || psp == PspState.OFF -> EntryLook.DIMMED
        psp == PspState.LIVE -> EntryLook.AIRING
        else -> EntryLook.PLAYABLE
    }
}

/** What selecting a Poslanecká sněmovna cell does (programmes open their page, [page]). */
sealed interface Selection {
    data class Notice(val text: String) : Selection
    data class Play(val request: PlayRequest) : Selection
}

/** Remembered progress (seconds) to offer for a resume key (`ct:<idec>`, `dvb:<id>`), or null. */
typealias ResumeOffers = (key: String) -> Double?

/** A button of the programme page; [request] is what it plays (null for "Otevřít v iVysílání"). */
data class PageAction(val button: PageButton, val request: PlayRequest?)

/**
 * The programme page of a CT or Anténa programme (spec/apps.md "Programme page (T15)"): its buttons in order
 * ([ProgrammePage.buttons]) with what each plays; [resumeLabel] for "Pokračovat od …".
 */
data class ProgrammePageModel(val entry: Entry, val actions: List<PageAction>, val resumeLabel: String? = null)

/** The page of a CT or Anténa programme, decided at [now]; null for a Poslanecká sněmovna stream (no page). */
fun Entry.page(now: Instant, offers: ResumeOffers = { null }): ProgrammePageModel? = when (this) {
    is CtEntry -> {
        val action = Decisions.decide(programme, now).action
        val airing = isAiring(now)
        val state = when {
            action == Action.FUTURE -> PageState.FUTURE
            airing -> PageState.AIRING
            else -> PageState.ENDED
        }
        // An airing CT programme is always offered from the start; a channel without a buffer says so in the player.
        val fromStart = if (airing) {
            PlayRequest.Startover(channel.id, programme.title, programme.start, programme.idec, end)
        } else {
            PlayRequest.Programme(channel.id, title, programme.idec, start, end, programme.isPlayableNow)
        }
        val offer = offers(ResumeRules.ctKey(programme.idec))
        val buttons = ProgrammePage.buttons(
            PageSource.CT, state, startable = true,
            playable = action == Action.VOD || action == Action.STARTOVER, resume = offer != null,
        ) // an ended CT programme is offered even when not marked playable: the player shows CT's text
        model(buttons, offer) { button ->
            when (button) {
                PageButton.RESUME -> when (fromStart) {
                    is PlayRequest.Startover -> fromStart.copy(resumeSeconds = offer)
                    is PlayRequest.Programme -> fromStart.copy(resumeSeconds = offer)
                    else -> null
                }
                PageButton.START_OVER -> fromStart
                PageButton.LIVE -> PlayRequest.Live(channel.id, channel.name)
                PageButton.OPEN_OFFICIAL_APP -> null
            }
        }
    }
    is DvbEntry -> {
        val action = DvbDecisions.decide(event, now)
        val state = when (action) {
            DvbAction.FUTURE -> PageState.FUTURE
            DvbAction.LIVE, DvbAction.LIVE_OR_STARTOVER -> PageState.AIRING
            DvbAction.RECORDING, DvbAction.NOT_RECORDED -> PageState.ENDED
        }
        val recordingId = event.recording?.id
        val length = Duration.between(start, end).toMillis() / 1000.0
        val offer = recordingId?.let { offers(ResumeRules.dvbKey(it)) }
        val buttons = ProgrammePage.buttons(
            PageSource.DVB, state, startable = action == DvbAction.LIVE_OR_STARTOVER,
            playable = action == DvbAction.RECORDING, resume = offer != null,
        )
        // A growing recording is asked for "from the start" explicitly (players start it at its live edge).
        val fromStart = recordingId?.let {
            PlayRequest.DvbRecorded(channel.id, title, it, fromStart = state == PageState.AIRING, lengthSeconds = length)
        }
        model(buttons, offer) { button ->
            when (button) {
                PageButton.RESUME -> fromStart?.copy(resumeSeconds = offer)
                PageButton.START_OVER -> fromStart
                PageButton.LIVE -> PlayRequest.DvbLive(channel.id, channel.name)
                PageButton.OPEN_OFFICIAL_APP -> null
            }
        }
    }
    is PspEntry -> null
}

private fun Entry.model(buttons: List<PageButton>, offer: Double?, request: (PageButton) -> PlayRequest?) =
    ProgrammePageModel(
        this,
        buttons.map { PageAction(it, request(it)) },
        offer?.takeIf { PageButton.RESUME in buttons }?.let(ResumeRules::label),
    )

/** Live only: no resume, no start-over. */
fun PspEntry.select(now: Instant, psp: PspState = PspState.UNKNOWN): Selection = when {
    !isAiring(now) -> Selection.Notice(PspMessages.LIVE_ONLY)
    psp == PspState.OFF -> Selection.Notice(PspMessages.NOT_ON)
    else -> Selection.Play(PlayRequest.PspLive(stream.id, stream.name))
}
