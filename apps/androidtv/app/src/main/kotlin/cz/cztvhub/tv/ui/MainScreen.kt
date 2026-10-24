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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.LocalContentColor
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Tab
import androidx.tv.material3.TabRow
import androidx.tv.material3.Text
import cz.cztvhub.core.ChannelNumbers
import cz.cztvhub.core.Days
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.tv.DigitEntry
import cz.cztvhub.tv.R
import cz.cztvhub.tv.ZapChannel
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@Composable
fun MainScreen(viewModel: MainViewModel, onPlay: (PlayRequest) -> Unit) {
    var tab by rememberSaveable { mutableIntStateOf(0) }
    // An open Nastavení section is full screen: no tab row (spec/apps.md).
    var settingsSection by rememberSaveable { mutableStateOf<SettingsSection?>(null) }
    val tabs = listOf(
        stringResource(R.string.tab_live),
        stringResource(R.string.tab_guide),
        stringResource(R.string.tab_settings),
    )

    val numbers = rememberNumberKeys(viewModel, onPlay)

    CompositionLocalProvider(LocalContentColor provides Palette.text) {
    Box(Modifier.fillMaxSize().onPreviewKeyEvent { tab != 2 && numbers.onKey(it.nativeKeyEvent) }) {
    Column(
        Modifier
            .fillMaxSize()
            .background(Palette.background)
            .padding(horizontal = 40.dp, vertical = 20.dp),
    ) {
        if (tab != 2 || settingsSection == null) Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            TabRow(selectedTabIndex = tab) {
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
            Clock(viewModel)
        }
        Spacer(Modifier.height(12.dp))
        when (tab) {
            0 -> LiveScreen(viewModel, onPlay)
            1 -> GuideScreen(viewModel, onPlay)
            else -> SettingsScreen(viewModel, settingsSection) { settingsSection = it }
        }
    }
    if (numbers.text.isNotEmpty()) {
        Text(
            numbers.text,
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
                val request = channels.firstOrNull { it.key == key }?.let { ZapChannel(it.key, it.name).liveRequest() }
                if (request != null) {
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

/** Own composable so the 30 s tick recomposes only the clock text. */
@Composable
private fun Clock(viewModel: MainViewModel) {
    val now by viewModel.now.collectAsStateWithLifecycle()
    Text(Days.time(now), style = MaterialTheme.typography.titleLarge, color = Palette.textDim)
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
