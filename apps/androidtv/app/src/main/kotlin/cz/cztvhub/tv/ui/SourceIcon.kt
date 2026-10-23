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
import cz.cztvhub.tv.R

/** Where a channel comes from: globe = CT internet, antenna = Anténa (spec/apps.md "Source icon"). */
@Composable
fun SourceIcon(antenna: Boolean, size: Dp = 22.dp, modifier: Modifier = Modifier) {
    Icon(
        painter = painterResource(if (antenna) R.drawable.ic_source_antenna else R.drawable.ic_source_internet),
        contentDescription = stringResource(if (antenna) R.string.source_antenna else R.string.source_internet),
        modifier = modifier.size(size),
    )
}

/** A channel's number (its place among the visible channels, spec/apps.md "Channel numbers and zapping"), dimmed. */
@Composable
fun ChannelNumber(number: Int, modifier: Modifier = Modifier, style: TextStyle = MaterialTheme.typography.titleMedium) {
    Text("$number", style = style, color = LocalContentColor.current.copy(alpha = 0.6f), maxLines = 1, modifier = modifier)
}
