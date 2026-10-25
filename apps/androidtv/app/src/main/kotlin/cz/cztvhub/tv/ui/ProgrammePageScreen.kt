package cz.cztvhub.tv.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.tv.material3.Button
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import cz.cztvhub.core.Days
import cz.cztvhub.core.ImageRequest
import cz.cztvhub.core.PageButton
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.ProgrammePage
import cz.cztvhub.core.PspState
import cz.cztvhub.core.dvb.ProgrammeDetails
import cz.cztvhub.tv.R
import java.time.Instant

private val IMAGE_WIDTH = 384.dp

/** TMDb's required attribution (spec/dvb-gateway.md), when an older gateway answer leaves it out. */
private const val TMDB_ATTRIBUTION =
    "This application uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB."

/**
 * The programme page (spec/apps.md "Programme page (T15)"): full screen over the guide, which stays composed below
 * it, so Back ([onDismiss]) returns to the same focused cell. It stays open under the player. The page shows at once; [details] (TMDb, through the
 * gateway) fill in when they arrive, null = the page without them.
 */
@Composable
fun ProgrammePageDialog(
    page: ProgrammePageModel,
    now: Instant,
    details: ProgrammeDetails?,
    images: GuideImages,
    /** The guide detail's image (CT image, gateway thumbnail), used without a TMDb poster. */
    guideImage: ImageRequest?,
    poster: ImageRequest?,
    notice: String?,
    onPlay: (PlayRequest) -> Unit,
    onOpenApp: () -> Unit,
    onDismiss: () -> Unit,
) {
    val entry = page.entry
    val match = details?.match
    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false),
    ) {
        Column(
            Modifier
                .fillMaxSize()
                .background(Palette.background)
                .padding(horizontal = 48.dp, vertical = 32.dp),
        ) {
            Row(Modifier.weight(1f).fillMaxWidth()) {
                PageImage(images, poster, guideImage, entry)
                Spacer(Modifier.width(32.dp))
                Column(Modifier.weight(1f)) {
                    Text(
                        entry.title,
                        style = MaterialTheme.typography.headlineMedium,
                        color = Palette.text,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                    if (entry.subtitle.isNotBlank()) {
                        Text(entry.subtitle, style = MaterialTheme.typography.titleMedium, color = Palette.textDim, maxLines = 2)
                    }
                    Spacer(Modifier.height(8.dp))
                    FactsLine(entry, now, details)
                    match?.rating?.let { ProgrammePage.ratingLine(it.value, it.votes) }?.let {
                        Text(it, style = MaterialTheme.typography.bodyLarge, color = Palette.text)
                    }
                    ProgrammePage.originalTitleLine(entry.title, match?.title, match?.originalTitle)?.let {
                        Text(it, style = MaterialTheme.typography.bodyMedium, color = Palette.textDim)
                    }
                    Spacer(Modifier.height(8.dp))
                    Text(
                        notice ?: stateLabel(entry, now, PspState.UNKNOWN),
                        style = MaterialTheme.typography.bodyLarge,
                        color = if (notice != null) Palette.nowLine else Palette.accent,
                    )
                    Spacer(Modifier.height(12.dp))
                    // The guide's description, else TMDb's overview; cut only where the screen ends.
                    Text(
                        entry.description.ifBlank { match?.overview.orEmpty() },
                        style = MaterialTheme.typography.bodyLarge,
                        color = Palette.text,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                    Spacer(Modifier.height(20.dp))
                    PageButtons(page, onPlay, onOpenApp, onDismiss)
                }
            }
            if (match != null) {
                Spacer(Modifier.height(16.dp))
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    TmdbLogo()
                    Text(
                        details.attribution ?: TMDB_ATTRIBUTION,
                        style = MaterialTheme.typography.labelSmall,
                        color = Palette.textDim,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }
    }
}

/** The TMDb poster (2:3) when there is one, else the guide's image (16:9) or the source placeholder. */
@Composable
private fun PageImage(images: GuideImages, poster: ImageRequest?, guideImage: ImageRequest?, entry: Entry) {
    val shape = RoundedCornerShape(10.dp)
    val posterBitmap by produceState<ImageBitmap?>(null, poster) {
        value = poster?.let(images::decoded)
        if (poster != null && value == null) value = images.image(poster)
    }
    val bitmap = posterBitmap
    if (bitmap != null) {
        Image(
            bitmap,
            contentDescription = null,
            modifier = Modifier.width(IMAGE_WIDTH * 2 / 3).height(IMAGE_WIDTH).clip(shape),
            contentScale = ContentScale.Crop,
        )
    } else {
        val placeholder = if (entry is DvbEntry) ChannelSource.DVB else null
        GuideImage(images, guideImage, placeholder, Modifier.width(IMAGE_WIDTH).height(IMAGE_WIDTH * 9 / 16).clip(shape))
    }
}

/** Channel (with its source icon) · `Dnes 20:15–22:05` · `110 min` · genre · year. */
@Composable
private fun FactsLine(entry: Entry, now: Instant, details: ProgrammeDetails?) {
    val match = details?.match
    val day = Days.label(Days.chipDay(entry.start), Days.today(now))
    val facts = listOf(
        "$day ${Days.timeRange(entry.start, entry.end)}",
        "${ProgrammePage.minutes(entry.start, entry.end)} min",
        ProgrammePage.genre(entry.genre, match?.genres.orEmpty()),
        match?.year?.toString().orEmpty(),
    ).filter(String::isNotBlank)
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        SourceIcon(entry is DvbEntry, size = 22.dp)
        Text(
            (listOf(entry.channelName) + facts).joinToString("  ·  "),
            style = MaterialTheme.typography.bodyLarge,
            color = Palette.text,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

/**
 * The page's buttons in order, then "Zpět"; focus starts on the first (on "Zpět" when there is none), and again
 * when the page is recomputed after the player (a new "Pokračovat od …" comes first).
 */
@Composable
private fun PageButtons(page: ProgrammePageModel, onPlay: (PlayRequest) -> Unit, onOpenApp: () -> Unit, onDismiss: () -> Unit) {
    val first = remember { FocusRequester() }
    Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
        page.actions.forEachIndexed { index, action ->
            val modifier = if (index == 0) Modifier.focusRequester(first) else Modifier
            val label = when (action.button) {
                PageButton.RESUME -> stringResource(R.string.choice_resume, page.resumeLabel.orEmpty())
                PageButton.START_OVER -> stringResource(R.string.choice_from_start)
                PageButton.LIVE -> stringResource(R.string.choice_live)
                PageButton.OPEN_OFFICIAL_APP -> stringResource(R.string.open_ivysilani)
            }
            Button(
                onClick = { action.request?.let(onPlay) ?: onOpenApp() },
                modifier = modifier,
            ) { Text(label) }
        }
        Button(
            onClick = onDismiss,
            modifier = if (page.actions.isEmpty()) Modifier.focusRequester(first) else Modifier,
        ) { Text(stringResource(R.string.back)) }
    }
    LaunchedEffect(page) { runCatching { first.requestFocus() } }
}

/** TMDb's official logo (spec/assets/tmdb-logo-short.svg, colours and shape unchanged), small. */
@Composable
internal fun TmdbLogo() {
    Image(
        painterResource(R.drawable.tmdb_logo_short),
        contentDescription = "TMDB",
        modifier = Modifier.height(12.dp),
    )
}
