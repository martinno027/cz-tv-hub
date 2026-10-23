package cz.cztvhub.tv

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.tv.ui.ChannelOrder
import cz.cztvhub.tv.ui.MainScreen
import cz.cztvhub.tv.ui.MainViewModel
import cz.cztvhub.tv.ui.TelevizeTheme

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            TelevizeTheme {
                MainScreen(viewModel, onPlay = ::play)
            }
        }
    }

    override fun onResume() {
        super.onResume()
        viewModel.refreshOnAir()
    }

    /** Live playback gets the visible channels, so the player can zap (spec/apps.md "Channel numbers and zapping"). */
    private fun play(request: PlayRequest) {
        val intent = PlayRequests.intent(this, request)
        if (ZapChannel.keyOf(request) != null) {
            val visible = ChannelOrder.visible(viewModel.channelPrefs.value, viewModel.dvb.value?.channels.orEmpty())
            PlayRequests.withChannels(intent, visible.map { ZapChannel(it.key, it.name) })
        }
        startActivity(intent)
    }
}
