package cz.cztvhub.tv.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.relocation.BringIntoViewRequester
import androidx.compose.foundation.relocation.bringIntoViewRequester
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Button
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import cz.cztvhub.core.ChannelPrefs
import cz.cztvhub.core.Channels
import cz.cztvhub.tv.R
import kotlinx.coroutines.launch
import kotlinx.coroutines.yield
import androidx.compose.ui.unit.IntSize

/** A row of the settings list: a channel's key and name. */
private data class ChannelItem(val key: String, val name: String)

/**
 * "Kanály" in Nastavení (spec/apps.md): per group rows `[☑ name] [▲] [▼]` that hide/show and reorder the
 * channels of Živě and the guide on this device, then "Obnovit výchozí pořadí".
 */
@Composable
fun ChannelsSection(viewModel: MainViewModel) {
    val prefs by viewModel.channelPrefs.collectAsStateWithLifecycle()
    val dvb by viewModel.dvb.collectAsStateWithLifecycle()

    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(stringResource(R.string.channels_intro), color = Palette.textDim)

        val antenna = dvb
        if (antenna != null) Text(stringResource(R.string.group_internet), style = MaterialTheme.typography.titleMedium)
        val ctNames = Channels.all.associate { ChannelPrefs.ctKey(it.id) to it.name }
        GroupList(prefs, ChannelOrder.ctKeys, ctNames, viewModel)

        if (antenna != null) {
            Text(stringResource(R.string.group_antenna), style = MaterialTheme.typography.titleMedium)
            when {
                antenna.channels.isNotEmpty() -> {
                    val names = antenna.channels.associate { ChannelPrefs.dvbKey(it.id) to it.name }
                    GroupList(prefs, ChannelOrder.dvbKeys(antenna.channels), names, viewModel)
                }
                antenna.error != null ->
                    Text(stringResource(R.string.channels_antenna_unavailable, antenna.error), color = Palette.nowLine)
                else -> Text(stringResource(R.string.loading), color = Palette.textDim)
            }
        }

        Button(onClick = { viewModel.resetChannelPrefs() }) { Text(stringResource(R.string.channels_reset)) }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun GroupList(prefs: ChannelPrefs, keys: List<String>, names: Map<String, String>, viewModel: MainViewModel) {
    val all = prefs.arrange(keys).all.map { ChannelItem(it, names[it].orEmpty()) }
    // One requester per button and channel, so focus can follow a moved row when its button turns disabled.
    val ups = remember { mutableMapOf<String, FocusRequester>() }
    val downs = remember { mutableMapOf<String, FocusRequester>() }
    // Focus stays on the moved row's button, so the scroll container never scrolls by itself:
    // bring the moved row (with a margin) into view after each move.
    val rows = remember { mutableMapOf<String, BringIntoViewRequester>() }
    val sizes = remember { mutableMapOf<String, IntSize>() }
    val margin = with(LocalDensity.current) { 48.dp.toPx() }
    val scope = rememberCoroutineScope()

    fun move(item: ChannelItem, by: Int) {
        val index = all.indexOfFirst { it.key == item.key }
        val target = index + by
        viewModel.updateChannelPrefs { it.move(item.key, by, keys) }
        // Keyed rows keep focus on the same button while they move; at the edge that button is disabled,
        // so focus goes to the other one of the same row.
        val requester = when {
            target == 0 -> downs[item.key]
            target == all.lastIndex -> ups[item.key]
            else -> null
        }
        scope.launch {
            yield() // let the list recompose first
            if (requester != null) runCatching { requester.requestFocus() }
            withFrameNanos { } // and lay out the row at its new place
            val size = sizes[item.key] ?: IntSize.Zero
            rows[item.key]?.bringIntoView(
                Rect(0f, -margin, size.width.toFloat(), size.height + margin),
            )
        }
    }

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        all.forEachIndexed { index, item ->
            key(item.key) {
                val hidden = prefs.isHidden(item.key)
                val up = ups.getOrPut(item.key) { FocusRequester() }
                val down = downs.getOrPut(item.key) { FocusRequester() }
                val row = rows.getOrPut(item.key) { BringIntoViewRequester() }
                val showLabel = stringResource(R.string.channels_show, item.name)
                val upLabel = stringResource(R.string.channels_up, item.name)
                val downLabel = stringResource(R.string.channels_down, item.name)
                Row(
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier
                        .bringIntoViewRequester(row)
                        .onSizeChanged { sizes[item.key] = it },
                ) {
                    Button(
                        onClick = { viewModel.updateChannelPrefs { it.toggle(item.key) } },
                        modifier = Modifier
                            .width(420.dp)
                            .alpha(if (hidden) 0.5f else 1f)
                            .semantics { contentDescription = showLabel },
                    ) {
                        Text(
                            (if (hidden) "☐  " else "☑  ") + item.name,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    Button(
                        onClick = { move(item, -1) },
                        enabled = index > 0,
                        modifier = Modifier.focusRequester(up).semantics { contentDescription = upLabel },
                    ) { Text("▲") }
                    Button(
                        onClick = { move(item, 1) },
                        enabled = index < all.lastIndex,
                        modifier = Modifier.focusRequester(down).semantics { contentDescription = downLabel },
                    ) { Text("▼") }
                }
            }
        }
    }
}
