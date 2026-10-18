package cz.cztvhub.tv.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.LocalContentColor
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Tab
import androidx.tv.material3.TabRow
import androidx.tv.material3.Text
import cz.cztvhub.core.Days
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.tv.R

@Composable
fun MainScreen(viewModel: MainViewModel, onPlay: (PlayRequest) -> Unit) {
    var tab by rememberSaveable { mutableIntStateOf(0) }
    val tabs = listOf(
        stringResource(R.string.tab_live),
        stringResource(R.string.tab_guide),
        stringResource(R.string.tab_settings),
    )

    CompositionLocalProvider(LocalContentColor provides Palette.text) {
    Column(
        Modifier
            .fillMaxSize()
            .background(Palette.background)
            .padding(horizontal = 40.dp, vertical = 20.dp),
    ) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            TabRow(selectedTabIndex = tab) {
                tabs.forEachIndexed { index, title ->
                    Tab(selected = tab == index, onFocus = { tab = index }, onClick = { tab = index }) {
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
            else -> SettingsScreen(viewModel)
        }
    }
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
