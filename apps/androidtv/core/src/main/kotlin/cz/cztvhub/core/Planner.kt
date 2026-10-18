package cz.cztvhub.core

import cz.cztvhub.core.dvb.DvbClient
import cz.cztvhub.core.dvb.DvbMessages
import java.time.Instant

/** What the viewer asked to play. Holds no stream URLs, so it is safe to keep in an Intent. */
sealed interface PlayRequest {
    val channelId: String
    val title: String

    data class Live(override val channelId: String, override val title: String) : PlayRequest

    /** Airing or recently aired programme, played from the rewind buffer. */
    data class Startover(
        override val channelId: String,
        override val title: String,
        val start: Instant,
    ) : PlayRequest

    /** A programme picked in the guide; the decision rules choose how to play it. */
    data class Programme(
        override val channelId: String,
        override val title: String,
        val idec: String,
        val start: Instant,
        val end: Instant,
        val isPlayableNow: Boolean,
    ) : PlayRequest

    /** Live from the household's own tuner through the gateway ("Anténa"). */
    data class DvbLive(override val channelId: String, override val title: String) : PlayRequest

    /** A gateway recording; [fromStart] plays a still-growing recording from its beginning. */
    data class DvbRecorded(
        override val channelId: String,
        override val title: String,
        val recordingId: String,
        val fromStart: Boolean,
    ) : PlayRequest
}

enum class PlayKind { LIVE, TIMESHIFT, VOD }

/**
 * A freshly resolved stream. For [PlayKind.TIMESHIFT] the player should seek to [seekToWallClock]
 * using the playlist's program date-time, or to [fallbackOffsetMs] from the start of the buffer.
 */
data class PlayTarget(
    val url: String,
    val kind: PlayKind,
    val title: String,
    val seekToWallClock: Instant? = null,
    val fallbackOffsetMs: Long = 0,
    /** Sent on every request, playlists and segments alike (the gateway's bearer key). */
    val headers: Map<String, String> = emptyMap(),
    /** Explicit start position, e.g. 0 for a still-growing recording. */
    val startPositionMs: Long? = null,
)

/** Turns a [PlayRequest] into a playable URL, resolving on the device right before playback. */
class Planner(
    private val api: CtApi,
    private val live: LiveResolver = LiveResolver(api),
    private val clock: () -> Instant = Instant::now,
    private val dvb: () -> DvbClient? = { null },
) {
    /** Throws [CtException] with a Czech message when nothing can be played. */
    fun plan(request: PlayRequest, forceRefresh: Boolean = false): PlayTarget {
        when (request) {
            is PlayRequest.DvbLive -> {
                val client = dvb() ?: throw CtException("DVB not configured", DvbMessages.UNREACHABLE)
                return PlayTarget(client.liveUrl(request.channelId), PlayKind.LIVE, request.title, headers = client.config.authHeaders)
            }
            is PlayRequest.DvbRecorded -> {
                val client = dvb() ?: throw CtException("DVB not configured", DvbMessages.UNREACHABLE)
                return PlayTarget(
                    url = client.recordingUrl(request.recordingId),
                    kind = PlayKind.VOD,
                    title = request.title,
                    headers = client.config.authHeaders,
                    startPositionMs = if (request.fromStart) 0 else null,
                )
            }
            else -> Unit
        }
        val channel = Channels.byId(request.channelId)
            ?: throw CtException("unknown channel ${request.channelId}")
        return when (request) {
            is PlayRequest.Live -> {
                PlayTarget(live.resolve(channel.id, forceRefresh).main, PlayKind.LIVE, request.title)
            }
            is PlayRequest.Startover -> startover(channel, request.title, request.start, forceRefresh)
            is PlayRequest.Programme -> programme(channel, request, forceRefresh)
            is PlayRequest.DvbLive, is PlayRequest.DvbRecorded -> error("handled above")
        }
    }

    private fun programme(channel: Channel, request: PlayRequest.Programme, forceRefresh: Boolean): PlayTarget {
        val decision = Decisions.decide(request.start, request.end, request.isPlayableNow, clock())
        return when (decision.action) {
            Action.FUTURE -> throw CtException("${request.idec} has not started", Messages.FUTURE)
            Action.STARTOVER -> startover(channel, request.title, request.start, forceRefresh)
            // Not playable: still ask the API so the viewer gets CT's reason.
            Action.VOD, Action.VOD_UNAVAILABLE -> try {
                PlayTarget(api.vodUrl(request.idec), PlayKind.VOD, request.title)
            } catch (e: StillLiveException) {
                val airingOn = Channels.byId(e.channelId) ?: channel
                startover(airingOn, request.title, request.start, forceRefresh)
            }
        }
    }

    private fun startover(channel: Channel, title: String, start: Instant, forceRefresh: Boolean): PlayTarget {
        val timeshift = live.resolve(channel.id, forceRefresh).timeshift
            ?: throw CtException("${channel.id} has no timeshift", Messages.NO_TIMESHIFT)
        return PlayTarget(
            url = timeshift,
            kind = PlayKind.TIMESHIFT,
            title = title,
            seekToWallClock = start.minus(Decisions.START_MARGIN),
            fallbackOffsetMs = Decisions.startoverOffsetSeconds(start, clock()) * 1000,
        )
    }
}
