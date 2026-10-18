package cz.cztvhub.tv

import android.content.Context
import android.content.Intent
import cz.cztvhub.core.PlayRequest
import java.time.Instant

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
