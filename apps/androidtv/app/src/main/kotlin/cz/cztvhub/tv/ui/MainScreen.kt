package cz.cztvhub.tv.ui

import android.view.KeyEvent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import androidx.tv.material3.LocalContentColor
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Tab
import androidx.tv.material3.TabRow
import androidx.tv.material3.Text
import cz.cztvhub.core.ChannelNumbers
import cz.cztvhub.core.Clock
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.tv.DigitEntry
import cz.cztvhub.tv.R
import cz.cztvhub.tv.ZapChannel
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.time.Instant

@Composable
fun MainScreen(viewModel: MainViewModel, archive: ArchiveViewModel, onPlay: PlayWithCard) {
    val play: (PlayRequest) -> Unit = remember(onPlay) { { onPlay(it, null) } }
    var tab by rememberSaveable { mutableIntStateOf(0) }
    // An open Nastavení section is full screen: no tab row (spec/apps.md).
    var settingsSection by rememberSaveable { mutableStateOf<SettingsSection?>(null) }
    val tabs = listOf(
        stringResource(R.string.tab_live),
        stringResource(R.string.tab_guide),
        stringResource(R.string.tab_archive),
        stringResource(R.string.tab_settings),
    )

    val numbers = rememberNumberKeys(viewModel, play)
    val notice by viewModel.notice.collectAsStateWithLifecycle()

    CompositionLocalProvider(LocalContentColor provides Palette.text) {
    // Number keys zap on Živě and the guide only (the archive's search field takes digits).
    Box(Modifier.fillMaxSize().onPreviewKeyEvent { tab <= 1 && numbers.onKey(it.nativeKeyEvent) }) {
    Column(
        Modifier
            .fillMaxSize()
            .background(Palette.background)
            .padding(horizontal = 40.dp, vertical = 20.dp),
    ) {
        // The tab row, or an open section's title in its place; the clock stays top right on both (spec/apps.md
        // "Clock"), at the height the tab row had.
        val section = settingsSection.takeIf { tab == SETTINGS_TAB }
        val density = LocalDensity.current
        var tabRowHeight by remember { mutableIntStateOf(0) }
        Row(
            Modifier
                .fillMaxWidth()
                .heightIn(min = with(density) { tabRowHeight.toDp() })
                .onSizeChanged { if (section == null) tabRowHeight = it.height },
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (section != null) {
                Text(stringResource(section.title), style = MaterialTheme.typography.headlineMedium)
            } else TabRow(selectedTabIndex = tab) {
                tabs.forEachIndexed { index, title ->
                    val open = {
                        // Every open of the guide shows now (spec/apps.md), whatever day it showed before.
                        if (index == 1 && tab != 1) viewModel.openGuide()
                        tab = index
                    }
                    Tab(selected = tab == index, onFocus = open, onClick = open) {
                        Text(
                            title,
                            style = MaterialTheme.typography.titleMedium,
                            modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp),
                        )
                    }
                }
            }
            Spacer(Modifier.weight(1f))
            TopClock()
        }
        Spacer(Modifier.height(12.dp))
        when (tab) {
            0 -> LiveScreen(viewModel, play)
            1 -> GuideScreen(viewModel, play, onPlay)
            2 -> ArchiveScreen(archive, viewModel, onPlay)
            else -> SettingsScreen(viewModel, settingsSection) { settingsSection = it }
        }
    }
    // The number buffer, else a short notice (e.g. "Poslanecká sněmovna teď nevysílá.").
    val overlay = numbers.text.ifEmpty { notice.orEmpty() }
    if (overlay.isNotEmpty()) {
        Text(
            overlay,
            style = MaterialTheme.typography.displaySmall,
            color = Palette.text,
            modifier = Modifier
                .align(Alignment.TopEnd)
                .padding(top = 80.dp, end = 48.dp)
                .background(Palette.surface, RoundedCornerShape(12.dp))
                .padding(horizontal = 24.dp, vertical = 8.dp),
        )
    }
    }
    }
}

