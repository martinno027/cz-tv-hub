package cz.cztvhub.tv

import android.annotation.SuppressLint
import android.graphics.Color
import android.os.Bundle
import android.os.SystemClock
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.annotation.OptIn
import androidx.lifecycle.lifecycleScope
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.Timeline
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.datasource.HttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.hls.HlsMediaSource
import androidx.media3.ui.PlayerView
import cz.cztvhub.core.CtException
import cz.cztvhub.core.PlayKind
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.PlayTarget
import cz.cztvhub.core.dvb.AdBreak
import cz.cztvhub.core.dvb.AdBreaks
import cz.cztvhub.core.dvb.DvbParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Full-screen playback. The stream is resolved here, on the device, right before playback;
 * on a playback error (typically an expired signed URL) it is re-resolved once and resumed.
 */
@OptIn(UnstableApi::class)
class PlayerActivity : ComponentActivity() {

    /** Where to continue after re-resolving. */
    private sealed interface Resume {
        data object Default : Resume
        data class Position(val ms: Long) : Resume
        data class WallClock(val epochMs: Long) : Resume
    }

    private lateinit var request: PlayRequest
    private lateinit var playerView: PlayerView
    private lateinit var message: TextView
    private lateinit var openApp: Button
    private lateinit var errorActions: LinearLayout
    private lateinit var skipAd: Button
    private var player: ExoPlayer? = null
    private var target: PlayTarget? = null
    private var loadJob: Job? = null
    private var resumeOnStart: Resume = Resume.Default

    /** Ad breaks of a finished gateway recording (spec/ad-breaks.md); empty when unknown or off. */
    private var adBreaks: List<AdBreak> = emptyList()
    private var adBreaksJob: Job? = null
    private var skipTargetSeconds: Double? = null

