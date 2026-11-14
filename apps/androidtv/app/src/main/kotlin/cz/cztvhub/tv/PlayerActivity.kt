package cz.cztvhub.tv

import android.annotation.SuppressLint
import android.app.AlertDialog
import android.graphics.Color
import android.os.Bundle
import android.os.SystemClock
import android.util.Log
import android.util.TypedValue
import android.view.Choreographer
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowManager
import android.widget.Button
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.ImageView
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
import androidx.media3.common.Format
import androidx.media3.common.Timeline
import androidx.media3.common.TrackSelectionOverride
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
import cz.cztvhub.core.CtBarKind
import cz.cztvhub.core.CtBarRules
import cz.cztvhub.core.CtBarWindow
import cz.cztvhub.core.CtException
import cz.cztvhub.core.InfoTitle
import cz.cztvhub.core.PlayerTitle
import cz.cztvhub.core.PlayKind
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.PlayTarget
import cz.cztvhub.core.PspMessages
import cz.cztvhub.core.PspState
import cz.cztvhub.core.PspStreams
import cz.cztvhub.core.ResumeCard
import cz.cztvhub.core.ResumeRules
import cz.cztvhub.core.ResumeSpot
import cz.cztvhub.core.TrackMenu
import cz.cztvhub.core.dvb.AdBreak
import cz.cztvhub.core.dvb.AdBreaks
import cz.cztvhub.core.dvb.BarClock
import cz.cztvhub.core.dvb.BarWindow
import cz.cztvhub.core.dvb.DvbBarWindow
import cz.cztvhub.core.dvb.DvbParser
import cz.cztvhub.core.dvb.DvbTimeline
import cz.cztvhub.core.dvb.TimelineEdge
import cz.cztvhub.core.dvb.TimelineRules
import cz.cztvhub.core.dvb.TimelineShownSeek
import cz.cztvhub.core.dvb.TimelineTrick
import cz.cztvhub.core.dvb.TrickAction
import cz.cztvhub.core.dvb.TrickStop
import cz.cztvhub.core.dvb.WatchItem
import cz.cztvhub.core.dvb.WatchReporter
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.Instant
import java.util.Locale

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

    /** Mute (T34): the button in Media3's controller (null in the timeline player, which has no controller). */
    private var muteButton: ImageButton? = null
    private lateinit var muteIndicator: ImageView

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

    /**
     * Subtitles of the own-bar players (T62): off at the start (no default or forced track of the stream), then the
     * viewer's choice from the long-OK list; [subtitleLanguage] lets it hold across a re-resolve (new track groups).
     */
    private var subtitlesOn = false
    private var subtitleLanguage: String? = null

    /** Ad breaks of a finished gateway recording (spec/ad-breaks.md); empty when unknown or off. */
    private var adBreaks: List<AdBreak> = emptyList()
    private var adBreaksJob: Job? = null
    private var skipTargetSeconds: Double? = null

    /** Where progress is remembered for "Pokračovat"; null for live. */
    private var spot: ResumeSpot? = null

    /**
     * What the archive's "Pokračovat ve sledování" row keeps with the entry of the programme opened (T13, its key
     * [cardKey]); null = keep the stored one.
     */
    private var card: ResumeCard? = null
    private var cardKey: String? = null
    private var progressJob: Job? = null

    /**
     * Pending start-over seek, applied once the live window is known; [pendingFallbackMs] = the buffer offset without
     * program date-time, null = estimate it from the window's length (a seek of the ČT bar, T62).
     */
    private var pendingWallClockMs: Long? = null
    private var pendingFallbackMs: Long? = 0
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

    /**
     * Anténa timeline playback (spec/apps.md "Anténa timeline player"): the window being played, its own bar instead
     * of Media3's controller, the ←/→ press being chosen and the one window switch allowed at a time.
     */
    private var timeline: DvbTimeline? = null
    private lateinit var timelineBar: TimelineOverlay
    private var barJob: Job? = null
    private var barHideAt = 0L

    /** Rewind / fast-forward (T55): ←/→ clicks and holds, ⏪/⏩; the video stays paused while it moves. */
    private val trick = TimelineTrick()

    /** The window and wall clock the movement began at: Back returns there, also from a neighbouring window. */
    private var trickOrigin: Pair<String, Instant>? = null

    /** The chosen seek's target, shown until the player is there (T55: no jump back). */
    private val shownSeek = TimelineShownSeek()

    /** The channel's name for the bar, looked up once per channel (the bar renders every frame while moving). */
    private var barChannel: Pair<String, String>? = null

    /** While rewinding / fast-forwarding the playhead moves every frame, not in key-repeat steps (T55). */
    private val trickFrame = object : Choreographer.FrameCallback {
        override fun doFrame(frameTimeNanos: Long) {
            if (!trick.moving) return
            trickEdge()
            renderBar()
            if (trick.moving) Choreographer.getInstance().postFrameCallback(this)
        }
    }
    private var timelineRefreshJob: Job? = null
    private var switching = false

    /**
     * A press (key code, its down time) already used, e.g. its first down only showed the bar: the rest of that press
     * (repeats, release) does nothing. Matched by down time, so a remote that never sends the key-up does not lose
     * its next press of the same key.
     */
    private var swallowed: Pair<Int, Long>? = null
    private var selectLongPressed = false

    /** OK pressed while the bar was hidden: its release only shows the bar. */
    private var selectShowsOnly = false
    private var releaseJob: Job? = null

    /** Ad breaks per programme id of the window, with when they were fetched (elapsed realtime). */
    private val timelineBreaks = mutableMapOf<String, Pair<Long, List<AdBreak>>>()

    /**
     * Watch reports to the gateway (T53, spec/apps.md "Watch reports (T53)"): a session from [play] until the player
     * is hidden or closed, or the channel changes; only while the gateway's status says `watchLog`.
     */
    private lateinit var watch: WatchReporter

    private val isTimeline: Boolean
        get() = (request as? PlayRequest.DvbRecorded)?.recordingId?.let(TimelineRules::isTimelineId) == true

    /**
     * ČT start-over / "Zpět v čase" and archive (T62, spec/apps.md "ČT player bar"): the same own bar and keys as the
     * Anténa timeline, over one window (the buffer sliding with now, or the VOD); set in [play] from the planned kind.
     */
    private var ctBar: CtBarWindow? = null
    private var ctGuideJob: Job? = null

    /** The own bar instead of Media3's controller: the Anténa timeline, ČT start-over and archive (never live). */
    private val ownBar: Boolean
        get() = isTimeline || CtBarWindow.usesBar(request)

    /** The window the bar and its keys work on; null before [play] (and in the native player). */
    private val barWindow: BarWindow?
        get() = timeline?.let(::DvbBarWindow) ?: ctBar

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        request = PlayRequests.from(intent) ?: return finish()
        spot = ResumeRules.spot(request)
        card = PlayRequests.card(intent)
        cardKey = (request as? PlayRequest.DvbRecorded)?.let { ResumeRules.dvbKey(it.recordingId) } ?: spot?.key
        zapChannels = PlayRequests.channels(intent)
        watch = WatchReporter(
            config = { tvApp.gateway.value },
            profileToken = { tvApp.dvbSettings.profile.value?.token },
            client = tvApp.clientHeader,
            enabled = { tvApp.dvb()?.status()?.watchLog ?: false },
            runner = tvApp.watchRunner,
        )
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        playerView = PlayerView(this).apply {
            useController = !ownBar
            setShowSubtitleButton(true)
            setShowNextButton(false)
            setShowPreviousButton(false)
            controllerShowTimeoutMs = 4000
            keepScreenOn = true
            setControllerVisibilityListener(PlayerView.ControllerVisibilityListener { visibility ->
                infoBox.visibility = visibility
            })
        }
        muteButton = if (ownBar) null else addMuteButton()
        // Muted: a crossed speaker top right, also with the controller hidden.
        muteIndicator = ImageView(this).apply {
            setImageResource(R.drawable.ic_volume_off)
            setBackgroundColor(Color.argb(160, 0, 0, 0))
            setPadding(dp(10), dp(10), dp(10), dp(10))
            contentDescription = getString(R.string.muted_indicator)
            visibility = View.GONE
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
        ).apply { setMargins(0, 0, dp(48), dp(if (ownBar) TIMELINE_SKIP_MARGIN_DP else 120)) }
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
        timelineBar = TimelineOverlay(this).apply { visibility = View.GONE }
        val root = FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK)
            addView(playerView, FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT)
            addView(timelineBar, FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM,
            ))
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
            // Top right: the mute indicator, the number buffer below it (in its old place while not muted).
            addView(LinearLayout(this@PlayerActivity).apply {
                orientation = LinearLayout.VERTICAL
                gravity = Gravity.END
                addView(muteIndicator, LinearLayout.LayoutParams(dp(MUTE_INDICATOR_DP), dp(MUTE_INDICATOR_DP)).apply {
                    gravity = Gravity.END
                    bottomMargin = dp(12)
                })
                addView(digitBox, LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { gravity = Gravity.END })
            }, FrameLayout.LayoutParams(
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
            trackSelectionParameters = withSubtitleChoice(audioPreference.applyTo(trackSelectionParameters))
            // Mute holds for every player until the app closes (zapping, the next programme, a timeline window).
            volume = tvApp.mute.volume
            addListener(listener)
        }
        player = exo
        renderMute()
        playerView.player = exo
        playerView.requestFocus()
        load(forceRefresh = false, resume = resumeOnStart)
        startTitles()
        startAdBreaks()
        startProgressSaving()
        startTimelineRefresh()
    }

    // Also on pause: the guide's programme page recomputes "Pokračovat" in its onResume, which runs before onStop.
    override fun onPause() {
        super.onPause()
        saveProgress()
    }

    override fun onStop() {
        super.onStop()
        // Closed or in the background: the session ends; back in front is a new one.
        if (::watch.isInitialized) watch.end()
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
        timelineRefreshJob?.cancel()
        timelineRefreshJob = null
        ctGuideJob?.cancel()
        ctGuideJob = null
        barJob?.cancel()
        barJob = null
        releaseJob?.cancel()
        releaseJob = null
        trick.reset()
        trickOrigin = null
        Choreographer.getInstance().removeFrameCallback(trickFrame)
        shownSeek.clear()
        swallowed = null
        switching = false
        playerView.player = null
        player?.release()
        player = null
    }

    private fun load(forceRefresh: Boolean, resume: Resume) {
        loadJob?.cancel()
        showMessage(getString(R.string.loading))
        // A timeline window re-opens at the wall clock it was left at (re-resolve, back from the background).
        val timelineAt = if (isTimeline) (resume as? Resume.WallClock)?.let { Instant.ofEpochMilli(it.epochMs) } else null
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
                withContext(Dispatchers.IO) { tvApp.planner.plan(request, forceRefresh, timelineAt) }
            } catch (e: CtException) {
                Log.w(TAG, "cannot play $request: ${e.message}")
                watch.end() // nothing plays (also a failed re-resolve of what played)
                showMessage(e.userMessage)
                if (e.status == "UNSUPPORTED_DRM") {
                    errorActions.visibility = View.VISIBLE
                    openApp.requestFocus()
                }
                return@launch
            }
            play(planned, resume)
        }
    }

    /** Starts [planned]; [resume] applies to the native player only (a timeline target is already mapped). */
    private fun play(planned: PlayTarget, resume: Resume) {
        val exo = player ?: return
        target = planned
        timeline = planned.timeline
        // ČT start-over / archive: the bar's window, decided by what the planner chose (a re-resolve keeps the guide).
        val previousBar = ctBar
        ctBar = if (planned.timeline != null) null else CtBarWindow.of(request, planned.kind, Instant.now())?.let { bar ->
            previousBar?.takeIf { it.kind == bar.kind }?.let { bar.copy(programmes = it.programmes) } ?: bar
        }
        startCtGuide(planned.channelId?.takeIf { ctBar?.kind == CtBarKind.TIMESHIFT })
        // A new window (or a re-resolved one) starts where it was mapped: no old target to show.
        shownSeek.clear()
        val playing = request
        // The same item again (a re-resolved URL) keeps its session; a new timeline window is a new programme.
        watch.begin(WatchItem.of(playing, planned.kind)) { watchTitle(playing) }
        pendingWallClockMs = null
        var startPositionMs: Long? = planned.startPositionMs
        when {
            planned.timeline != null -> Unit
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
        exo.trackSelectionParameters = withSubtitleChoice(audioPreference.applyTo(exo.trackSelectionParameters))
        if (startPositionMs != null) exo.setMediaSource(source, startPositionMs) else exo.setMediaSource(source)
        // Hold playback until the start-over seek is done, so the live edge never flashes by.
        exo.playWhenReady = pendingWallClockMs == null
        // Moving on into a neighbouring window: the movement goes on from where the window opened, paused.
        if (planned.timeline != null && trick.moving && trick.frozen) {
            trick.rebase((startPositionMs ?: 0L) / 1000.0, SystemClock.uptimeMillis())
            exo.playWhenReady = false
        }
        exo.prepare()
        hideMessage()
        if (barWindow != null) showBar()
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
            if (playbackState == Player.STATE_ENDED) timelineEnded()
        }

        override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
            // The ČT buffer slid on during a pause: a playhead now below the lower clamp goes to it (T62).
            if (playWhenReady) resumeInsideBuffer()
            // A paused timeline keeps its bar on screen.
            if (barWindow != null) showBar()
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
                watch.end()
                showMessage(getString(R.string.psp_not_on))
                return
            }
            // The gateway explains tuner/stream problems in Czech (503 "Tuner je obsazený", 502).
            gatewayError(error)?.let {
                watch.end()
                showMessage(it)
                return
            }
            // Re-resolve once per incident (signed URLs expire after ~1 h of playback).
            val now = SystemClock.elapsedRealtime()
            if (lastReResolveAt == 0L || now - lastReResolveAt > RE_RESOLVE_COOLDOWN_MS) {
                lastReResolveAt = now
                load(forceRefresh = true, resume = currentResume())
            } else {
                watch.end()
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
        // The ČT bar keeps every seek inside the buffer's clamps (T62): a minute after its start, 10 s before its end
        // (the stream's end, which lags the real clock; the real clock without date-time).
        val buffer = ctBar?.kind == CtBarKind.TIMESHIFT
        val target = if (buffer) {
            CtBarRules.clampTimeshift(Instant.ofEpochMilli(wallClock), streamEnd(exo) ?: Instant.now()).toEpochMilli()
        } else {
            wallClock
        }
        val position = if (window.windowStartTimeMs != C.TIME_UNSET) {
            target - window.windowStartTimeMs
        } else {
            pendingFallbackMs
                ?: CtBarRules.playerPositionMs(target, null, window.durationMs, System.currentTimeMillis())
                ?: 0
        }
        pendingWallClockMs = null
        val lowest = if (buffer) minOf(CtBarRules.OLDEST_SKIPPED.toMillis(), window.durationMs) else 0
        exo.seekTo(position.coerceIn(lowest, maxOf(lowest, window.durationMs - LIVE_EDGE_GUARD_MS)))
        exo.playWhenReady = true
    }

    private fun currentResume(): Resume {
        val exo = player ?: return resumeOnStart
        pendingWallClockMs?.let { return Resume.WallClock(it) }
        if (timeline != null) return playheadTime()?.let { Resume.WallClock(it.toEpochMilli()) } ?: resumeOnStart
        // The ČT bar while rewinding / fast-forwarding: on at the moving playhead (the movement ends there).
        val bar = ctBar
        if (bar != null && trick.moving) {
            val at = trick.position(SystemClock.uptimeMillis())
            return when (bar.kind) {
                CtBarKind.VOD -> Resume.Position(Math.round(bar.clamp(at, barClock()) * 1000))
                CtBarKind.TIMESHIFT -> Resume.WallClock(bar.toTime(at).toEpochMilli())
            }
        }
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
        if (spot == null && !ownBar) return
        progressJob?.cancel()
        progressJob = lifecycleScope.launch {
            while (isActive) {
                delay(SAVE_INTERVAL_MS)
                if (player?.isPlaying == true) saveProgress()
            }
        }
    }

    private fun saveProgress() {
        if (isTimeline) return saveTimelineProgress()
        val bar = ctBar
        val (remembered, seconds) = if (bar != null && bar.kind == CtBarKind.TIMESHIFT && bar.programmes.isNotEmpty()) {
            // The ČT buffer with its guide (T62): the programme at the playhead with an idec, the 30 s margin
            // before the opened one counting as the opened one (progress 0).
            val exo = player ?: return
            if (!progressReady(exo)) return
            val wallClock = timeshiftWallClockMs(exo) ?: return
            bar.resume(bar.toPosition(Instant.ofEpochMilli(wallClock)), spot) ?: return
        } else {
            val opened = spot ?: return
            opened to (progressSeconds(opened) ?: return)
        }
        tvApp.resume.save(remembered.key, seconds, remembered.lengthSeconds, card?.takeIf { remembered.key == cardKey })
    }

    /** Not before the first seek, on an error page or without a stream. */
    private fun progressReady(exo: ExoPlayer): Boolean =
        target != null && pendingWallClockMs == null && message.visibility != View.VISIBLE &&
            exo.playbackState != Player.STATE_IDLE

    /** Resuming in the ČT buffer below its lower clamp (it slid on during a pause): seek to the clamp. */
    private fun resumeInsideBuffer() {
        val bar = ctBar ?: return
        val exo = player ?: return
        if (trick.moving || pendingWallClockMs != null || !progressReady(exo)) return
        val to = bar.resumeTarget(playerBarPosition(exo), barClock()) ?: return
        seekPlayer(exo, to, SystemClock.uptimeMillis())
    }

    /**
     * Seconds from the programme's start: the position for archive and recordings, the shown frame's wall
     * clock − programme start for "Zpět v čase". Null for live, before the first seek, or on an error page.
     */
    private fun progressSeconds(remembered: ResumeSpot): Double? {
        val exo = player ?: return null
        val planned = target ?: return null
        if (!progressReady(exo)) return null
        return when (planned.kind) {
            PlayKind.LIVE -> null
            PlayKind.VOD -> exo.currentPosition / 1000.0
            PlayKind.TIMESHIFT -> {
                val start = remembered.start ?: return null
                val wallClockMs = timeshiftWallClockMs(exo) ?: return null
                (wallClockMs - start.toEpochMilli()) / 1000.0
            }
        }
    }

    /** The live window the player knows, null before it knows one (empty or placeholder). */
    private fun liveWindow(exo: ExoPlayer): Timeline.Window? {
        val timeline = exo.currentTimeline
        if (timeline.isEmpty) return null
        return timeline.getWindow(exo.currentMediaItemIndex, Timeline.Window()).takeUnless { it.isPlaceholder }
    }

    /**
     * Wall clock of the frame shown in the buffer: program date-time + position, else the estimate now − window
     * length + position; null when unknown.
     */
    private fun timeshiftWallClockMs(exo: ExoPlayer): Long? {
        val window = liveWindow(exo) ?: return null
        return CtBarRules.playerWallClockMs(
            window.windowStartTimeMs.takeIf { it != C.TIME_UNSET },
            window.durationMs.takeIf { it != C.TIME_UNSET },
            exo.currentPosition,
            System.currentTimeMillis(),
        )
    }

    /**
     * Ad breaks for finished gateway recordings only (a still-growing one gets no button in v1): fetched on start
     * and every minute, the position checked twice a second. Gateway failures are silent: no button.
     */
    private fun startAdBreaks() {
        if (isTimeline) return startTimelineAdBreaks()
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

    /**
     * The programme a watch report names (T53), on the report thread: for live what the guide shows on air now, the
     * Chamber's stream name, else the programme title the player shows.
     */
    private fun watchTitle(playing: PlayRequest): String? {
        val now = Instant.now()
        return when (playing) {
            is PlayRequest.Live -> runCatching { tvApp.schedules.onAir(now)[playing.channelId]?.current?.title }.getOrNull()
            is PlayRequest.DvbLive -> runCatching { tvApp.dvb()?.onAir(now)?.get(playing.channelId)?.current?.title }.getOrNull()
            is PlayRequest.PspLive -> PspStreams.byId(playing.channelId)?.name ?: playing.title
            else -> playing.title
        }
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

    // -- Anténa timeline (spec/apps.md "Anténa timeline player") ------------------------------------------

    /** Now and the window's length the player knows (a growing Anténa window, the ČT VOD), for the bar's rules. */
    private fun barClock(): BarClock {
        val exo = player
        val known = exo?.duration?.takeIf { it != C.TIME_UNSET && it > 0 }?.let { it / 1000.0 }
        val streamEnd = if (exo != null && ctBar?.kind == CtBarKind.TIMESHIFT) streamEnd(exo) else null
        return BarClock(Instant.now(), known, streamEnd)
    }

    /**
     * The ČT buffer's end on the wall clock (T62: it lags the real clock by minutes): program date-time + window
     * duration; null without date-time (the estimate puts the end at the real clock).
     */
    private fun streamEnd(exo: ExoPlayer): Instant? {
        val window = liveWindow(exo) ?: return null
        return CtBarRules.streamEndMs(
            window.windowStartTimeMs.takeIf { it != C.TIME_UNSET },
            window.durationMs.takeIf { it != C.TIME_UNSET },
        )?.let(Instant::ofEpochMilli)
    }

    /** The Anténa window's length the bar and the edges use: a growing window also what the player already knows. */
    private fun windowDuration(t: DvbTimeline): Double = DvbBarWindow(t).duration(barClock())

    /**
     * The guide of the channel whose ČT buffer plays (T62): the bar's ticks, title at the playhead, ▲/▼ and
     * "Pokračovat"; read from the schedule cache now and every [CT_GUIDE_REFRESH_MS]. Failures keep what was known.
     */
    private fun startCtGuide(channelId: String?) {
        ctGuideJob?.cancel()
        ctGuideJob = null
        if (channelId == null) return
        ctGuideJob = lifecycleScope.launch {
            while (isActive) {
                val now = Instant.now()
                val guide = withContext(Dispatchers.IO) {
                    val days = CtBarRules.guideDays(now).map { day ->
                        runCatching { tvApp.schedules.day(day)[channelId].orEmpty() }
                            .onFailure { Log.i(TAG, "no guide for the bar: ${it.message}") }
                            .getOrNull()
                    }
                    if (days.all { it == null }) null else days.flatMap { it.orEmpty() }
                }
                val bar = ctBar
                if (guide != null && bar != null && bar.kind == CtBarKind.TIMESHIFT) {
                    // Programmes ahead are kept too: ▼ on the one airing goes to the live edge (clamped).
                    ctBar = bar.copy(programmes = CtBarRules.guideProgrammes(guide, now.minus(CtBarRules.WINDOW), now.plus(CtBarRules.WINDOW)))
                }
                delay(CT_GUIDE_REFRESH_MS)
            }
        }
    }

    /** Wall clock of the shown frame; null before playback or on an error page. */
    private fun playheadTime(): Instant? {
        val t = timeline ?: return null
        val exo = player ?: return null
        if (switching || message.visibility == View.VISIBLE || exo.playbackState == Player.STATE_IDLE) return null
        return TimelineRules.toTime(t.pieces, exo.currentPosition / 1000.0, growing = !t.complete)
    }

    /** "Pokračovat" remembers the programme at the playhead (with an id): wall clock − its start. */
    private fun saveTimelineProgress() {
        val t = timeline ?: return
        val time = playheadTime() ?: return
        val remembered = TimelineRules.resumeSpot(t.programmes, time) ?: return
        val start = remembered.start ?: return
        // The programme the viewer opened keeps its card; one the playhead moved into gets its own titles.
        val opened = card?.takeIf { remembered.key == cardKey }
        val programme = TimelineRules.programmeAt(t.programmes, time)?.let { t.programmes[it] }
        val saved = opened ?: programme?.takeIf { it.title.isNotBlank() }?.let {
            ResumeCard(
                title = it.subtitle.ifBlank { it.title },
                showTitle = it.title.takeIf { _ -> it.subtitle.isNotBlank() },
                source = ResumeRules.source(remembered.key),
                start = it.start,
                channelId = request.channelId.ifBlank { null },
            )
        }
        tvApp.resume.save(remembered.key, TimelineRules.resumeProgress(start, time), remembered.lengthSeconds, saved)
    }

    /** Shows the bar for [BAR_MS]; it stays while paused, while a seek is chosen and across a window switch. */
    private fun showBar() {
        if (barWindow == null) return
        barHideAt = SystemClock.elapsedRealtime() + BAR_MS
        timelineBar.visibility = View.VISIBLE
        renderBar()
        if (barJob?.isActive == true) return
        barJob = lifecycleScope.launch {
            while (isActive) {
                delay(BAR_TICK_MS)
                val paused = player?.playWhenReady == false
                if (!paused && !trick.moving && !switching && SystemClock.elapsedRealtime() >= barHideAt) {
                    timelineBar.visibility = View.GONE
                    break
                }
                renderBar()
            }
        }
    }

    private val barVisible: Boolean get() = timelineBar.visibility == View.VISIBLE

    private fun renderBar() {
        val window = barWindow ?: return
        val exo = player ?: return
        timelineBar.render(window.frame(shownPosition(exo), barClock(), barChannelName()), trick.badge)
    }

    /** The playhead the bar shows: the moving one, else a chosen seek until the player is there. */
    private fun shownPosition(exo: ExoPlayer): Double {
        val now = SystemClock.uptimeMillis()
        if (trick.moving) return trick.position(now)
        return shownSeek.position(playerBarPosition(exo), now, switching)
    }

    /**
     * The player's frame in bar seconds: the position (Anténa window, ČT VOD), or for the ČT buffer its wall clock
     * from the bar's origin (the player's positions slide with the buffer); a pending seek counts as done.
     */
    private fun playerBarPosition(exo: ExoPlayer): Double {
        val bar = ctBar
        if (bar == null || bar.kind == CtBarKind.VOD) return exo.currentPosition / 1000.0
        val wallClock = pendingWallClockMs ?: timeshiftWallClockMs(exo) ?: return bar.upper(barClock())
        return bar.toPosition(Instant.ofEpochMilli(wallClock))
    }

    private fun barChannelName(): String {
        val id = request.channelId
        barChannel?.let { (forId, name) -> if (forId == id) return name }
        val name = channelName()
        // Only a name found in a list is kept; the fallback (the title) is looked up again next time.
        if (name != request.title) barChannel = id to name
        return name
    }

    /**
     * Keys of the timeline player: OK / play-pause = pause/play, long OK or the audio key = audio, ←/→ = ±10 s on a
     * click, held = rewind / fast-forward (T55: clicks then change the speed), ⏪/⏩ = start / change it, ▲/▼ =
     * start of this / the next programme. While it moves OK / play-pause play from the playhead, Back returns to
     * where it began and ▲/▼ stop and jump. With the bar hidden the first press only shows it, except play/pause,
     * Back, the audio keys, ⏪/⏩ and a held ←/→ (they show it and act) and OK on "Přeskočit reklamu". Back is
     * otherwise left to the activity (closes the player).
     */
    private fun handleTimelineKey(event: KeyEvent): Boolean {
        val code = event.keyCode
        if (message.visibility == View.VISIBLE) return false
        val down = event.action == KeyEvent.ACTION_DOWN
        val first = down && event.repeatCount == 0
        swallowed?.let { (key, downTime) ->
            if (key == code && downTime == event.downTime) {
                if (!down) swallowed = null
                return true
            }
        }
        if (trick.moving && first && stopKey(code)) {
            swallowed = code to event.downTime
            return true
        }
        if (code == KeyEvent.KEYCODE_BACK) return false
        // "Přeskočit reklamu" takes OK while it is shown (spec/ad-breaks.md).
        if (isSelectKey(code) && skipAd.visibility == View.VISIBLE) {
            if (!down) skipAd()
            return true
        }
        when (code) {
            KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE, KeyEvent.KEYCODE_MEDIA_PLAY, KeyEvent.KEYCODE_MEDIA_PAUSE -> {
                if (first) {
                    val exo = player ?: return true
                    exo.playWhenReady = when (code) {
                        KeyEvent.KEYCODE_MEDIA_PLAY -> true
                        KeyEvent.KEYCODE_MEDIA_PAUSE -> false
                        else -> !exo.playWhenReady
                    }
                    showBar()
                }
                return true
            }
            KeyEvent.KEYCODE_MEDIA_AUDIO_TRACK -> {
                if (first) showAudioChoice()
                return true
            }
            KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                when {
                    first -> {
                        selectLongPressed = false
                        selectShowsOnly = !barVisible
                    }
                    down -> if (!selectLongPressed &&
                        event.eventTime - event.downTime >= ViewConfiguration.getLongPressTimeout()
                    ) {
                        selectLongPressed = true
                        showAudioChoice()
                    }
                    selectLongPressed -> selectLongPressed = false
                    selectShowsOnly -> showBar()
                    else -> {
                        player?.let { it.playWhenReady = !it.playWhenReady }
                        showBar()
                    }
                }
                return true
            }
        }
        val forward = when (code) {
            KeyEvent.KEYCODE_DPAD_RIGHT, KeyEvent.KEYCODE_MEDIA_FAST_FORWARD -> true
            KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_MEDIA_REWIND -> false
            KeyEvent.KEYCODE_DPAD_UP, KeyEvent.KEYCODE_DPAD_DOWN -> null
            else -> return false
        }
        if (barWindow == null) return true
        val media = code == KeyEvent.KEYCODE_MEDIA_REWIND || code == KeyEvent.KEYCODE_MEDIA_FAST_FORWARD
        // With the bar hidden ▲/▼ only show it; ⏪/⏩ and a held ←/→ show it and act at once, a ←/→ click only shows it.
        val barShown = barVisible
        if (first && !barShown && forward == null) {
            swallowed = code to event.downTime
            showBar()
            return true
        }
        if (forward == null) {
            if (first) jump(up = code == KeyEvent.KEYCODE_DPAD_UP, from = player?.let(::playerBarPosition))
            showBar()
            return true
        }
        val exo = player ?: return true
        val now = event.eventTime
        val action = when {
            media -> {
                if (!first) return true
                swallowed = code to event.downTime // its repeats and key-up do nothing
                trick.media(forward, now, shownPosition(exo))
            }
            first -> trick.down(forward, now, shownPosition(exo), barShown)
            down -> trick.repeat(now)
            else -> trick.up(now)
        }
        trickAction(action)
        if (down && trick.releaseAtMs() != null) watchRelease(code, event.downTime) else releaseJob?.cancel()
        showBar()
        return true
    }

    /** OK, play/pause, Back and ▲/▼ while moving: stop (here, back where it began, or and jump). */
    private fun stopKey(code: Int): Boolean {
        val now = SystemClock.uptimeMillis()
        when {
            isSelectKey(code) || code == KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE || code == KeyEvent.KEYCODE_MEDIA_PLAY ||
                code == KeyEvent.KEYCODE_MEDIA_PAUSE -> stopTrick(TrickStop.PLAY_HERE)
            code == KeyEvent.KEYCODE_BACK -> stopTrick(TrickStop.RETURN)
            code == KeyEvent.KEYCODE_DPAD_UP || code == KeyEvent.KEYCODE_DPAD_DOWN -> {
                if (switching) {
                    stopTrick(TrickStop.PLAY_HERE)
                } else {
                    val from = trick.stop(now)
                    stopped()
                    // Nothing to jump to: it plays from the playhead.
                    if (!jump(up = code == KeyEvent.KEYCODE_DPAD_UP, from = from)) seekTimeline(from)
                    player?.playWhenReady = true
                }
            }
            else -> return false
        }
        showBar()
        return true
    }

    private fun trickAction(action: TrickAction) {
        when (action) {
            TrickAction.None, TrickAction.Change -> Unit
            is TrickAction.Step -> player?.let { seekTimeline(shownPosition(it) + action.seconds) }
            TrickAction.Start -> {
                val exo = player
                if (barWindow == null || exo == null) {
                    trick.reset()
                    return
                }
                // Back may need to re-open the Anténa window it began in; the ČT window is always the same one.
                trickOrigin = timeline?.let { t -> t.id to TimelineRules.toTime(t.pieces, trick.origin, growing = !t.complete) }
                shownSeek.clear()
                // The picture stays until the stop (one exact seek then); ExoPlayer has no cheap preview seek here.
                exo.playWhenReady = false
                Choreographer.getInstance().removeFrameCallback(trickFrame)
                Choreographer.getInstance().postFrameCallback(trickFrame)
            }
        }
    }

    /** A press without a key-up ends [TimelineRules.RELEASE_TIMEOUT_MS] after its last repeat. */
    private fun watchRelease(code: Int, downTime: Long) {
        val at = trick.releaseAtMs() ?: return
        releaseJob?.cancel()
        releaseJob = lifecycleScope.launch {
            delay((at - SystemClock.uptimeMillis()).coerceAtLeast(0))
            if (trick.releaseAtMs() != at) return@launch
            Log.i(TAG, "no key-up for $code: counted as released")
            swallowed = code to downTime // its late key-up, if it comes, does nothing
            trickAction(trick.released())
            showBar()
        }
    }

    /** Stops the movement: one exact seek to the playhead ([TrickStop.PLAY_HERE]) or back to its origin, playing. */
    private fun stopTrick(how: TrickStop) {
        if (!trick.moving) return
        val origin = trickOrigin
        val at = trick.stop(SystemClock.uptimeMillis(), how)
        stopped()
        val exo = player ?: return
        exo.playWhenReady = true
        // A neighbouring window is opening: it plays from where it opens (the movement just ends).
        if (switching) return
        val t = timeline
        if (t != null && how == TrickStop.RETURN && origin != null && origin.first != t.id) {
            shownSeek.clear()
            openWindow(origin.first, origin.second)
            return
        }
        seekTimeline(at)
    }

    private fun stopped() {
        trickOrigin = null
        releaseJob?.cancel()
        releaseJob = null
        Choreographer.getInstance().removeFrameCallback(trickFrame)
    }

    /** Each frame while moving: the window's start / end continue in its neighbour, else it stops there and plays. */
    private fun trickEdge() {
        val window = barWindow ?: return
        val clock = barClock()
        val now = SystemClock.uptimeMillis()
        when (window.movingEdge(trick, now, clock)) {
            TimelineEdge.SEEK -> Unit
            // No neighbour, a growing window's live edge, or a ČT clamp (which slides with now): stop and play.
            TimelineEdge.CLAMP -> {
                val at = window.clamp(trick.stop(now), clock)
                stopped()
                player?.let {
                    seekPlayer(it, at, now)
                    it.playWhenReady = true
                }
            }
            TimelineEdge.PREVIOUS -> timeline?.let { t ->
                t.previous?.let {
                    trick.freeze(0.0)
                    openWindow(it, TimelineRules.toTime(t.pieces, 0.0))
                }
            }
            TimelineEdge.NEXT -> timeline?.let { t ->
                val duration = windowDuration(t)
                t.next?.let {
                    trick.freeze(duration)
                    openWindow(it, TimelineRules.toTime(t.pieces, duration))
                }
            }
        }
    }

    /**
     * ▲/▼: the start of this programme (or the previous one) / of the next one through the map, a target outside
     * the window by the edge rule; nothing there = nothing happens (false).
     */
    private fun jump(up: Boolean, from: Double?): Boolean {
        val window = barWindow ?: return false
        if (from == null || switching || player?.playbackState == Player.STATE_IDLE) return false
        val target = window.jumpTarget(from, up, barClock()) ?: return false
        seekTimeline(target)
        return true
    }

    /**
     * A chosen seek (exact): inside the window, clamped to it, or on into the previous / next window at its wall
     * clock. The bar shows the target until the player is there.
     */
    private fun seekTimeline(target: Double) {
        val window = barWindow ?: return
        val exo = player ?: return
        val clock = barClock()
        val now = SystemClock.uptimeMillis()
        when (window.edge(target, clock)) {
            TimelineEdge.SEEK -> seekPlayer(exo, target, now)
            TimelineEdge.CLAMP -> seekPlayer(exo, window.clamp(target, clock), now)
            TimelineEdge.PREVIOUS -> timeline?.let { t ->
                t.previous?.let {
                    shownSeek.seek(target, now)
                    openWindow(it, TimelineRules.targetTime(t.pieces, target))
                }
            }
            TimelineEdge.NEXT -> timeline?.let { t ->
                t.next?.let {
                    shownSeek.seek(target, now)
                    openWindow(it, TimelineRules.targetTime(t.pieces, target))
                }
            }
        }
    }

    /** Seeks the player to bar seconds; in the ČT buffer through its wall clock (the window slides). */
    private fun seekPlayer(exo: ExoPlayer, seconds: Double, now: Long) {
        shownSeek.seek(seconds, now)
        val bar = ctBar
        if (bar == null || bar.kind == CtBarKind.VOD) {
            exo.seekTo(Math.round(seconds * 1000))
            return
        }
        val wallClock = bar.toTime(seconds).toEpochMilli()
        val window = liveWindow(exo)
        val position = window?.let {
            CtBarRules.playerPositionMs(
                wallClock,
                it.windowStartTimeMs.takeIf { start -> start != C.TIME_UNSET },
                it.durationMs.takeIf { d -> d != C.TIME_UNSET },
                System.currentTimeMillis(),
            )
        }
        if (window == null || position == null) {
            // The window is not known yet: the seek waits for it (estimated from its length without date-time).
            pendingWallClockMs = wallClock
            pendingFallbackMs = null
            applyPendingSeek()
            return
        }
        exo.seekTo(position.coerceIn(0, maxOf(0, window.durationMs - LIVE_EDGE_GUARD_MS)))
    }

    /** Playback reached the end of a complete window: on into the next one, if any (else it stays). */
    private fun timelineEnded() {
        val t = timeline ?: return
        val duration = windowDuration(t)
        if (TimelineRules.edge(duration, duration, t.complete, t.previous != null, t.next != null) == TimelineEdge.NEXT) {
            t.next?.let { openWindow(it, TimelineRules.toTime(t.pieces, duration)) }
        }
    }

    /** Opens the neighbouring window [id] with its own JSON, continuing at [at]; one switch at a time. */
    private fun openWindow(id: String, at: Instant) {
        if (switching) return
        val current = request as? PlayRequest.DvbRecorded ?: return
        val next = current.copy(recordingId = id, fromStart = false, lengthSeconds = null, resumeSeconds = null)
        switching = true
        showBar()
        loadJob?.cancel()
        loadJob = lifecycleScope.launch {
            try {
                // JSON and playlist both first: a failure keeps this window playing, its reason shown in the bar.
                val planned = try {
                    withContext(Dispatchers.IO) {
                        tvApp.planner.plan(next, timelineAt = at).also { planned ->
                            val window = planned.timeline ?: throw CtException("$id is no timeline")
                            tvApp.dvb()?.client?.checkPlaylist(window)
                        }
                    }
                } catch (e: CtException) {
                    Log.w(TAG, "cannot open timeline $id: ${e.message}")
                    shownSeek.clear() // this window keeps playing: the bar shows where it is
                    // Moving into it: it stops at this window's edge and plays.
                    if (trick.moving) {
                        val at = trick.stop(SystemClock.uptimeMillis())
                        stopped()
                        player?.let {
                            val end = timeline?.let(::windowDuration) ?: at
                            seekPlayer(it, at.coerceIn(0.0, end), SystemClock.uptimeMillis())
                            it.playWhenReady = true
                        }
                    }
                    timelineBar.notice(e.userMessage)
                    showBar()
                    return@launch
                }
                saveTimelineProgress()
                request = next
                spot = ResumeRules.spot(next)
                intent = PlayRequests.intent(this@PlayerActivity, next)
                play(planned, Resume.Default)
            } finally {
                switching = false
            }
        }
    }

    /** A growing window re-fetches its JSON every minute (new pieces, programmes, completion). */
    private fun startTimelineRefresh() {
        timelineRefreshJob?.cancel()
        if (!isTimeline) return
        timelineRefreshJob = lifecycleScope.launch {
            while (isActive) {
                delay(TimelineRules.REFRESH.toMillis())
                val t = timeline ?: continue
                if (t.complete) continue
                val client = tvApp.dvb()?.client ?: continue
                val fresh = try {
                    withContext(Dispatchers.IO) { client.timeline(t.id) }
                } catch (e: CtException) {
                    Log.i(TAG, "timeline refresh failed: ${e.message}")
                    continue
                }
                if (timeline?.id == fresh.id && fresh.playlist == t.playlist) timeline = fresh
            }
        }
    }

    /** Breaks of the programme at the playhead (id, `breaks: true`, ended), as positions of this window. */
    private fun startTimelineAdBreaks() {
        adBreaksJob?.cancel()
        adBreaksJob = lifecycleScope.launch {
            while (isActive) {
                updateSkipAd(timelineSkipTarget())
                delay(SKIP_CHECK_MS)
            }
        }
    }

    private suspend fun timelineSkipTarget(): Double? {
        val t = timeline ?: return null
        val exo = player ?: return null
        if (trick.moving) return null
        val time = playheadTime() ?: return null
        val programme = TimelineRules.breaksProgramme(t.programmes, time, Instant.now()) ?: return null
        val id = programme.id ?: return null
        val breaks = TimelineRules.breaksInWindow(t.pieces, programme.start, programmeBreaks(id))
        return AdBreaks.skipTarget(breaks, exo.currentPosition / 1000.0)
    }

    /** Fetched once a minute per programme; failures are silent (no button). */
    private suspend fun programmeBreaks(id: String): List<AdBreak> {
        val now = SystemClock.elapsedRealtime()
        timelineBreaks[id]?.let { (at, breaks) -> if (now - at < AD_BREAKS_REFRESH_MS) return breaks }
        val client = tvApp.dvb()?.client
        val breaks = if (client == null) {
            emptyList()
        } else {
            try {
                withContext(Dispatchers.IO) { client.breaks(id) }
            } catch (e: CtException) {
                Log.i(TAG, "no ad breaks: ${e.message}")
                timelineBreaks[id]?.second.orEmpty()
            } catch (e: RuntimeException) {
                Log.i(TAG, "no ad breaks: $e")
                timelineBreaks[id]?.second.orEmpty()
            }
        }
        timelineBreaks[id] = now to breaks
        return breaks
    }

    /**
     * Long OK: the audio renditions of the stream (Czech stays the default until the viewer picks another) and, when
     * the stream has any, the subtitles ("Titulky: Vypnuto" + each by name; T62, also in the Anténa timeline), with
     * the mute item.
     */
    private fun showAudioChoice() {
        val exo = player ?: return
        val audio = mutableListOf<String>()
        val audioOverrides = mutableListOf<TrackSelectionOverride>()
        var audioSelected: Int? = null
        val subtitles = mutableListOf<String?>()
        val subtitleOverrides = mutableListOf<TrackSelectionOverride>()
        var subtitleSelected: Int? = null
        for (group in exo.currentTracks.groups) {
            for (i in 0 until group.length) {
                if (!group.isTrackSupported(i)) continue
                when (group.type) {
                    C.TRACK_TYPE_AUDIO -> {
                        if (group.isTrackSelected(i)) audioSelected = audio.size
                        audio += trackLabel(group.getTrackFormat(i), audio.size + 1)
                        audioOverrides += TrackSelectionOverride(group.mediaTrackGroup, i)
                    }
                    C.TRACK_TYPE_TEXT -> {
                        if (group.isTrackSelected(i)) subtitleSelected = subtitles.size
                        val format = group.getTrackFormat(i)
                        subtitles += TrackMenu.trackName(format.label, format.language)
                        subtitleOverrides += TrackSelectionOverride(group.mediaTrackGroup, i)
                    }
                }
            }
        }
        val items = TrackMenu.items(audio, audioSelected, subtitles, subtitleSelected)
        // The own-bar players have no controller: their mute button lives here (T34), also with a single track.
        val builder = AlertDialog.Builder(this)
            .setTitle(TrackMenu.title(hasSubtitles = subtitles.isNotEmpty()))
            .setNeutralButton(muteLabel()) { _, _ -> toggleMute() }
        if (items.isEmpty()) {
            builder.setMessage(R.string.audio_single).show()
            return
        }
        val pick = { item: TrackMenu.Item ->
            val params = exo.trackSelectionParameters.buildUpon()
            exo.trackSelectionParameters = when (item.kind) {
                TrackMenu.Kind.AUDIO -> params.setOverrideForType(audioOverrides[item.index])
                TrackMenu.Kind.SUBTITLES_OFF -> {
                    subtitlesOn = false
                    subtitleLanguage = null
                    params.clearOverridesOfType(C.TRACK_TYPE_TEXT)
                        .setPreferredTextLanguage(null)
                        .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true)
                }
                // The language too: a re-resolved stream has new track groups, where the override no longer applies.
                TrackMenu.Kind.SUBTITLE -> subtitleOverrides[item.index].let { override ->
                    subtitlesOn = true
                    subtitleLanguage = override.mediaTrackGroup.getFormat(override.trackIndices[0]).language
                    params.setTrackTypeDisabled(C.TRACK_TYPE_TEXT, false)
                        .setPreferredTextLanguage(subtitleLanguage)
                        .setOverrideForType(override)
                }
            }.build()
        }
        if (subtitles.isEmpty()) {
            // Audio only: the radio list as before.
            builder.setSingleChoiceItems(items.map { it.label }.toTypedArray(), items.indexOfFirst { it.selected }) { dialog, which ->
                pick(items[which])
                dialog.dismiss()
            }.show()
            return
        }
        // Audio and subtitles: one list, what plays now marked in each part.
        builder.setItems(items.map { (if (it.selected) "✓ " else "    ") + it.label }.toTypedArray()) { _, which ->
            pick(items[which])
        }.show()
    }

    /**
     * „Vypnout zvuk" / „Zapnout zvuk" in Media3's controller, before its subtitle and settings buttons; null when
     * the controller layout has no basic-controls row (another Media3 layout).
     */
    private fun addMuteButton(): ImageButton? {
        val row = playerView.findViewById<LinearLayout>(androidx.media3.ui.R.id.exo_basic_controls) ?: return null
        val width = resources.getDimensionPixelSize(androidx.media3.ui.R.dimen.exo_small_icon_width)
        val height = resources.getDimensionPixelSize(androidx.media3.ui.R.dimen.exo_small_icon_height)
        val margin = resources.getDimensionPixelSize(androidx.media3.ui.R.dimen.exo_small_icon_horizontal_margin)
        val padH = resources.getDimensionPixelSize(androidx.media3.ui.R.dimen.exo_small_icon_padding_horizontal)
        val padV = resources.getDimensionPixelSize(androidx.media3.ui.R.dimen.exo_small_icon_padding_vertical)
        val button = ImageButton(this).apply {
            val ripple = TypedValue()
            theme.resolveAttribute(android.R.attr.selectableItemBackground, ripple, true)
            setBackgroundResource(ripple.resourceId)
            scaleType = ImageView.ScaleType.FIT_CENTER
            setPadding(padH, padV, padH, padV)
            setOnClickListener { toggleMute() }
        }
        row.addView(button, 0, LinearLayout.LayoutParams(width, height).apply { setMargins(margin, 0, margin, 0) })
        return button
    }

    private fun muteLabel(): String = getString(if (tvApp.mute.muted) R.string.mute_off else R.string.mute_on)

    private fun toggleMute() {
        tvApp.mute.toggle()
        player?.volume = tvApp.mute.volume
        renderMute()
    }

    /** The button shows the speaker as it is now (crossed when muted) and names its action; the corner icon. */
    private fun renderMute() {
        val muted = tvApp.mute.muted
        muteButton?.apply {
            setImageResource(if (muted) R.drawable.ic_volume_off else R.drawable.ic_volume_up)
            contentDescription = muteLabel()
        }
        muteIndicator.visibility = if (muted) View.VISIBLE else View.GONE
    }

    /** An audio track's name: its label, else its language in Czech, else "Stopa n". */
    private fun trackLabel(format: Format, number: Int): String {
        format.label?.takeIf { it.isNotBlank() }?.let { return it }
        val language = format.language?.takeIf { it.isNotBlank() && it != C.LANGUAGE_UNDETERMINED }
            ?.let { Locale.forLanguageTag(it).getDisplayLanguage(CZECH) }
            ?.takeIf { it.isNotBlank() }
        return language?.replaceFirstChar { it.titlecase(CZECH) } ?: getString(R.string.audio_track, number)
    }

    /**
     * The own-bar players' subtitles in [params] (T62): off unless the viewer chose some (then by language, so the
     * choice holds in a re-resolved stream); the native players keep Media3's own selection.
     */
    private fun withSubtitleChoice(params: TrackSelectionParameters): TrackSelectionParameters {
        if (!ownBar) return params
        val builder = params.buildUpon().setTrackTypeDisabled(C.TRACK_TYPE_TEXT, !subtitlesOn)
        // A new stream: an override of the old one's track groups no longer applies, the language does.
        builder.clearOverridesOfType(C.TRACK_TYPE_TEXT)
        builder.setPreferredTextLanguage(if (subtitlesOn) subtitleLanguage else null)
        return builder.build()
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun showMessage(text: String) {
        message.text = text
        message.visibility = View.VISIBLE
        updateSkipAd(null)
        // An error page ends a rewind / fast-forward (its keys go to the page now).
        if (trick.moving) {
            trick.reset()
            stopped()
        }
    }

    private fun hideMessage() {
        message.visibility = View.GONE
        errorActions.visibility = View.GONE
    }

    // Lint flags any dispatchKeyEvent override in a ComponentActivity (androidx.core restriction); false positive.
    @SuppressLint("RestrictedApi")
    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        // The remote's MUTE key, when the TV hands it to the app: mutes this app's player, not the TV.
        if (event.keyCode == KeyEvent.KEYCODE_VOLUME_MUTE || event.keyCode == KeyEvent.KEYCODE_MUTE) {
            if (event.action == KeyEvent.ACTION_DOWN && event.repeatCount == 0) toggleMute()
            return true
        }
        if (ownBar && handleTimelineKey(event)) return true
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
        // The old channel's session ends now, also when the new one does not play (e.g. the Chamber off air).
        watch.end()
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
        const val BAR_MS = 5_000L
        const val BAR_TICK_MS = 250L
        const val TIMELINE_SKIP_MARGIN_DP = 300
        const val MUTE_INDICATOR_DP = 56
        const val CT_GUIDE_REFRESH_MS = 60_000L
        val CZECH: Locale = Locale.forLanguageTag("cs")
    }
}
