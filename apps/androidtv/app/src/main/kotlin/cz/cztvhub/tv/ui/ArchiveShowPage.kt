package cz.cztvhub.tv.ui

import android.view.KeyEvent as AndroidKeyEvent
import androidx.compose.foundation.background
import androidx.compose.foundation.focusGroup
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.tv.material3.Button
import androidx.tv.material3.ClickableSurfaceDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Surface
import androidx.tv.material3.Text
import cz.cztvhub.core.ImageRequest
import cz.cztvhub.core.PageSource
import cz.cztvhub.core.ProgrammePage
import cz.cztvhub.core.ResumeRules
import cz.cztvhub.core.archive.ArchiveCard
import cz.cztvhub.core.archive.ArchiveEpisode
import cz.cztvhub.core.archive.ArchiveRules
import cz.cztvhub.core.archive.ArchiveShow
import cz.cztvhub.core.archive.CardKind
import cz.cztvhub.core.archive.CardSource
import cz.cztvhub.core.archive.EpisodeSource
import cz.cztvhub.tv.R
import cz.cztvhub.tv.tvApp
import java.time.Instant

private val PAGE_IMAGE_WIDTH = 384.dp

/**
 * The show page (spec/apps.md "Archiv", the T15 page extended for shows): image, title, facts, TMDb rating,
 * description, creators; a film gets [`Pokračovat od …`] `Přehrát` `Zpět`, a series season chips and its episodes,
 * then "Nahráno z antény". It stays under the player and reads "Pokračovat" again when the player closes.
 */