    /** Pending start-over seek, applied once the live window is known. */
    private var pendingWallClockMs: Long? = null
    private var pendingFallbackMs: Long = 0
    private var lastReResolveAt = 0L

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        request = PlayRequests.from(intent) ?: return finish()
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        playerView = PlayerView(this).apply {
            setShowSubtitleButton(true)
            setShowNextButton(false)
            setShowPreviousButton(false)
            controllerShowTimeoutMs = 4000
            keepScreenOn = true
        }
        message = TextView(this).apply {
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 28f)
            gravity = Gravity.CENTER
            setPadding(96, 48, 96, 48)
            visibility = View.GONE
        }
        // Error page for DRM-only CT items: CT's official app can play them, this one cannot.
        openApp = Button(this).apply {
            text = getString(R.string.open_ivysilani)
            setOnClickListener {
                OfficialAppLauncher.open(this@PlayerActivity)?.let { message.text = "${message.text}\n\n$it" }
            }
        }
        val back = Button(this).apply {
            text = getString(R.string.back)
            setOnClickListener { finish() }
        }
        errorActions = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            visibility = View.GONE
            addView(openApp, LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
            addView(back, LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
        }
        val messageBox = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            addView(message, LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
            addView(errorActions, LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
        }
        // "Přeskočit reklamu": bottom right above the controller's time bar, shown even when the controller is hidden.
        skipAd = Button(this).apply {
            text = getString(R.string.skip_ad)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 26f)
            isAllCaps = false
            setPadding(dp(32), dp(16), dp(32), dp(16))
            visibility = View.GONE
            setOnClickListener { skipAd() }
        }
        val skipParams = FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.WRAP_CONTENT,
            FrameLayout.LayoutParams.WRAP_CONTENT,
            Gravity.BOTTOM or Gravity.END,
        ).apply { setMargins(0, 0, dp(48), dp(120)) }
        val root = FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK)
            addView(playerView, FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT)
            addView(skipAd, skipParams)
            addView(messageBox, FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT)
        }
        setContentView(root)
    }

    override fun onStart() {
        super.onStart()
        if (!::request.isInitialized) return
        val exo = ExoPlayer.Builder(this).build().apply {
            trackSelectionParameters = trackSelectionParameters.buildUpon()
                .setPreferredAudioLanguage("cs")
                .setPreferredAudioRoleFlags(C.ROLE_FLAG_MAIN)
                .build()
            addListener(listener)
        }
        player = exo
        playerView.player = exo
        playerView.requestFocus()
        load(forceRefresh = false, resume = resumeOnStart)
        startAdBreaks()
    }

    override fun onStop() {
        super.onStop()
        // Release when hidden; a fresh URL is resolved when the viewer comes back.
        resumeOnStart = currentResume()
        loadJob?.cancel()
        adBreaksJob?.cancel()
        adBreaksJob = null
        updateSkipAd(null)
        playerView.player = null
        player?.release()
        player = null
    }

    private fun load(forceRefresh: Boolean, resume: Resume) {
        loadJob?.cancel()
        showMessage(getString(R.string.loading))
        loadJob = lifecycleScope.launch {
            val planned = try {
                withContext(Dispatchers.IO) { tvApp.planner.plan(request, forceRefresh) }
            } catch (e: CtException) {
                Log.w(TAG, "cannot play $request: ${e.message}")
                showMessage(e.userMessage)
                if (e.status == "UNSUPPORTED_DRM") {
                    errorActions.visibility = View.VISIBLE
                    openApp.requestFocus()
                }
                return@launch
            }
            val exo = player ?: return@launch
            target = planned
            pendingWallClockMs = null
            var startPositionMs: Long? = planned.startPositionMs
            when {
                resume is Resume.Position -> startPositionMs = resume.ms
                resume is Resume.WallClock -> {
                    pendingWallClockMs = resume.epochMs
                    pendingFallbackMs = 0
                }
                planned.kind == PlayKind.TIMESHIFT -> {
                    pendingWallClockMs = planned.seekToWallClock?.toEpochMilli()
                    pendingFallbackMs = planned.fallbackOffsetMs
                }
            }
            val item = MediaItem.Builder()
                .setUri(planned.url)
                .setMimeType(MimeTypes.APPLICATION_M3U8)
                .setMediaMetadata(MediaMetadata.Builder().setTitle(planned.title).build())
                .build()
            // Headers (the gateway's bearer key) go on every request: playlists and segments.
            val http = DefaultHttpDataSource.Factory()
                .setDefaultRequestProperties(planned.headers)
                .setReadTimeoutMs(HTTP_TIMEOUT_MS) // a gateway live start blocks until the first segment
                .setConnectTimeoutMs(HTTP_TIMEOUT_MS)
            val source = HlsMediaSource.Factory(http).createMediaSource(item)
            if (startPositionMs != null) exo.setMediaSource(source, startPositionMs) else exo.setMediaSource(source)
            // Hold playback until the start-over seek is done, so the live edge never flashes by.
            exo.playWhenReady = pendingWallClockMs == null
            exo.prepare()
            hideMessage()
        }
    }

    private val listener = object : Player.Listener {
        override fun onTimelineChanged(timeline: Timeline, reason: Int) {
            applyPendingSeek()
        }

        override fun onPlaybackStateChanged(playbackState: Int) {
            if (playbackState == Player.STATE_READY) applyPendingSeek()
        }

        override fun onPlayerError(error: PlaybackException) {
            val exo = player ?: return
            if (error.errorCode == PlaybackException.ERROR_CODE_BEHIND_LIVE_WINDOW) {
                exo.seekToDefaultPosition()
                exo.prepare()
                return
            }
            // The gateway explains tuner/stream problems in Czech (503 "Tuner je obsazený", 502).
            gatewayError(error)?.let {
                showMessage(it)
                return
            }
            // Re-resolve once per incident (signed URLs expire after ~1 h of playback).
            val now = SystemClock.elapsedRealtime()
            if (lastReResolveAt == 0L || now - lastReResolveAt > RE_RESOLVE_COOLDOWN_MS) {
                lastReResolveAt = now
                load(forceRefresh = true, resume = currentResume())
            } else {
                showMessage(getString(R.string.error_playback))
            }
        }
    }

    private fun gatewayError(error: PlaybackException): String? {
        if (target?.headers.isNullOrEmpty()) return null
        var cause: Throwable? = error
        while (cause != null && cause !is HttpDataSource.InvalidResponseCodeException) cause = cause.cause
        val http = cause ?: return null
        if (http.responseCode != 502 && http.responseCode != 503) return null
        return DvbParser.errorText(http.responseBody.toString(Charsets.UTF_8)) ?: getString(R.string.error_playback)
    }

    /** Seek by wall clock (EXT-X-PROGRAM-DATE-TIME) when known, else by the computed buffer offset. */
    private fun applyPendingSeek() {
        val wallClock = pendingWallClockMs ?: return
        val exo = player ?: return
        val timeline = exo.currentTimeline
        if (timeline.isEmpty) return
        val window = timeline.getWindow(exo.currentMediaItemIndex, Timeline.Window())
        if (window.isPlaceholder || window.durationMs == C.TIME_UNSET) return
        val position = if (window.windowStartTimeMs != C.TIME_UNSET) {
            wallClock - window.windowStartTimeMs
        } else {
            pendingFallbackMs
        }
        pendingWallClockMs = null
        exo.seekTo(position.coerceIn(0, maxOf(0, window.durationMs - LIVE_EDGE_GUARD_MS)))
        exo.playWhenReady = true
    }

    private fun currentResume(): Resume {
        val exo = player ?: return resumeOnStart
        pendingWallClockMs?.let { return Resume.WallClock(it) }
        return when (target?.kind) {
            PlayKind.VOD -> Resume.Position(exo.currentPosition)
            PlayKind.TIMESHIFT -> {
                val timeline = exo.currentTimeline
                if (timeline.isEmpty) return Resume.Default
                val window = timeline.getWindow(exo.currentMediaItemIndex, Timeline.Window())
                if (window.windowStartTimeMs == C.TIME_UNSET) Resume.Default
                else Resume.WallClock(window.windowStartTimeMs + exo.currentPosition)
            }
            else -> Resume.Default
        }
    }

    /**
     * Ad breaks for finished gateway recordings only (a still-growing one gets no button in v1): fetched on start
     * and every minute, the position checked twice a second. Gateway failures are silent: no button.
     */
    private fun startAdBreaks() {
        val recorded = request as? PlayRequest.DvbRecorded ?: return
        if (recorded.fromStart) return
        adBreaksJob?.cancel()
        adBreaksJob = lifecycleScope.launch {
            var fetchedAt = 0L
            while (isActive) {
                val now = SystemClock.elapsedRealtime()
                if (fetchedAt == 0L || now - fetchedAt >= AD_BREAKS_REFRESH_MS) {
                    fetchedAt = now
                    val client = tvApp.dvb()?.client
                    if (client != null) {
                        try {
                            adBreaks = withContext(Dispatchers.IO) { client.breaks(recorded.recordingId) }
                        } catch (e: CtException) {
                            Log.i(TAG, "no ad breaks: ${e.message}")
                        } catch (e: RuntimeException) {
                            Log.i(TAG, "no ad breaks: $e")
                        }
                    }
                }
                val exo = player
                val positionMs = exo?.currentPosition
                val usable = exo != null && target != null && message.visibility != View.VISIBLE &&
                    exo.playbackState != Player.STATE_IDLE && pendingWallClockMs == null
                updateSkipAd(if (usable && positionMs != null) AdBreaks.skipTarget(adBreaks, positionMs / 1000.0) else null)
                delay(SKIP_CHECK_MS)
            }
        }
    }

    private fun updateSkipAd(targetSeconds: Double?) {
        skipTargetSeconds = targetSeconds
        val visible = targetSeconds != null
        if (visible == (skipAd.visibility == View.VISIBLE)) return
        if (!visible && skipAd.hasFocus()) playerView.requestFocus()
        skipAd.visibility = if (visible) View.VISIBLE else View.GONE
    }

    private fun skipAd() {
        val seconds = skipTargetSeconds ?: return
        val exo = player ?: return
        exo.seekTo((seconds * 1000).toLong())
        updateSkipAd(null)
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun showMessage(text: String) {
        message.text = text
        message.visibility = View.VISIBLE
        updateSkipAd(null)
    }

    private fun hideMessage() {
        message.visibility = View.GONE
        errorActions.visibility = View.GONE
    }

    // Lint flags any dispatchKeyEvent override in a ComponentActivity (androidx.core restriction); false positive.
    @SuppressLint("RestrictedApi")
    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (event.keyCode == KeyEvent.KEYCODE_BACK && playerView.isControllerFullyVisible) {
            if (event.action == KeyEvent.ACTION_UP) playerView.hideController()
            return true
        }
        // OK presses "Přeskočit reklamu" while it is shown, unless the viewer is working in the open controller.
        if (isSelectKey(event.keyCode) && skipAd.visibility == View.VISIBLE &&
            (skipAd.hasFocus() || !playerView.isControllerFullyVisible)
        ) {
            if (event.action == KeyEvent.ACTION_UP) skipAd()
            return true
        }
        if (event.keyCode != KeyEvent.KEYCODE_BACK && message.visibility != View.VISIBLE &&
            playerView.dispatchKeyEvent(event)
        ) {
            return true
        }
        return super.dispatchKeyEvent(event)
    }

    private fun isSelectKey(keyCode: Int): Boolean =
        keyCode == KeyEvent.KEYCODE_DPAD_CENTER || keyCode == KeyEvent.KEYCODE_ENTER ||
            keyCode == KeyEvent.KEYCODE_NUMPAD_ENTER

    private companion object {
        const val TAG = "PlayerActivity"
        const val AD_BREAKS_REFRESH_MS = 60_000L
        const val SKIP_CHECK_MS = 500L
        const val HTTP_TIMEOUT_MS = 20_000
        const val RE_RESOLVE_COOLDOWN_MS = 5 * 60 * 1000L
        const val LIVE_EDGE_GUARD_MS = 10_000L
    }
}
