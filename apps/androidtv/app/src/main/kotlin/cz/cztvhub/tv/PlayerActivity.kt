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
import androidx.media3.common.TrackSelectionParameters
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.datasource.HttpDataSource
import androidx.media3.exoplayer.DefaultLoadControl
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.hls.HlsMediaSource
import androidx.media3.ui.PlayerView
import cz.cztvhub.core.ChannelNumbers
import cz.cztvhub.core.Channels
import cz.cztvhub.core.CtException
import cz.cztvhub.core.InfoTitle
import cz.cztvhub.core.PlayerTitle
import cz.cztvhub.core.PlayKind
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.PlayTarget
import cz.cztvhub.core.PspMessages
import cz.cztvhub.core.PspState
import cz.cztvhub.core.PspStreams
import cz.cztvhub.core.ResumeRules
import cz.cztvhub.core.ResumeSpot
import cz.cztvhub.core.dvb.AdBreak
import cz.cztvhub.core.dvb.AdBreaks
import cz.cztvhub.core.dvb.DvbParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.Instant

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

    /** Info bar: programme title over the channel, shown with the controller (spec "Title in the player's info bar"). */
    private lateinit var infoBox: LinearLayout
    private lateinit var infoTitle: TextView
    private lateinit var infoSubtitle: TextView
    private var titleJob: Job? = null
    private var player: ExoPlayer? = null
    private var target: PlayTarget? = null
    private var loadJob: Job? = null
    private var resumeOnStart: Resume = Resume.Default

    /** Czech by default; the track picked in the settings menu once the viewer chooses one (Anténa audio tracks). */
    private var audioPreference = AudioPreference.DEFAULT

    /** Ad breaks of a finished gateway recording (spec/ad-breaks.md); empty when unknown or off. */
    private var adBreaks: List<AdBreak> = emptyList()
    private var adBreaksJob: Job? = null
    private var skipTargetSeconds: Double? = null

    /** Where progress is remembered for "Pokračovat"; null for live. */
    private var spot: ResumeSpot? = null
    private var progressJob: Job? = null

    /** Pending start-over seek, applied once the live window is known. */
    private var pendingWallClockMs: Long? = null
    private var pendingFallbackMs: Long = 0
    private var lastReResolveAt = 0L

    /** Zapping (live only): the visible channels in "Kanály" order (spec/apps.md "Channel numbers and zapping"). */
    private var zapChannels: List<ZapChannel> = emptyList()
    private var zapTarget: ZapChannel? = null
    private var zapJob: Job? = null
    private lateinit var zapOverlay: TextView
    private var overlayJob: Job? = null
    private lateinit var digitBox: TextView
    private lateinit var digitEntry: DigitEntry

    /** OK used the number buffer: its key-up must not reach the controller. */
    private var swallowSelectUp = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        request = PlayRequests.from(intent) ?: return finish()
        spot = ResumeRules.spot(request)
        zapChannels = PlayRequests.channels(intent)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        playerView = PlayerView(this).apply {
            setShowSubtitleButton(true)
            setShowNextButton(false)
            setShowPreviousButton(false)
            controllerShowTimeoutMs = 4000
            keepScreenOn = true
            setControllerVisibilityListener(PlayerView.ControllerVisibilityListener { visibility ->
                infoBox.visibility = visibility
            })
        }
        infoTitle = TextView(this).apply {
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 34f)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
            setShadowLayer(8f, 0f, 2f, Color.BLACK)
        }
        infoSubtitle = TextView(this).apply {
            setTextColor(Color.argb(220, 255, 255, 255))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 22f)
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
            setShadowLayer(8f, 0f, 2f, Color.BLACK)
        }
        infoBox = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
            addView(infoTitle)
            addView(infoSubtitle)
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
        // Zapping: "<number>  <channel>" and the programme on air, top left; the number buffer top right.
        zapOverlay = TextView(this).apply {
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 28f)
            setBackgroundColor(Color.argb(200, 0, 0, 0))
            setPadding(dp(28), dp(16), dp(28), dp(16))
            maxLines = 2
            ellipsize = android.text.TextUtils.TruncateAt.END
            visibility = View.GONE
        }
        digitBox = TextView(this).apply {
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 56f)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setBackgroundColor(Color.argb(200, 0, 0, 0))
            setPadding(dp(28), dp(8), dp(28), dp(8))
            visibility = View.GONE
        }
        digitEntry = DigitEntry(
            lifecycleScope,
            count = { zapChannels.size },
            onBuffer = { buffer ->
                digitBox.text = if (buffer.isEmpty()) "" else "${buffer}_"
                digitBox.visibility = if (buffer.isEmpty()) View.GONE else View.VISIBLE
            },
            onNumber = ::pickNumber,
        )
        val root = FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK)
            addView(playerView, FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT)
            addView(skipAd, skipParams)
            addView(infoBox, FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM or Gravity.START,
            ).apply { setMargins(dp(48), 0, dp(320), dp(120)) })
            addView(messageBox, FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT)
            addView(zapOverlay, FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.WRAP_CONTENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.TOP or Gravity.START,
            ).apply { setMargins(dp(48), dp(36), dp(320), 0) })
            addView(digitBox, FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.WRAP_CONTENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.TOP or Gravity.END,
            ).apply { setMargins(0, dp(36), dp(48), 0) })
        }
        setContentView(root)
    }

    override fun onStart() {
        super.onStart()
        if (!::request.isInitialized) return
        // Start (or restart after a seek) once ~1 s is buffered instead of the default 2.5 s: live
        // channels begin sooner; after a stall the default (5 s) still applies.
        val loadControl = DefaultLoadControl.Builder()
            .setBufferDurationsMs(
                DefaultLoadControl.DEFAULT_MIN_BUFFER_MS,
                DefaultLoadControl.DEFAULT_MAX_BUFFER_MS,
                START_BUFFER_MS,
                DefaultLoadControl.DEFAULT_BUFFER_FOR_PLAYBACK_AFTER_REBUFFER_MS,
            )
            .build()
        val exo = ExoPlayer.Builder(this).setLoadControl(loadControl).build().apply {
            trackSelectionParameters = audioPreference.applyTo(trackSelectionParameters)
            addListener(listener)
        }
        player = exo
        playerView.player = exo
        playerView.requestFocus()
        load(forceRefresh = false, resume = resumeOnStart)
        startTitles()
        startAdBreaks()
        startProgressSaving()
    }

    override fun onStop() {
        super.onStop()
        saveProgress()
        progressJob?.cancel()
        progressJob = null
        // Release when hidden; a fresh URL is resolved when the viewer comes back.
        resumeOnStart = currentResume()
        // A zap still waiting for its delay: come back on that channel.
        zapJob?.cancel()
        zapJob = null
        zapTarget?.let { pending ->
            pending.liveRequest()?.let { setRequest(it) }
            resumeOnStart = Resume.Default
        }
        zapTarget = null
        digitEntry.cancel()
        overlayJob?.cancel()
        zapOverlay.visibility = View.GONE
        loadJob?.cancel()
        titleJob?.cancel()
        titleJob = null
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
            // Poslanecká sněmovna, right before playback: an answer younger than 60 s (e.g. the one Živě just made),
            // else one more check of the playlist. Off (e.g. zapped onto): the message instead of a player error.
            (request as? PlayRequest.PspLive)?.let { psp ->
                if (withContext(Dispatchers.IO) { tvApp.psp.stateForPlayback(psp.channelId) } == PspState.OFF) {
                    target = null
                    showMessage(getString(R.string.psp_not_on))
                    // A zap overlay still showing gets "Nevysílá se".
                    val zapped = zapChannels.firstOrNull { it.key == ZapChannel.keyOf(psp) }
                    if (zapped != null && zapOverlay.visibility == View.VISIBLE) showZapOverlay(zapped)
                    return@launch
                }
            }
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
            val http = if (request is PlayRequest.PspLive) {
                // Poslanecká sněmovna: its own data source, the published URL as is, no header of ours (no gateway
                // key, nothing faked).
                DefaultHttpDataSource.Factory()
                    .setReadTimeoutMs(HTTP_TIMEOUT_MS)
                    .setConnectTimeoutMs(HTTP_TIMEOUT_MS)
            } else {
                // Headers (the gateway's bearer key) go on every request: playlists and segments.
                DefaultHttpDataSource.Factory()
                    .setDefaultRequestProperties(planned.headers)
                    .setReadTimeoutMs(HTTP_TIMEOUT_MS) // a gateway live start blocks until the first segment
                    .setConnectTimeoutMs(HTTP_TIMEOUT_MS)
            }
            val source = HlsMediaSource.Factory(http).createMediaSource(item)
            // A master playlist with several audio renditions gets new track groups: the menu's override is stale.
            exo.trackSelectionParameters = audioPreference.applyTo(exo.trackSelectionParameters)
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

        override fun onTrackSelectionParametersChanged(parameters: TrackSelectionParameters) {
            AudioPreference.chosen(parameters)?.let { audioPreference = it }
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
            // Poslanecká sněmovna answers 404 outside broadcasts: the broadcast just ended (or never began).
            if (request is PlayRequest.PspLive && httpStatus(error) == 404) {
                exo.stop()
                showMessage(getString(R.string.psp_not_on))
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

    private fun httpStatus(error: PlaybackException): Int? {
        var cause: Throwable? = error
        while (cause != null && cause !is HttpDataSource.InvalidResponseCodeException) cause = cause.cause
        return (cause as? HttpDataSource.InvalidResponseCodeException)?.responseCode
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

    /** "Pokračovat": remember the position every [SAVE_INTERVAL_MS] while playing (and in [onStop]). */
    private fun startProgressSaving() {
        if (spot == null) return
        progressJob?.cancel()
        progressJob = lifecycleScope.launch {
            while (isActive) {
                delay(SAVE_INTERVAL_MS)
                if (player?.isPlaying == true) saveProgress()
            }
        }
    }

    private fun saveProgress() {
        val remembered = spot ?: return
        val seconds = progressSeconds(remembered) ?: return
        tvApp.resume.save(remembered.key, seconds, remembered.lengthSeconds)
    }

    /**
     * Seconds from the programme's start: the position for archive and recordings, the shown frame's wall
     * clock − programme start for "Zpět v čase". Null for live, before the first seek, or on an error page.
     */
    private fun progressSeconds(remembered: ResumeSpot): Double? {
        val exo = player ?: return null
        val planned = target ?: return null
        if (pendingWallClockMs != null || message.visibility == View.VISIBLE) return null
        if (exo.playbackState == Player.STATE_IDLE) return null
        return when (planned.kind) {
            PlayKind.LIVE -> null
            PlayKind.VOD -> exo.currentPosition / 1000.0
            PlayKind.TIMESHIFT -> {
                val start = remembered.start ?: return null
                val timeline = exo.currentTimeline
                if (timeline.isEmpty) return null
                val window = timeline.getWindow(exo.currentMediaItemIndex, Timeline.Window())
                if (window.isPlaceholder) return null
                val wallClockMs = when {
                    window.windowStartTimeMs != C.TIME_UNSET -> window.windowStartTimeMs + exo.currentPosition
                    // No program date-time: estimate the window start as now − window length.
                    window.durationMs != C.TIME_UNSET -> System.currentTimeMillis() - window.durationMs + exo.currentPosition
                    else -> return null
                }
                (wallClockMs - start.toEpochMilli()) / 1000.0
            }
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

    /** Channel name of the request (CT catalogue or the gateway's channel list). */
    private fun channelName(): String {
        val id = request.channelId
        if (request is PlayRequest.PspLive) return PspStreams.byId(id)?.name ?: request.title
        Channels.byId(id)?.let { return it.name }
        return runCatching { tvApp.dvb()?.channels()?.firstOrNull { it.id == id }?.name }.getOrNull()
            ?: request.title
    }

    /** Programme title in the info bar; for live it follows the guide and changes with the next programme. */
    private fun startTitles() {
        titleJob?.cancel()
        titleJob = lifecycleScope.launch {
            while (isActive) {
                val now = Instant.now()
                val (title, end) = withContext(Dispatchers.IO) { currentTitle(now) }
                infoTitle.text = title.title
                infoSubtitle.text = title.subtitle
                infoSubtitle.visibility = if (title.subtitle.isBlank()) View.GONE else View.VISIBLE
                if (request !is PlayRequest.Live && request !is PlayRequest.DvbLive) break
                delay(PlayerTitle.nextCheck(now, end).toMillis())
            }
        }
    }

    private fun currentTitle(now: Instant): Pair<InfoTitle, Instant?> {
        val channel = channelName()
        return when (val r = request) {
            is PlayRequest.Live -> {
                val on = runCatching { tvApp.schedules.onAir(now)[r.channelId]?.current }.getOrNull()
                PlayerTitle.live(channel, on?.title, on?.episodeTitle.orEmpty()) to on?.end
            }
            is PlayRequest.DvbLive -> {
                val on = runCatching { tvApp.dvb()?.onAir(now)?.get(r.channelId)?.current }.getOrNull()
                PlayerTitle.live(channel, on?.title, on?.subtitle.orEmpty()) to on?.end
            }
            // No programme data: the stream's name over where it comes from.
            is PlayRequest.PspLive -> InfoTitle(channel, PspMessages.SOURCE) to null
            else -> PlayerTitle.programme(r.title, channel) to null
        }
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
        if (handleZapKey(event)) return true
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

    /**
     * Live only: ▲/▼ with the controller hidden and CH+/CH− always zap (▼ and CH+ = next), number keys pick a
     * channel, OK uses a typed number at once. Start-over, archive and recordings keep their keys.
     */
    private fun handleZapKey(event: KeyEvent): Boolean {
        if (swallowSelectUp && isSelectKey(event.keyCode)) {
            if (event.action == KeyEvent.ACTION_UP) swallowSelectUp = false
            return true
        }
        if (!canZap()) return false
        val down = event.action == KeyEvent.ACTION_DOWN
        ZapChannel.digit(event.keyCode)?.let { digit ->
            if (down && event.repeatCount == 0) digitEntry.type(digit)
            return true
        }
        if (isSelectKey(event.keyCode) && digitEntry.isActive) {
            if (down) {
                swallowSelectUp = true
                digitEntry.commit()
            }
            return true
        }
        val by = when (event.keyCode) {
            KeyEvent.KEYCODE_CHANNEL_UP -> 1
            KeyEvent.KEYCODE_CHANNEL_DOWN -> -1
            KeyEvent.KEYCODE_DPAD_DOWN -> if (playerView.isControllerFullyVisible) return false else 1
            KeyEvent.KEYCODE_DPAD_UP -> if (playerView.isControllerFullyVisible) return false else -1
            else -> return false
        }
        if (down) zap(by)
        return true
    }

    private fun canZap(): Boolean =
        zapChannels.isNotEmpty() && ZapChannel.keyOf(request) != null && errorActions.visibility != View.VISIBLE

    /** Moves the target at once; the stream is resolved after [ChannelNumbers.ZAP_DELAY_MS] without another press. */
    private fun zap(by: Int) {
        val from = zapTarget?.key ?: ZapChannel.keyOf(request)
        val key = ChannelNumbers.step(zapChannels.map { it.key }, from, by) ?: return
        val channel = zapChannels.first { it.key == key }
        digitEntry.cancel()
        zapTarget = channel
        showZapOverlay(channel)
        zapJob?.cancel()
        zapJob = lifecycleScope.launch {
            delay(ChannelNumbers.ZAP_DELAY_MS)
            zapTarget = null
            switchTo(channel)
        }
    }

    private fun pickNumber(number: Int) {
        val key = ChannelNumbers.resolve(zapChannels.map { it.key }, number)
        val channel = zapChannels.firstOrNull { it.key == key }
        if (channel == null) {
            showOverlayText(getString(R.string.channel_missing, number))
            return
        }
        zapJob?.cancel()
        zapTarget = null
        showZapOverlay(channel)
        switchTo(channel)
    }

    /** Plays [channel] live in this player; the info bar follows it. */
    private fun switchTo(channel: ZapChannel) {
        val next = channel.liveRequest() ?: return
        if (next == request || player == null) return
        setRequest(next)
        target = null
        pendingWallClockMs = null
        lastReResolveAt = 0L
        resumeOnStart = Resume.Default
        player?.stop()
        load(forceRefresh = false, resume = Resume.Default)
        startTitles()
    }

    /** Live requests only (no "Pokračovat"); the intent keeps the channel list. */
    private fun setRequest(next: PlayRequest) {
        request = next
        spot = ResumeRules.spot(next)
        intent = PlayRequests.withChannels(PlayRequests.intent(this, next), zapChannels)
    }

    /** "<number>  <channel>" and, once known, the programme airing now, for [ChannelNumbers.OVERLAY_MS]. */
    private fun showZapOverlay(channel: ZapChannel) {
        val number = ChannelNumbers.number(zapChannels.map { it.key }, channel.key)
        val head = if (number != null) "$number  ${channel.name}" else channel.name
        showOverlayText(head)
        overlayJob?.cancel()
        overlayJob = lifecycleScope.launch {
            val programme = withContext(Dispatchers.IO) { onAirTitle(channel) }
            if (programme != null && zapOverlay.visibility == View.VISIBLE && zapOverlay.text == head) {
                zapOverlay.text = "$head\n$programme"
            }
            delay(ChannelNumbers.OVERLAY_MS)
            zapOverlay.visibility = View.GONE
        }
    }

    private fun showOverlayText(text: String) {
        overlayJob?.cancel()
        zapOverlay.text = text
        zapOverlay.visibility = View.VISIBLE
        overlayJob = lifecycleScope.launch {
            delay(ChannelNumbers.OVERLAY_MS)
            zapOverlay.visibility = View.GONE
        }
    }

    /** The programme on air now from the cached guide data (null when unknown). */
    private fun onAirTitle(channel: ZapChannel): String? {
        val now = Instant.now()
        return when (val live = channel.liveRequest()) {
            is PlayRequest.Live -> runCatching { tvApp.schedules.onAir(now)[live.channelId]?.current?.title }.getOrNull()
            is PlayRequest.DvbLive -> runCatching { tvApp.dvb()?.onAir(now)?.get(live.channelId)?.current?.title }.getOrNull()
            // The last known answer only (no check from the player): "Přímý přenos" / "Nevysílá se".
            is PlayRequest.PspLive -> when (tvApp.psp.state(live.channelId, now)) {
                PspState.LIVE -> getString(R.string.psp_live)
                PspState.OFF -> getString(R.string.psp_off)
                PspState.UNKNOWN -> null
            }
            else -> null
        }
    }

    private fun isSelectKey(keyCode: Int): Boolean =
        keyCode == KeyEvent.KEYCODE_DPAD_CENTER || keyCode == KeyEvent.KEYCODE_ENTER ||
            keyCode == KeyEvent.KEYCODE_NUMPAD_ENTER

    private companion object {
        const val TAG = "PlayerActivity"
        const val AD_BREAKS_REFRESH_MS = 60_000L
        const val SKIP_CHECK_MS = 500L
        const val SAVE_INTERVAL_MS = 10_000L
        const val HTTP_TIMEOUT_MS = 20_000
        const val START_BUFFER_MS = 1_000
        const val RE_RESOLVE_COOLDOWN_MS = 5 * 60 * 1000L
        const val LIVE_EDGE_GUARD_MS = 10_000L
    }
}