private const val SETTINGS_TAB = 3

/** Number keys on Živě and the guide start that channel live (spec/apps.md "Channel numbers and zapping"). */
private class NumberKeys(private val entry: DigitEntry) {
    /** The buffer (`2_`) or "Kanál 9 není"; empty = nothing shown. */
    var text by mutableStateOf("")
    private var swallowSelectUp = false

    fun onKey(event: KeyEvent): Boolean {
        val select = event.keyCode == KeyEvent.KEYCODE_DPAD_CENTER || event.keyCode == KeyEvent.KEYCODE_ENTER ||
            event.keyCode == KeyEvent.KEYCODE_NUMPAD_ENTER
        val down = event.action == KeyEvent.ACTION_DOWN
        if (select && swallowSelectUp) {
            if (!down) swallowSelectUp = false
            return true
        }
        ZapChannel.digit(event.keyCode)?.let { digit ->
            if (down && event.repeatCount == 0) entry.type(digit)
            return true
        }
        if (select && entry.isActive && down) {
            swallowSelectUp = true
            entry.commit()
            return true
        }
        return false
    }
}

@Composable
private fun rememberNumberKeys(viewModel: MainViewModel, onPlay: (PlayRequest) -> Unit): NumberKeys {
    val scope = rememberCoroutineScope()
    val play by rememberUpdatedState(onPlay)
    // Unformatted "Kanál %1$d není", formatted when a number misses.
    val missing by rememberUpdatedState(stringResource(R.string.channel_missing))
    val notOn by rememberUpdatedState(stringResource(R.string.psp_not_on))
    return remember {
        var keys: NumberKeys? = null
        var messageJob: Job? = null
        // Read when a key comes, so the 30 s refreshes do not recompose this screen.
        fun visible() = ChannelOrder.visible(viewModel.channelPrefs.value, viewModel.dvb.value?.channels.orEmpty())
        val entry = DigitEntry(
            scope,
            count = { visible().size },
            onBuffer = { buffer ->
                messageJob?.cancel()
                keys?.text = if (buffer.isEmpty()) "" else "${buffer}_"
            },
            onNumber = { number ->
                val channels = visible()
                val key = ChannelNumbers.resolve(channels.map { it.key }, number)
                val listed = channels.firstOrNull { it.key == key }
                val request = listed?.let { ZapChannel(it.key, it.name).liveRequest() }
                val stream = listed?.psp
                if (stream != null) {
                    selectPsp(viewModel, stream, play) { viewModel.showNotice(notOn) }
                } else if (request != null) {
                    play(request)
                } else {
                    keys?.text = missing.format(number)
                    messageJob = scope.launch {
                        delay(ChannelNumbers.OVERLAY_MS)
                        keys?.text = ""
                    }
                }
            },
        )
        NumberKeys(entry).also { keys = it }
    }
}

/**
 * Prague `H:MM`, changing on the minute: each update is scheduled at the next minute boundary, and only while the
 * app is visible (an update on return). Own composable so the tick recomposes only this text.
 */
@Composable
private fun TopClock() {
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val now by produceState(Instant.now(), lifecycle) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                value = Instant.now()
                delay(Clock.untilNextMinute(value))
            }
        }
    }
    // Same size and colour as the inactive tab labels (tv-material's default: content colour at 40 %).
    Text(Clock.text(now), style = MaterialTheme.typography.titleMedium, color = Palette.text.copy(alpha = 0.4f))
}

@Composable
fun CenteredMessage(text: String, modifier: Modifier = Modifier, action: (@Composable () -> Unit)? = null) {
    Column(
        modifier.fillMaxSize(),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(text, style = MaterialTheme.typography.headlineSmall, color = Palette.textDim)
        if (action != null) {
            Spacer(Modifier.padding(top = 16.dp))
            action()
        }
    }
}
