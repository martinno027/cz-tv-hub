package cz.cztvhub.tv.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.PspState
import cz.cztvhub.core.PspStream
import cz.cztvhub.tv.R

/**
 * Poslanecká sněmovna "is it on now?" checks while the calling screen (Živě or the guide) is composed and the app
 * is visible; leaving the screen, opening the player or going to the background stops them (spec/apps.md).
 */
@Composable
fun PspPolling(viewModel: MainViewModel) {
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(viewModel, lifecycle) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) { viewModel.pollPsp() }
    }
}

/** "Přímý přenos" / "Nevysílá se" / "Zjišťuji…". */
fun pspStateText(state: PspState): Int = when (state) {
    PspState.LIVE -> R.string.psp_live
    PspState.OFF -> R.string.psp_off
    PspState.UNKNOWN -> R.string.psp_checking
}

/**
 * Selecting a stream on Živě or by number: after the pre-play check ([MainViewModel.checkPsp]) one that is off gets
 * [notice] ("Poslanecká sněmovna teď nevysílá.", no player); live or still unknown plays it live.
 */
fun selectPsp(viewModel: MainViewModel, stream: PspStream, onPlay: (PlayRequest) -> Unit, notice: () -> Unit) {
    viewModel.checkPsp(stream.id) { state ->
        if (state == PspState.OFF) notice() else onPlay(PlayRequest.PspLive(stream.id, stream.name))
    }
}
