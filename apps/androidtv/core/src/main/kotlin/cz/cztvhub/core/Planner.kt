package cz.cztvhub.core

import cz.cztvhub.core.dvb.DvbClient
import cz.cztvhub.core.dvb.DvbMessages
import cz.cztvhub.core.dvb.DvbTimeline
import cz.cztvhub.core.dvb.TimelineRules
import java.time.Instant

/** What the viewer asked to play. Holds no stream URLs, so it is safe to keep in an Intent. */
sealed interface PlayRequest {
    val channelId: String
    val title: String

    data class Live(override val channelId: String, override val title: String) : PlayRequest

    /**
     * Airing or recently aired programme, played from the rewind buffer. [idec] and [end] identify the
     * programme for "Pokračovat"; [resumeSeconds] continues from that point instead of the start.
     */
    data class Startover(
        override val channelId: String,
        override val title: String,
        val start: Instant,
        val idec: String? = null,
        val end: Instant? = null,
        val resumeSeconds: Double? = null,
    ) : PlayRequest

    /** A programme picked in the guide; the decision rules choose how to play it. */
    data class Programme(
        override val channelId: String,
        override val title: String,
        val idec: String,
        val start: Instant,
        val end: Instant,
        val isPlayableNow: Boolean,
        /** "Pokračovat": seconds from the programme's start. */
        val resumeSeconds: Double? = null,
    ) : PlayRequest

    /** Live from the household's own tuner through the gateway ("Anténa"). */
    data class DvbLive(override val channelId: String, override val title: String) : PlayRequest

    /**
     * A gateway recording; [fromStart] plays a still-growing recording from its beginning. [lengthSeconds]
     * (the EPG event's length) lets the player remember progress; [resumeSeconds] starts there instead.
     * A continuous-recording id (`b…`) plays in its channel timeline, where [resumeSeconds] counts from the
     * programme's start (spec/apps.md "Anténa timeline player").
     */
    data class DvbRecorded(
        override val channelId: String,
        override val title: String,
        val recordingId: String,
        val fromStart: Boolean,
        val lengthSeconds: Double? = null,
        val resumeSeconds: Double? = null,
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
    /** The channel timeline window a `b…` recording plays in; [startPositionMs] is then mapped through it. */
    val timeline: DvbTimeline? = null,
)

/** Turns a [PlayRequest] into a playable URL, resolving on the device right before playback. */
class Planner(
    private val api: CtApi,
    private val live: LiveResolver = LiveResolver(api),
    private val clock: () -> Instant = Instant::now,
    private val dvb: () -> DvbClient? = { null },
) {
    /**
     * Throws [CtException] with a Czech message when nothing can be played. [timelineAt] opens a timeline
     * window at that wall clock (a hand-off to a neighbouring window, or a re-open) instead of the programme.
     */
    fun plan(request: PlayRequest, forceRefresh: Boolean = false, timelineAt: Instant? = null): PlayTarget {
        when (request) {
            is PlayRequest.DvbLive -> {
                val client = dvb() ?: throw CtException("DVB not configured", DvbMessages.UNREACHABLE)
                return PlayTarget(client.liveUrl(request.channelId), PlayKind.LIVE, request.title, headers = client.config.authHeaders)
            }
            is PlayRequest.DvbRecorded -> {
                val client = dvb() ?: throw CtException("DVB not configured", DvbMessages.UNREACHABLE)
                if (TimelineRules.isTimelineId(request.recordingId)) {
                    val timeline = client.timeline(request.recordingId)
                    val position = TimelineRules.startPosition(timeline, request.resumeSeconds, timelineAt)
                    return PlayTarget(
                        url = client.timelineUrl(timeline),
                        kind = PlayKind.VOD,
                        title = request.title,
                        headers = client.config.authHeaders,
                        startPositionMs = Math.round(position * 1000),
                        timeline = timeline,
                    )
                }
                return PlayTarget(
                    url = client.recordingUrl(request.recordingId),
                    kind = PlayKind.VOD,
                    title = request.title,
                    headers = client.config.authHeaders,
                    startPositionMs = when {
                        request.resumeSeconds != null -> (request.resumeSeconds * 1000).toLong()
                        request.fromStart -> 0
                        else -> null
                    },
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
            is PlayRequest.Startover ->
                startover(channel, request.title, request.start, forceRefresh, request.resumeSeconds)
            is PlayRequest.Programme -> programme(channel, request, forceRefresh)
            is PlayRequest.DvbLive, is PlayRequest.DvbRecorded -> error("handled above")
        }
    }

    private fun programme(channel: Channel, request: PlayRequest.Programme, forceRefresh: Boolean): PlayTarget {
        val decision = Decisions.decide(request.start, request.end, request.isPlayableNow, clock())
        return when (decision.action) {
            Action.FUTURE -> throw CtException("${request.idec} has not started", Messages.FUTURE)
            Action.STARTOVER -> startover(channel, request.title, request.start, forceRefresh, request.resumeSeconds)
            // Not playable: still ask the API so the viewer gets CT's reason.
            Action.VOD, Action.VOD_UNAVAILABLE -> try {
                PlayTarget(
                    api.vodUrl(request.idec),
                    PlayKind.VOD,
                    request.title,
                    startPositionMs = request.resumeSeconds?.let { (it * 1000).toLong() },
                )
            } catch (e: StillLiveException) {
                val airingOn = Channels.byId(e.channelId) ?: channel
                startover(airingOn, request.title, request.start, forceRefresh, request.resumeSeconds)
            }
        }
    }

    /** From the programme's start (minus a margin), or from [resumeSeconds] into it ("Pokračovat"). */
    private fun startover(
        channel: Channel,
        title: String,
        start: Instant,
        forceRefresh: Boolean,
        resumeSeconds: Double? = null,
    ): PlayTarget {
        val timeshift = live.resolve(channel.id, forceRefresh).timeshift
            ?: throw CtException("${channel.id} has no timeshift", Messages.NO_TIMESHIFT)
        if (resumeSeconds != null) {
            return PlayTarget(
                url = timeshift,
                kind = PlayKind.TIMESHIFT,
                title = title,
                seekToWallClock = ResumeRules.wallClock(start, resumeSeconds),
                fallbackOffsetMs = ResumeRules.timeshiftOffsetSeconds(start, resumeSeconds, clock()) * 1000,
            )
        }
        return PlayTarget(
            url = timeshift,
            kind = PlayKind.TIMESHIFT,
            title = title,
            seekToWallClock = start.minus(Decisions.START_MARGIN),
            fallbackOffsetMs = Decisions.startoverOffsetSeconds(start, clock()) * 1000,
        )
    }
}
