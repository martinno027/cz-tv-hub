package cz.cztvhub.tv

import android.content.Context
import android.content.Intent
import android.view.KeyEvent
import cz.cztvhub.core.ChannelPrefs
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.ResumeCard
import cz.cztvhub.core.ResumeEntry
import java.time.Instant

/** A channel the live player can zap to: its "Kanály" key and name (spec/apps.md "Channel numbers and zapping"). */
data class ZapChannel(val key: String, val name: String) {
    /** Live playback of this channel; null for an unknown key kind. */
    fun liveRequest(): PlayRequest? = when {
        key.startsWith(CT_PREFIX) -> PlayRequest.Live(key.removePrefix(CT_PREFIX), name)
        key.startsWith(DVB_PREFIX) -> PlayRequest.DvbLive(key.removePrefix(DVB_PREFIX), name)
        key.startsWith(PSP_PREFIX) -> PlayRequest.PspLive(key.removePrefix(PSP_PREFIX), name)
        else -> null
    }

    companion object {
        private val CT_PREFIX = ChannelPrefs.ctKey("")
        private val DVB_PREFIX = ChannelPrefs.dvbKey("")
        private val PSP_PREFIX = ChannelPrefs.pspKey("")

        /** The "Kanály" key of a live request; null for anything else. */
        fun keyOf(request: PlayRequest): String? = when (request) {
            is PlayRequest.Live -> ChannelPrefs.ctKey(request.channelId)
            is PlayRequest.DvbLive -> ChannelPrefs.dvbKey(request.channelId)
            is PlayRequest.PspLive -> ChannelPrefs.pspKey(request.channelId)
            else -> null
        }

        /** The digit of a number key (top row or numpad), else null. */
        fun digit(keyCode: Int): Int? = when (keyCode) {
            in KeyEvent.KEYCODE_0..KeyEvent.KEYCODE_9 -> keyCode - KeyEvent.KEYCODE_0
            in KeyEvent.KEYCODE_NUMPAD_0..KeyEvent.KEYCODE_NUMPAD_9 -> keyCode - KeyEvent.KEYCODE_NUMPAD_0
            else -> null
        }
    }
}

/** Encodes a [PlayRequest] (never a stream URL or key) into Intent extras for [PlayerActivity], resume point included. */
object PlayRequests {
    private const val KIND = "kind"
    private const val CHANNEL = "channel"
    private const val TITLE = "title"
    private const val IDEC = "idec"
    private const val START = "start"
    private const val END = "end"
    private const val PLAYABLE = "playable"
    private const val RECORDING = "recording"
    private const val FROM_START = "fromStart"
    private const val RESUME = "resumeSeconds"
    private const val LENGTH = "lengthSeconds"
    private const val ZAP_KEYS = "zapKeys"
    private const val ZAP_NAMES = "zapNames"
    private const val CARD = "resumeCard"

    /** Adds the visible channel list (in "Kanály" order) the live player zaps through. */
    fun withChannels(intent: Intent, channels: List<ZapChannel>): Intent = intent
        .putStringArrayListExtra(ZAP_KEYS, ArrayList(channels.map { it.key }))
        .putStringArrayListExtra(ZAP_NAMES, ArrayList(channels.map { it.name }))

    fun channels(intent: Intent): List<ZapChannel> {
        val keys = intent.getStringArrayListExtra(ZAP_KEYS).orEmpty()
        val names = intent.getStringArrayListExtra(ZAP_NAMES).orEmpty()
        if (keys.size != names.size) return emptyList()
        return keys.zip(names, ::ZapChannel)
    }

    /**
     * Adds what "Pokračovat ve sledování" keeps with the entry (T13): titles, the image URL or gateway path, the
     * programme start and channel; the stored entry's own format, so it never holds a stream URL.
     */
    fun withCard(intent: Intent, card: ResumeCard?): Intent {
        if (card == null) return intent
        return intent.putExtra(CARD, ResumeEntry(0.0, 0.0, Instant.EPOCH, card).encode())
    }

    fun card(intent: Intent): ResumeCard? = intent.getStringExtra(CARD)?.let(ResumeEntry::decode)?.card

    fun intent(context: Context, request: PlayRequest): Intent {
        val intent = Intent(context, PlayerActivity::class.java)
            .putExtra(CHANNEL, request.channelId)
            .putExtra(TITLE, request.title)
        when (request) {
            is PlayRequest.Live -> intent.putExtra(KIND, "live")
            is PlayRequest.Startover -> {
                intent.putExtra(KIND, "startover").putExtra(START, request.start.toEpochMilli())
                request.idec?.let { intent.putExtra(IDEC, it) }
                request.end?.let { intent.putExtra(END, it.toEpochMilli()) }
                request.resumeSeconds?.let { intent.putExtra(RESUME, it) }
            }
            is PlayRequest.Programme -> {
                intent.putExtra(KIND, "programme")
                    .putExtra(IDEC, request.idec)
                    .putExtra(START, request.start.toEpochMilli())
                    .putExtra(END, request.end.toEpochMilli())
                    .putExtra(PLAYABLE, request.isPlayableNow)
                request.resumeSeconds?.let { intent.putExtra(RESUME, it) }
            }
            is PlayRequest.DvbLive -> intent.putExtra(KIND, "dvbLive")
            is PlayRequest.PspLive -> intent.putExtra(KIND, "pspLive")
            is PlayRequest.DvbRecorded -> {
                intent.putExtra(KIND, "dvbRecorded")
                    .putExtra(RECORDING, request.recordingId)
                    .putExtra(FROM_START, request.fromStart)
                request.lengthSeconds?.let { intent.putExtra(LENGTH, it) }
                request.resumeSeconds?.let { intent.putExtra(RESUME, it) }
            }
        }
        return intent
    }

    fun from(intent: Intent): PlayRequest? {
        val channel = intent.getStringExtra(CHANNEL) ?: return null
        val title = intent.getStringExtra(TITLE).orEmpty()
        val start = Instant.ofEpochMilli(intent.getLongExtra(START, 0))
        val resume = intent.doubleOrNull(RESUME)
        return when (intent.getStringExtra(KIND)) {
            "live" -> PlayRequest.Live(channel, title)
            "startover" -> PlayRequest.Startover(
                channelId = channel,
                title = title,
                start = start,
                idec = intent.getStringExtra(IDEC),
                end = if (intent.hasExtra(END)) Instant.ofEpochMilli(intent.getLongExtra(END, 0)) else null,
                resumeSeconds = resume,
            )
            "programme" -> PlayRequest.Programme(
                channelId = channel,
                title = title,
                idec = intent.getStringExtra(IDEC) ?: return null,
                start = start,
                end = Instant.ofEpochMilli(intent.getLongExtra(END, 0)),
                isPlayableNow = intent.getBooleanExtra(PLAYABLE, false),
                resumeSeconds = resume,
            )
            "dvbLive" -> PlayRequest.DvbLive(channel, title)
            "pspLive" -> PlayRequest.PspLive(channel, title)
            "dvbRecorded" -> PlayRequest.DvbRecorded(
                channelId = channel,
                title = title,
                recordingId = intent.getStringExtra(RECORDING) ?: return null,
                fromStart = intent.getBooleanExtra(FROM_START, false),
                lengthSeconds = intent.doubleOrNull(LENGTH),
                resumeSeconds = resume,
            )
            else -> null
        }
    }

    private fun Intent.doubleOrNull(name: String): Double? = if (hasExtra(name)) getDoubleExtra(name, 0.0) else null
}
