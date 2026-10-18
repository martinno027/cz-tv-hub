package cz.cztvhub.core

import java.time.Instant

/** A channel of the CT live API. [slug] is its name in the programme (GraphQL) API. */
data class Channel(val id: String, val name: String, val slug: String)

object Channels {
    // CH_7 (ČT3) returned 404 on 2026-10-17; re-check before adding it back.
    val all: List<Channel> = listOf(
        Channel("CH_1", "ČT1", "ct1"),
        Channel("CH_2", "ČT2", "ct2"),
        Channel("CH_24", "ČT24", "ct24"),
        Channel("CH_4", "ČT sport", "sport"),
        Channel("CH_5", "ČT :D", "decko"),
        Channel("CH_6", "ČT art", "art"),
    )

    fun byId(id: String): Channel? = all.firstOrNull { it.id == id }
}

/** Signed HLS URLs of a live channel. Short-lived and IP-bound: never persist them. */
data class LiveStreams(val main: String, val timeshift: String?)

/** Anything with a time span that the guide lays out. */
interface Timed {
    val start: Instant
    val end: Instant
}

data class Programme(
    val idec: String,
    val title: String,
    override val start: Instant,
    override val end: Instant,
    /** Available in the archive (iVysílání) right now. */
    val isPlayableNow: Boolean,
    val liveOnly: Boolean = false,
    val episodeTitle: String = "",
    val description: String = "",
    val genre: String = "",
    val imageUrl: String = "",
) : Timed {
    fun isAiring(now: Instant): Boolean = !now.isBefore(start) && now.isBefore(end)
}

/**
 * The API gave no playable stream. [userMessage] is short Czech text for the viewer,
 * CT's own wording when the API provides one.
 */
open class CtException(
    message: String,
    val userMessage: String = Messages.GENERIC,
    /** CT's `message.status.name` of an HTTP error, e.g. `MISSING_LICENCE_NOW`. */
    val status: String? = null,
) : Exception(message)

/** The archive item is airing right now on [channelId]; play it from the rewind buffer instead. */
class StillLiveException(val idec: String, val channelId: String) :
    CtException("$idec is airing live on $channelId", "Pořad se právě vysílá")

object Messages {
    const val GENERIC = "Stream se nepodařilo načíst"
    const val BLOCKED = "Vysílání je blokováno"
    const val VIDEO_UNAVAILABLE = "Video není k dispozici"
    const val SCHEDULE_FAILED = "Program se nepodařilo načíst"
    const val FUTURE = "Pořad ještě nezačal"
    const val NO_TIMESHIFT = "Zpět v čase není pro tento kanál k dispozici"
    const val DRM_HINT = "Pořad můžete pustit v oficiální aplikaci iVysílání."
}
