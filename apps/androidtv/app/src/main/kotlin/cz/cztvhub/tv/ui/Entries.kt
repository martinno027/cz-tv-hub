package cz.cztvhub.tv.ui

import cz.cztvhub.core.Action
import cz.cztvhub.core.Channel
import cz.cztvhub.core.Decisions
import cz.cztvhub.core.Messages
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.Programme
import cz.cztvhub.core.PspMessages
import cz.cztvhub.core.PspState
import cz.cztvhub.core.PspStream
import cz.cztvhub.core.ResumeRules
import cz.cztvhub.core.Timed
import cz.cztvhub.core.dvb.DvbAction
import cz.cztvhub.core.dvb.DvbChannel
import cz.cztvhub.core.dvb.DvbDecisions
import cz.cztvhub.core.dvb.DvbEvent
import cz.cztvhub.core.dvb.DvbMessages
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

/** What selecting a cell does. */
sealed interface Selection {
    data class Notice(val text: String) : Selection
    data class Play(val request: PlayRequest) : Selection

    /**
     * Airing: offer [live], and [fromStart] when the source can start over. With a remembered position
     * ("Pokračovat od [resumeLabel]") [resume] comes first; an ended programme then has no [live].
     */
    data class Choice(
        val entry: Entry,
        val live: PlayRequest?,
        val fromStart: PlayRequest?,
        val resume: PlayRequest? = null,
        val resumeLabel: String? = null,
    ) : Selection
}

/** Remembered progress (seconds) to offer for a resume key (`ct:<idec>`, `dvb:<id>`), or null. */
typealias ResumeOffers = (key: String) -> Double?

/** [psp] = the stream's state, for a [PspEntry] only (live only: no resume, no start-over). */
fun Entry.select(now: Instant, offers: ResumeOffers = { null }, psp: PspState = PspState.UNKNOWN): Selection = when (this) {
    is CtEntry -> {
        val key = ResumeRules.ctKey(programme.idec)
        when {
            Decisions.decide(programme, now).action == Action.FUTURE -> Selection.Notice(Messages.FUTURE)
            isAiring(now) -> {
                val fromStart = PlayRequest.Startover(channel.id, programme.title, programme.start, programme.idec, end)
                val offer = offers(key)
                Selection.Choice(
                    this,
                    live = PlayRequest.Live(channel.id, channel.name),
                    fromStart = fromStart,
                    resume = offer?.let { fromStart.copy(resumeSeconds = it) },
                    resumeLabel = offer?.let(ResumeRules::label),
                )
            }
            else -> {
                val play = PlayRequest.Programme(channel.id, title, programme.idec, start, end, programme.isPlayableNow)
                val offer = offers(key)
                if (offer == null) {
                    Selection.Play(play)
                } else {
                    Selection.Choice(this, null, play, play.copy(resumeSeconds = offer), ResumeRules.label(offer))
                }
            }
        }
    }
    is DvbEntry -> {
        val live = PlayRequest.DvbLive(channel.id, channel.name)
        val recordingId = event.recording?.id
        val length = Duration.between(start, end).toMillis() / 1000.0
        val offer = recordingId?.let { offers(ResumeRules.dvbKey(it)) }
        when (DvbDecisions.decide(event, now)) {
            DvbAction.FUTURE -> Selection.Notice(Messages.FUTURE)
            DvbAction.LIVE -> Selection.Choice(this, live, fromStart = null)
            DvbAction.LIVE_OR_STARTOVER -> {
                val fromStart = PlayRequest.DvbRecorded(channel.id, title, recordingId!!, fromStart = true, lengthSeconds = length)
                Selection.Choice(
                    this,
                    live,
                    fromStart = fromStart,
                    resume = offer?.let { fromStart.copy(resumeSeconds = it) },
                    resumeLabel = offer?.let(ResumeRules::label),
                )
            }
            DvbAction.RECORDING -> {
                val play = PlayRequest.DvbRecorded(channel.id, title, recordingId!!, fromStart = false, lengthSeconds = length)
                if (offer == null) {
                    Selection.Play(play)
                } else {
                    Selection.Choice(this, null, play, play.copy(resumeSeconds = offer), ResumeRules.label(offer))
                }
            }
            DvbAction.NOT_RECORDED -> Selection.Notice(DvbMessages.NOT_RECORDED)
        }
    }
    is PspEntry -> when {
        !isAiring(now) -> Selection.Notice(PspMessages.LIVE_ONLY)
        psp == PspState.OFF -> Selection.Notice(PspMessages.NOT_ON)
        else -> Selection.Play(PlayRequest.PspLive(stream.id, stream.name))
    }
}
