package cz.cztvhub.tv.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.ClickableSurfaceDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Surface
import androidx.tv.material3.Text
import cz.cztvhub.core.ChannelPrefs
import cz.cztvhub.core.Days
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.Timed
import cz.cztvhub.tv.R
import java.time.Duration
import java.time.Instant

/** Title and time span of what a card shows. */
private data class CardItem(val title: String, val span: Timed)

/**
 * "Živě": one list mixing CT internet and Anténa channels in the viewer's order ("Kanály", spec/apps.md), each
 * card with its source icon and the programme on air now.
 */
@Composable
fun LiveScreen(viewModel: MainViewModel, onPlay: (PlayRequest) -> Unit) {
    val onAir by viewModel.onAir.collectAsStateWithLifecycle()
    val now by viewModel.now.collectAsStateWithLifecycle()
    val dvb by viewModel.dvb.collectAsStateWithLifecycle()
    val current = (onAir as? Load.Ready)?.value.orEmpty()
    val loading = onAir is Load.Loading
    val firstCard = remember { FocusRequester() }
    val antenna = dvb
    val prefs by viewModel.channelPrefs.collectAsStateWithLifecycle()
    val channels = ChannelOrder.visible(prefs, antenna?.channels.orEmpty())
    // Anténa switched on but the gateway has not answered yet: not "everything hidden".
    val antennaPending = antenna != null && prefs.isSourceOn(ChannelPrefs.SOURCE_DVB) &&
        antenna.channels.isEmpty() && antenna.error == null

    if (channels.isEmpty() && !antennaPending) {
        CenteredMessage(stringResource(R.string.channels_all_hidden))
        return
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(top = 16.dp, bottom = 16.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp),
    ) {
        if (antenna?.error != null && prefs.isSourceOn(ChannelPrefs.SOURCE_DVB)) {
            Text(stringResource(R.string.antenna_unavailable, antenna.error), color = Palette.nowLine)
        }
        channels.chunked(3).forEachIndexed { rowIndex, row ->
            CardRow {
                row.forEachIndexed { index, channel ->
                    val number = rowIndex * 3 + index + 1
                    // The very first card takes the initial focus.
                    val base = Modifier.weight(1f).fillMaxHeight()
                    val modifier = if (rowIndex == 0 && index == 0) base.focusRequester(firstCard) else base
                    val ct = channel.ct
                    val dvbChannel = channel.dvb
                    if (ct != null) {
                        val info = current[ct.id]
                        ChannelCard(
                            name = ct.name,
                            number = number,
                            antenna = false,
                            current = info?.current?.let { CardItem(it.title, it) },
                            next = info?.next?.let { CardItem(it.title, it) },
                            emptyText = stringResource(if (loading) R.string.loading else R.string.no_programme_info),
                            now = now,
                            modifier = modifier,
                            onClick = { onPlay(PlayRequest.Live(ct.id, ct.name)) },
                        )
                    } else if (dvbChannel != null) {
                        val info = antenna?.onAir?.get(dvbChannel.id)
                        ChannelCard(
                            name = dvbChannel.name,
                            number = number,
                            antenna = true,
                            current = info?.current?.let { CardItem(it.title, it) },
                            next = info?.next?.let { CardItem(it.title, it) },
                            emptyText = stringResource(R.string.no_programme_info),
                            now = now,
                            modifier = modifier,
                            onClick = { onPlay(PlayRequest.DvbLive(dvbChannel.id, dvbChannel.name)) },
                        )
                    }
                }
                repeat(3 - row.size) { Spacer(Modifier.weight(1f)) }
            }
        }
    }
    // Focus the first card once at app start; later tab switches keep focus on the tab row.
    LaunchedEffect(Unit) {
        if (!viewModel.initialFocusDone) {
            viewModel.initialFocusDone = true
            runCatching { firstCard.requestFocus() }
        }
    }
}

@Composable
private fun CardRow(content: @Composable RowScope.() -> Unit) {
    Row(Modifier.fillMaxWidth().height(200.dp), horizontalArrangement = Arrangement.spacedBy(20.dp), content = content)
}

@Composable
private fun ChannelCard(
    name: String,
    number: Int,
    antenna: Boolean,
    current: CardItem?,
    next: CardItem?,
    emptyText: String,
    now: Instant,
    modifier: Modifier,
    onClick: () -> Unit,
) {
    Surface(
        onClick = onClick,
        modifier = modifier,
        shape = ClickableSurfaceDefaults.shape(RoundedCornerShape(16.dp)),
        scale = ClickableSurfaceDefaults.scale(focusedScale = 1.04f),
        colors = ClickableSurfaceDefaults.colors(
            containerColor = Palette.surface,
            contentColor = Palette.text,
            focusedContainerColor = Palette.focused,
            focusedContentColor = Palette.onFocused,
        ),
    ) {
        Column(Modifier.fillMaxSize().padding(20.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                ChannelNumber(number, style = MaterialTheme.typography.titleLarge)
                SourceIcon(antenna, size = 26.dp)
                Text(name, style = MaterialTheme.typography.headlineMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            Spacer(Modifier.height(8.dp))
            if (current == null) {
                Text(emptyText, style = MaterialTheme.typography.bodyLarge)
                Spacer(Modifier.weight(1f))
                next?.let { NextUp(it) }
                return@Column
            }
            Text(current.title, style = MaterialTheme.typography.titleLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Spacer(Modifier.weight(1f))
            Text(Days.timeRange(current.span.start, current.span.end), style = MaterialTheme.typography.bodyLarge)
            Spacer(Modifier.height(6.dp))
            ProgressBar(progress(current.span, now))
            next?.let {
                Spacer(Modifier.height(6.dp))
                NextUp(it)
            }
        }
    }
}

@Composable
private fun NextUp(next: CardItem) {
    Text(
        stringResource(R.string.next_up, "${Days.time(next.span.start)} ${next.title}"),
        style = MaterialTheme.typography.bodyMedium,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
    )
}

@Composable
private fun ProgressBar(fraction: Float) {
    Box(
        Modifier
            .fillMaxWidth()
            .height(6.dp)
            .background(Palette.textDim.copy(alpha = 0.35f), RoundedCornerShape(3.dp)),
    ) {
        Box(
            Modifier
                .fillMaxWidth(fraction)
                .height(6.dp)
                .background(Palette.accent, RoundedCornerShape(3.dp)),
        )
    }
}

private fun progress(p: Timed, now: Instant): Float {
    val total = Duration.between(p.start, p.end).seconds.toFloat()
    if (total <= 0f) return 0f
    return (Duration.between(p.start, now).seconds / total).coerceIn(0f, 1f)
}