@Composable
fun ShowPageDialog(
    state: ShowState,
    images: ArchiveImages,
    imageOf: (String?) -> ImageRequest?,
    viewModel: ArchiveViewModel,
    onPlay: PlayWithCard,
    onDismiss: () -> Unit,
) {
    val resume = LocalContext.current.tvApp.resume
    var generation by remember { mutableIntStateOf(0) }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { generation++ }
    val offer: (ArchiveEpisode) -> Double? = remember(generation) { { resume.offer(ArchiveRules.resumeKey(it)) } }
    val show = (state.show as? Load.Ready)?.value
    val card = show?.card ?: state.card
    val play: (ArchiveEpisode, Double?) -> Unit = { episode, from ->
        onPlay(ArchiveRules.request(episode, from), ArchiveRules.resumeCard(episode, card))
    }

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false),
    ) {
        Column(Modifier.fillMaxSize().background(Palette.background).padding(horizontal = 48.dp, vertical = 32.dp)) {
            Row(Modifier.weight(1f).fillMaxWidth()) {
                ShowImage(images, imageOf(card.image), card)
                Spacer(Modifier.width(32.dp))
                Column(Modifier.weight(1f)) {
                    Text(card.title, style = MaterialTheme.typography.headlineMedium, color = Palette.text, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    Spacer(Modifier.height(8.dp))
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        ArchiveRules.icons(card.source).forEach { SourceIcon(it == PageSource.DVB, size = 22.dp) }
                        Text(
                            show?.let(ArchiveRules::factsLine) ?: ArchiveRules.metaLine(card),
                            style = MaterialTheme.typography.bodyLarge,
                            color = Palette.text,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    card.rating?.let { ProgrammePage.ratingLine(it.value, it.votes) }?.let {
                        Text(it, style = MaterialTheme.typography.bodyLarge, color = Palette.text)
                    }
                    show?.creators?.takeIf { it.isNotBlank() }?.let {
                        Text(it, style = MaterialTheme.typography.bodyMedium, color = Palette.textDim, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    }
                    Spacer(Modifier.height(8.dp))
                    when (val s = state.show) {
                        Load.Loading -> Text(stringResource(R.string.loading), color = Palette.textDim)
                        is Load.Failed -> Text(s.message, color = Palette.nowLine)
                        is Load.Ready -> s.value.error?.let { Text(it, color = Palette.nowLine) }
                    }
                    when (val s = state.show) {
                        Load.Loading, is Load.Failed -> Unit
                        is Load.Ready -> Text(
                            s.value.description,
                            style = MaterialTheme.typography.bodyLarge,
                            color = Palette.text,
                            maxLines = if (card.kind == CardKind.MOVIE) 8 else 3,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    Spacer(Modifier.height(16.dp))
                    if (show == null) {
                        BackOnly(onDismiss)
                    } else if (card.kind == CardKind.MOVIE) {
                        FilmButtons(show, offer, play, onDismiss)
                    } else {
                        Episodes(state, show, offer, play, viewModel, onDismiss, Modifier.weight(1f))
                    }
                }
            }
            if (card.tmdb) TmdbFooter(show?.attribution)
        }
    }
}

/** A TMDb poster (2:3) as such, any other image 16:9; the source icon until it arrives. */
@Composable
private fun ShowImage(images: ArchiveImages, request: ImageRequest?, card: ArchiveCard) {
    val poster = card.image?.startsWith("/dvb/details/image/") == true
    val shape = RoundedCornerShape(10.dp)
    val modifier = if (poster) {
        Modifier.width(PAGE_IMAGE_WIDTH * 2 / 3).height(PAGE_IMAGE_WIDTH)
    } else {
        Modifier.width(PAGE_IMAGE_WIDTH).height(PAGE_IMAGE_WIDTH * 9 / 16)
    }
    ArchiveImage(images, request, if (card.source == CardSource.REC) ChannelSource.DVB else ChannelSource.CT, modifier.clip(shape))
}

@Composable
private fun BackOnly(onDismiss: () -> Unit) {
    val first = remember { FocusRequester() }
    Button(onClick = onDismiss, modifier = Modifier.focusRequester(first)) { Text(stringResource(R.string.back)) }
    LaunchedEffect(Unit) { runCatching { first.requestFocus() } }
}

/** A film: [`Pokračovat od …`] `Přehrát` `Zpět` (the T15 order); its playable entry is the first episode. */
@Composable
private fun FilmButtons(
    show: ArchiveShow,
    offer: (ArchiveEpisode) -> Double?,
    play: (ArchiveEpisode, Double?) -> Unit,
    onDismiss: () -> Unit,
) {
    val episodes = show.groups.flatMap { it.episodes }
    val episode = episodes.firstOrNull { it.source == EpisodeSource.CT } ?: episodes.firstOrNull()
    val from = episode?.let(offer)
    val first = remember { FocusRequester() }
    Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
        if (episode != null && from != null) {
            Button(onClick = { play(episode, from) }, modifier = Modifier.focusRequester(first)) {
                Text(stringResource(R.string.choice_resume, ResumeRules.label(from)))
            }
        }
        if (episode != null) {
            Button(
                onClick = { play(episode, null) },
                modifier = (if (from == null) Modifier.focusRequester(first) else Modifier).dimmed(!episode.playable),
            ) { Text(stringResource(R.string.archive_play)) }
        }
        Button(onClick = onDismiss, modifier = if (episode == null) Modifier.focusRequester(first) else Modifier) {
            Text(stringResource(R.string.back))
        }
    }
    LaunchedEffect(from) { runCatching { first.requestFocus() } }
}

/**
 * A series: season chips, then each group's episodes (`1/26 Od začátku · 53 min · ČT1 9. 1.`), each `Přehrát` or
 * `Pokračovat od …`, dimmed when not playable; the season's next page when the focus nears its end.
 */
@Composable
private fun Episodes(
    state: ShowState,
    show: ArchiveShow,
    offer: (ArchiveEpisode) -> Double?,
    play: (ArchiveEpisode, Double?) -> Unit,
    viewModel: ArchiveViewModel,
    onDismiss: () -> Unit,
    modifier: Modifier,
) {
    val first = remember { FocusRequester() }
    var choosing by remember { mutableStateOf<ArchiveEpisode?>(null) }
    choosing?.let { episode ->
        FromStartDialog(episode, onPlay = {
            choosing = null
            play(episode, null)
        }, onDismiss = { choosing = null })
    }
    Column(modifier) {
        if (show.seasons.size > 1) {
            LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.focusGroup()) {
                items(show.seasons, key = { it.id }) { season ->
                    Chip(
                        season.title,
                        selected = state.season == season.id,
                        modifier = if (season.id == state.season) Modifier.focusRequester(first) else Modifier,
                    ) { viewModel.loadSeason(season.id) }
                }
            }
            Spacer(Modifier.height(12.dp))
        }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.weight(1f)) {
            show.groups.forEachIndexed { groupIndex, group ->
                if (group.episodes.isEmpty()) return@forEachIndexed
                if (group.title.isNotBlank()) item(key = "title$groupIndex") {
                    Text(group.title, style = MaterialTheme.typography.titleMedium, color = Palette.textDim, modifier = Modifier.padding(top = 4.dp))
                }
                val ctGroup = group.episodes.any { it.source == EpisodeSource.CT }
                itemsIndexed(group.episodes, key = { index, e -> "$groupIndex:$index:${e.idec ?: e.recordingId}" }) { index, episode ->
                    val from = offer(episode)
                    val focus = if (show.seasons.size <= 1 && groupIndex == show.groups.indexOfFirst { it.episodes.isNotEmpty() } && index == 0) {
                        Modifier.focusRequester(first)
                    } else {
                        Modifier
                    }
                    EpisodeRow(
                        episode, from,
                        onFromStart = { choosing = episode },
                        modifier = focus.onFocusChanged {
                            if (it.isFocused && ctGroup && index >= group.episodes.size - ArchiveRules.PAGE_AHEAD) {
                                state.season?.let { season -> viewModel.loadSeason(season, more = true) }
                            }
                        },
                    ) { play(episode, from) }
                }
            }
            if (state.seasonLoading) item(key = "loading") { Text(stringResource(R.string.loading), color = Palette.textDim) }
            item(key = "back") {
                Button(
                    onClick = onDismiss,
                    modifier = if (show.groups.all { it.episodes.isEmpty() } && show.seasons.size <= 1) Modifier.focusRequester(first) else Modifier,
                ) { Text(stringResource(R.string.back)) }
            }
        }
    }
    LaunchedEffect(show.card.id) { runCatching { first.requestFocus() } }
}

@Composable
private fun EpisodeRow(
    episode: ArchiveEpisode,
    from: Double?,
    modifier: Modifier,
    onFromStart: () -> Unit,
    onClick: () -> Unit,
) {
    // With an offer OK resumes; long OK or the menu key offers "Přehrát od začátku" (shared details).
    val other = if (from != null) onFromStart else null
    Surface(
        onClick = onClick,
        onLongClick = other,
        modifier = modifier
            .onPreviewKeyEvent {
                val menu = it.nativeKeyEvent.keyCode == AndroidKeyEvent.KEYCODE_MENU && other != null
                if (menu && it.type == KeyEventType.KeyUp) other.invoke()
                menu
            }
            .fillMaxWidth()
            .dimmed(!episode.playable),
        shape = ClickableSurfaceDefaults.shape(RoundedCornerShape(8.dp)),
        scale = ClickableSurfaceDefaults.scale(focusedScale = 1.01f),
        colors = ClickableSurfaceDefaults.colors(
            containerColor = Palette.surface,
            contentColor = Palette.text,
            focusedContainerColor = Palette.focused,
            focusedContentColor = Palette.onFocused,
        ),
    ) {
        Row(Modifier.padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            SourceIcon(episode.source == EpisodeSource.REC, size = 18.dp)
            Spacer(Modifier.width(10.dp))
            Text(
                ArchiveRules.episodeLine(episode, Instant.now()),
                style = MaterialTheme.typography.bodyLarge,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            Spacer(Modifier.width(16.dp))
            Text(
                if (from != null) stringResource(R.string.choice_resume, ResumeRules.label(from)) else stringResource(R.string.archive_play),
                style = MaterialTheme.typography.titleSmall,
            )
        }
    }
}

@Composable
private fun FromStartDialog(episode: ArchiveEpisode, onPlay: () -> Unit, onDismiss: () -> Unit) {
    Dialog(onDismissRequest = onDismiss) {
        Column(
            Modifier.background(Palette.surface, RoundedCornerShape(16.dp)).padding(32.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text(episode.title, style = MaterialTheme.typography.titleLarge, color = Palette.text)
            val first = remember { FocusRequester() }
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                Button(onClick = onPlay, modifier = Modifier.focusRequester(first)) {
                    Text(stringResource(R.string.archive_play_from_start))
                }
                Button(onClick = onDismiss) { Text(stringResource(R.string.back)) }
            }
            LaunchedEffect(Unit) { runCatching { first.requestFocus() } }
        }
    }
}
