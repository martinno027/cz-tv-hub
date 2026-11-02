package cz.cztvhub.tv.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import cz.cztvhub.core.archive.ArchiveLoading
import cz.cztvhub.core.archive.LoaderPlace
import cz.cztvhub.core.archive.Wait
import kotlinx.coroutines.delay

/**
 * Where the loader of a part waiting in [wait] is drawn now (spec/apps.md "Loading is always visible", T52): nothing
 * for the first [ArchiveLoading.DELAY_MS], then over or below the part. A newer request of the same kind keeps the
 * wait (and the loader shown); the delay starts again only after the part stopped waiting.
 */
@Composable
fun rememberLoaderPlace(wait: Wait): LoaderPlace {
    var waited by remember { mutableStateOf(0L) }
    LaunchedEffect(wait) {
        waited = 0L
        if (wait != Wait.NONE) {
            delay(ArchiveLoading.DELAY_MS)
            waited = ArchiveLoading.DELAY_MS
        }
    }
    return ArchiveLoading.place(wait, waited)
}

/** The spinning ring in the accent colour ([small]: half size, below a list's last row). */
@Composable
fun Spinner(modifier: Modifier = Modifier, small: Boolean = false) {
    val size = if (small) ArchiveLoading.SMALL_SIZE_DP else ArchiveLoading.SIZE_DP
    val turn by rememberInfiniteTransition(label = "loader").animateFloat(
        initialValue = 0f,
        targetValue = 360f,
        animationSpec = infiniteRepeatable(tween(900, easing = LinearEasing), RepeatMode.Restart),
        label = "turn",
    )
    Canvas(modifier.size(size.dp)) {
        val stroke = this.size.minDimension / 10f
        val inset = stroke / 2f
        val arc = Size(this.size.width - stroke, this.size.height - stroke)
        drawArc(Palette.accent.copy(alpha = 0.25f), 0f, 360f, false, Offset(inset, inset), arc, style = Stroke(stroke))
        drawArc(Palette.accent, turn, 100f, false, Offset(inset, inset), arc, style = Stroke(stroke, cap = StrokeCap.Round))
    }
}

/**
 * A part that may wait for new content: [content] stays where it is, dimmed to [ArchiveLoading.DIM_ALPHA] under a
 * centred ring once [place] is [LoaderPlace.OVER]; it keeps the focus and its keys (only drawing is dimmed).
 */
@Composable
fun LoadingBox(place: LoaderPlace, modifier: Modifier = Modifier, content: @Composable BoxScope.() -> Unit) {
    Box(modifier) {
        Box(
            Modifier.fillMaxSize().let { if (ArchiveLoading.dimmed(place)) it.alpha(ArchiveLoading.DIM_ALPHA) else it },
            content = content,
        )
        if (place == LoaderPlace.OVER) Spinner(Modifier.align(Alignment.Center))
    }
}

/** The small ring below a list's last row (its next page, T52). */
@Composable
fun MoreLoader() {
    Box(Modifier.fillMaxWidth().padding(vertical = 8.dp), contentAlignment = Alignment.Center) { Spinner(small = true) }
}

/** A part with nothing to show yet: the ring alone in its middle, after the same delay (T52). */
@Composable
fun CenteredLoader(modifier: Modifier = Modifier) {
    val place = rememberLoaderPlace(Wait.REPLACE)
    Box(modifier, contentAlignment = Alignment.Center) { if (place != LoaderPlace.NONE) Spinner() }
}
