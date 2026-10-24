package cz.cztvhub.tv.ui

import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.text.TextStyle
import androidx.tv.material3.Icon
import androidx.tv.material3.LocalContentColor
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import cz.cztvhub.core.ChannelPrefs
import cz.cztvhub.tv.R

/** Where a channel comes from. */
enum class ChannelSource {
    CT, DVB, PSP;

    companion object {
        /** The source of a "Kanály" key (`ct:`, `dvb:`, `psp:`). */
        fun ofKey(key: String): ChannelSource = when {
            key.startsWith(ChannelPrefs.dvbKey("")) -> DVB
            key.startsWith(ChannelPrefs.pspKey("")) -> PSP
            else -> CT
        }
    }
}

/**
 * Where a channel comes from (spec/apps.md "Source icon"): globe = CT internet, antenna = Anténa, a generic
 * building with columns (Material "AccountBalance") = Poslanecká sněmovna; never the Chamber's logo or emblem.
 */
@Composable
fun SourceIcon(source: ChannelSource, size: Dp = 22.dp, modifier: Modifier = Modifier) {
    Icon(
        painter = painterResource(
            when (source) {
                ChannelSource.CT -> R.drawable.ic_source_internet
                ChannelSource.DVB -> R.drawable.ic_source_antenna
                ChannelSource.PSP -> R.drawable.ic_source_parliament
            },
        ),
        contentDescription = stringResource(
            when (source) {
                ChannelSource.CT -> R.string.source_internet
                ChannelSource.DVB -> R.string.source_antenna
                ChannelSource.PSP -> R.string.source_psp
            },
        ),
        modifier = modifier.size(size),
    )
}

/** Globe or antenna (CT internet or Anténa). */
@Composable
fun SourceIcon(antenna: Boolean, size: Dp = 22.dp, modifier: Modifier = Modifier) =
    SourceIcon(if (antenna) ChannelSource.DVB else ChannelSource.CT, size, modifier)

/** A channel's number (its place among the visible channels, spec/apps.md "Channel numbers and zapping"), dimmed. */
@Composable
fun ChannelNumber(number: Int, modifier: Modifier = Modifier, style: TextStyle = MaterialTheme.typography.titleMedium) {
    Text("$number", style = style, color = LocalContentColor.current.copy(alpha = 0.6f), maxLines = 1, modifier = modifier)
}
