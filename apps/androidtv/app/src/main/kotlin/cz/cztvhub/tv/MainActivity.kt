package cz.cztvhub.tv

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import cz.cztvhub.core.PlayRequest
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

    private fun play(request: PlayRequest) {
        startActivity(PlayRequests.intent(this, request))
    }
}
