package cz.cztvhub.tv.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle

/**
 * A failed ČT day is asked again by itself (spec/apps.md, T42) while the calling screen — Živě ([live]) or the
 * guide — is composed and the app is visible; leaving it, opening the player or the background stops it.
 */
@Composable
fun ScheduleRetrying(viewModel: MainViewModel, live: Boolean) {
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(viewModel, lifecycle, live) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) { viewModel.pollSchedules(live) }
    }
}
