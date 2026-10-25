package cz.cztvhub.tv.ui

import android.view.KeyEvent as AndroidKeyEvent
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.focusGroup
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Button
import androidx.tv.material3.ClickableSurfaceDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Surface
import androidx.tv.material3.Text
import cz.cztvhub.core.ContinueItem
import cz.cztvhub.core.ImageRequest
import cz.cztvhub.core.PageSource
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.ResumeCard
import cz.cztvhub.core.ResumeRules
import cz.cztvhub.core.archive.ArchiveCard
import cz.cztvhub.core.archive.ArchiveMessages
import cz.cztvhub.core.archive.ArchiveRules
import cz.cztvhub.core.archive.ArchiveSort
import cz.cztvhub.core.archive.CardSource
import cz.cztvhub.tv.R
import cz.cztvhub.tv.tvApp

private val CARD_WIDTH = 240.dp
private val CARD_IMAGE_HEIGHT = CARD_WIDTH * 9 / 16

/** Plays a request, with what "Pokračovat ve sledování" keeps with its entry. */
typealias PlayWithCard = (PlayRequest, ResumeCard?) -> Unit

/**
 * The Archiv tab (spec/apps.md "Archiv (T13)"): a search field, then the rows "Pokračovat ve sledování", "Naše
 * nahrávky" and the categories, as the gateway orders them. A category and a show open full screen over the tab
 * (Back returns to the same card); the show page stays under the player.
 */
@Composable
fun ArchiveScreen(viewModel: ArchiveViewModel, main: MainViewModel, onPlay: PlayWithCard) {
    val configured by viewModel.configured.collectAsStateWithLifecycle()
    if (!configured) {
        CenteredMessage(ArchiveMessages.NO_GATEWAY)
        return
    }
    // Back from the player too: the row and the page's "Pokračovat od …" from what the player saved.
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { viewModel.opened() }
    LaunchedEffect(Unit) { viewModel.opened() }

    val context = LocalContext.current
    val images = remember { ArchiveImages(context.tvApp.images) }
    val dvb by main.dvb.collectAsStateWithLifecycle()
    val thumbnailsOnTv by main.antennaThumbnails.collectAsStateWithLifecycle()
    val thumbnails = dvb?.thumbnails == true && thumbnailsOnTv
    val imageOf: (String?) -> ImageRequest? = remember(thumbnails) { { viewModel.image(it, thumbnails) } }

    val home by viewModel.home.collectAsStateWithLifecycle()
    val continueRow by viewModel.continueRow.collectAsStateWithLifecycle()
    val query by viewModel.query.collectAsStateWithLifecycle()
    val search by viewModel.search.collectAsStateWithLifecycle()
    val category by viewModel.category.collectAsStateWithLifecycle()
    val show by viewModel.show.collectAsStateWithLifecycle()
    var removing by remember { mutableStateOf<ContinueItem?>(null) }

    Column(Modifier.fillMaxSize()) {
        SearchField(query, viewModel::setQuery)
        // Back on search results first clears the search (shared details), then leaves as usual.
        BackHandler(enabled = query.isNotEmpty() && category == null && show == null) { viewModel.setQuery("") }
        Spacer(Modifier.height(12.dp))
        // iVysílání did not answer: its text, our recordings are still listed.
        (search?.notice ?: (home as? Load.Ready)?.value?.error)?.let {
            Text(it, style = MaterialTheme.typography.bodyLarge, color = Palette.nowLine, modifier = Modifier.padding(bottom = 8.dp))
        }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            val results = search
            if (results != null) {
                CardGrid(results, images, imageOf, onOpen = viewModel::openShow, onMore = viewModel::moreSearch)
            } else {
                when (val h = home) {
                    Load.Loading -> CenteredMessage(stringResource(R.string.loading))
                    is Load.Failed -> CenteredMessage(h.message) {
                        Button(onClick = viewModel::loadHome) { Text(stringResource(R.string.retry)) }
                    }
                    is Load.Ready -> {
                        if (h.value.rows.isEmpty() && continueRow.isEmpty()) {
                            CenteredMessage(ArchiveMessages.EMPTY)
                        } else {
                            LazyColumn(verticalArrangement = Arrangement.spacedBy(16.dp)) {
                                if (continueRow.isNotEmpty()) item(key = "continue") {
                                    ContinueRow(continueRow, images, imageOf, onPlay, onRemove = { removing = it })
                                }
                                items(h.value.rows, key = { it.id }) { row ->
                                    CardRow(
                                        row.title, row.cards, images, imageOf,
                                        onOpen = viewModel::openShow,
                                        onAll = { viewModel.openCategory(row.id, row.title) },
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
        val ready = (home as? Load.Ready)?.value
        val shown = search?.cards ?: ready?.rows?.flatMap { it.cards }.orEmpty()
        if (ArchiveRules.showsTmdb(shown)) TmdbFooter(search?.attribution ?: ready?.attribution)
    }

    category?.let { state ->
        CategoryDialog(state, images, imageOf, viewModel, onOpen = viewModel::openShow, onDismiss = viewModel::closeCategory)
    }
    show?.let { state ->
        ShowPageDialog(state, images, imageOf, viewModel, onPlay, onDismiss = viewModel::closeShow)
    }
    removing?.let { item ->
        RemoveDialog(item, onRemove = {
            viewModel.removeFromRow(item)
            removing = null
        }, onDismiss = { removing = null })
    }
}

/** The search field: read-only until OK (walking past it never opens the keyboard), then the TV's keyboard. */
@Composable
private fun SearchField(value: String, onChange: (String) -> Unit) {
    var focused by remember { mutableStateOf(false) }
    var editing by remember { mutableStateOf(false) }
    val focusManager = LocalFocusManager.current
    val keyboard = LocalSoftwareKeyboardController.current
    BasicTextField(
        value = value,
        onValueChange = onChange,
        singleLine = true,
        readOnly = !editing,
        textStyle = MaterialTheme.typography.titleMedium.copy(color = Palette.text),
        cursorBrush = SolidColor(Palette.accent),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Text, imeAction = ImeAction.Search),
        keyboardActions = KeyboardActions(onSearch = {
            keyboard?.hide()
            editing = false
            focusManager.moveFocus(FocusDirection.Down)
        }),
        modifier = Modifier
            .widthIn(max = 720.dp)
            .fillMaxWidth()
            .onFocusChanged {
                focused = it.isFocused
                if (!it.isFocused) editing = false
            }
            .onPreviewKeyEvent { event ->
                when (event.key) {
                    Key.DirectionDown -> {
                        if (event.type == KeyEventType.KeyDown) focusManager.moveFocus(FocusDirection.Down)
                        true
                    }
                    Key.DirectionCenter, Key.Enter, Key.NumPadEnter -> {
                        if (editing) return@onPreviewKeyEvent false
                        if (event.type == KeyEventType.KeyUp) {
                            editing = true
                            keyboard?.show()
                        }
                        true
                    }
                    else -> false
                }
            }
            .background(Palette.surface, RoundedCornerShape(8.dp))
            .border(2.dp, if (focused) Palette.focused else Palette.surface, RoundedCornerShape(8.dp))
            .padding(horizontal = 14.dp, vertical = 10.dp),
        decorationBox = { inner ->
            if (value.isEmpty()) Text(stringResource(R.string.archive_search_hint), color = Palette.textDim)
            inner()
        },
    )
}

@Composable
private fun RowTitle(title: String) {
    Text(title, style = MaterialTheme.typography.titleLarge, color = Palette.text, modifier = Modifier.padding(bottom = 8.dp))
}

/** A row of cards ending with "Vše ›", which opens the whole category. */
@Composable
private fun CardRow(
    title: String,
    cards: List<ArchiveCard>,
    images: ArchiveImages,
    imageOf: (String?) -> ImageRequest?,
    onOpen: (ArchiveCard) -> Unit,
    onAll: () -> Unit,
) {
    Column {
        RowTitle(title)
        LazyRow(
            horizontalArrangement = Arrangement.spacedBy(16.dp),
            contentPadding = PaddingValues(end = 24.dp),
            modifier = Modifier.focusGroup(),
        ) {
            items(cards, key = { it.id }) { card -> ShowCard(card, images, imageOf) { onOpen(card) } }
            item(key = "more") { MoreTile(onAll) }
        }
    }
}

/** A show's card: image, title, `2019 · film`, "★ 7,4" and the source icons. */
@Composable
private fun ShowCard(
    card: ArchiveCard,
    images: ArchiveImages,
    imageOf: (String?) -> ImageRequest?,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    val icons = ArchiveRules.icons(card.source)
    CardSurface(onClick = onClick, modifier = modifier) {
        ArchiveImage(
            images, remember(card.image, imageOf) { imageOf(card.image) },
            if (card.source == CardSource.REC) ChannelSource.DVB else ChannelSource.CT,
            Modifier.width(CARD_WIDTH).height(CARD_IMAGE_HEIGHT).clip(RoundedCornerShape(topStart = 10.dp, topEnd = 10.dp)),
        )
        Column(Modifier.width(CARD_WIDTH).padding(horizontal = 10.dp, vertical = 8.dp)) {
            Text(card.title, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(
                    ArchiveRules.metaLine(card),
                    style = MaterialTheme.typography.bodySmall,
                    maxLines = 1,
                    modifier = Modifier.weight(1f, fill = true),
                )
                ArchiveRules.star(card.rating)?.let { Text(it, style = MaterialTheme.typography.bodySmall, maxLines = 1) }
                icons.forEach { SourceIcon(it == PageSource.DVB, size = 16.dp) }
            }
        }
    }
}

@Composable
private fun CardSurface(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    onLongClick: (() -> Unit)? = null,
    content: @Composable () -> Unit,
) {
    Surface(
        onClick = onClick,
        onLongClick = onLongClick,
        modifier = modifier,
        shape = ClickableSurfaceDefaults.shape(RoundedCornerShape(10.dp)),
        scale = ClickableSurfaceDefaults.scale(focusedScale = 1.05f),
        colors = ClickableSurfaceDefaults.colors(
            containerColor = Palette.surface,
            contentColor = Palette.text,
            focusedContainerColor = Palette.focused,
            focusedContentColor = Palette.onFocused,
        ),
    ) {
        Column { content() }
    }
}

@Composable
private fun MoreTile(onClick: () -> Unit) {
    CardSurface(onClick = onClick) {
        Box(Modifier.width(CARD_WIDTH / 2).height(CARD_IMAGE_HEIGHT + 56.dp), contentAlignment = Alignment.Center) {
            Text(stringResource(R.string.archive_all_more), style = MaterialTheme.typography.titleLarge)
        }
    }
}

/**
 * "Pokračovat ve sledování": this TV's resume entries with a title, newest first. OK plays at once from the saved
 * position; long OK or the menu key offers "Odebrat z řady".
 */
@Composable
private fun ContinueRow(
    items: List<ContinueItem>,
    images: ArchiveImages,
    imageOf: (String?) -> ImageRequest?,
    onPlay: PlayWithCard,
    onRemove: (ContinueItem) -> Unit,
) {
    Column {
        RowTitle(stringResource(R.string.archive_continue))
        LazyRow(horizontalArrangement = Arrangement.spacedBy(16.dp), modifier = Modifier.focusGroup()) {
            items(items, key = { it.key }) { item ->
                val card = item.card
                CardSurface(
                    onClick = { ArchiveRules.continueRequest(item)?.let { onPlay(it, card) } },
                    onLongClick = { onRemove(item) },
                    modifier = Modifier.onPreviewKeyEvent {
                        val menu = it.nativeKeyEvent.keyCode == AndroidKeyEvent.KEYCODE_MENU
                        if (menu && it.type == KeyEventType.KeyUp) onRemove(item)
                        menu
                    },
                ) {
                    ArchiveImage(
                        images, remember(card.image, imageOf) { imageOf(card.image) },
                        if (card.source == "dvb") ChannelSource.DVB else ChannelSource.CT,
                        Modifier.width(CARD_WIDTH).height(CARD_IMAGE_HEIGHT).clip(RoundedCornerShape(topStart = 10.dp, topEnd = 10.dp)),
                    )
                    Column(Modifier.width(CARD_WIDTH).padding(horizontal = 10.dp, vertical = 8.dp)) {
                        Text(card.showTitle ?: card.title, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text(
                                listOfNotNull(
                                    card.title.takeIf { card.showTitle != null },
                                    stringResource(R.string.choice_resume, ResumeRules.label(item.entry.progressSeconds)),
                                ).joinToString(" · "),
                                style = MaterialTheme.typography.bodySmall,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                                modifier = Modifier.weight(1f, fill = true),
                            )
                            SourceIcon(card.source == "dvb", size = 16.dp)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun RemoveDialog(item: ContinueItem, onRemove: () -> Unit, onDismiss: () -> Unit) {
    Dialog(onDismissRequest = onDismiss) {
        Column(
            Modifier.background(Palette.surface, RoundedCornerShape(16.dp)).padding(32.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text(item.card.showTitle ?: item.card.title, style = MaterialTheme.typography.titleLarge, color = Palette.text)
            val first = remember { FocusRequester() }
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                Button(onClick = onRemove, modifier = Modifier.focusRequester(first)) {
                    Text(stringResource(R.string.archive_remove))
                }
                Button(onClick = onDismiss) { Text(stringResource(R.string.back)) }
            }
            LaunchedEffect(Unit) { runCatching { first.requestFocus() } }
        }
    }
}

/** A paged grid of cards: the next page when the focus nears the end ([ArchiveRules.PAGE_AHEAD]). */
@Composable
private fun CardGrid(
    grid: ArchiveGrid,
    images: ArchiveImages,
    imageOf: (String?) -> ImageRequest?,
    onOpen: (ArchiveCard) -> Unit,
    onMore: () -> Unit,
) {
    when {
        grid.cards.isEmpty() && grid.loading -> CenteredMessage(stringResource(R.string.loading))
        grid.cards.isEmpty() && grid.error != null -> CenteredMessage(grid.error) {
            Button(onClick = onMore) { Text(stringResource(R.string.retry)) }
        }
        grid.cards.isEmpty() -> CenteredMessage(ArchiveMessages.NO_RESULTS)
        else -> LazyVerticalGrid(
            columns = GridCells.Adaptive(CARD_WIDTH),
            horizontalArrangement = Arrangement.spacedBy(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
            contentPadding = PaddingValues(8.dp),
        ) {
            itemsIndexed(grid.cards, key = { _, card -> card.id }) { index, card ->
                ShowCard(
                    card, images, imageOf,
                    modifier = Modifier.onFocusChanged {
                        if (it.isFocused && index >= grid.cards.size - ArchiveRules.PAGE_AHEAD) onMore()
                    },
                ) { onOpen(card) }
            }
        }
    }
}

/** A whole category: sub-genre chips and the sort on top, a paged grid below. */
@Composable
private fun CategoryDialog(
    state: CategoryState,
    images: ArchiveImages,
    imageOf: (String?) -> ImageRequest?,
    viewModel: ArchiveViewModel,
    onOpen: (ArchiveCard) -> Unit,
    onDismiss: () -> Unit,
) {
    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false),
    ) {
        Column(
            Modifier.fillMaxSize().background(Palette.background).padding(horizontal = 48.dp, vertical = 32.dp),
        ) {
            Text(state.grid.title.ifBlank { state.title }, style = MaterialTheme.typography.headlineMedium, color = Palette.text)
            Spacer(Modifier.height(12.dp))
            val first = remember { FocusRequester() }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.focusGroup()) {
                ArchiveSort.entries.forEach { sort ->
                    Chip(
                        sortLabel(sort),
                        selected = state.sort == sort,
                        modifier = if (sort == ArchiveSort.POPULAR) Modifier.focusRequester(first) else Modifier,
                    ) { viewModel.setSort(sort) }
                }
            }
            LaunchedEffect(state.slug) { runCatching { first.requestFocus() } }
            if (state.grid.genres.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.focusGroup()) {
                    item(key = "all") { Chip(stringResource(R.string.archive_all), selected = state.genre == null) { viewModel.setGenre(null) } }
                    items(state.grid.genres, key = { it.id }) { genre ->
                        Chip(genre.title, selected = state.genre == genre.id) { viewModel.setGenre(genre.id) }
                    }
                }
            }
            Spacer(Modifier.height(16.dp))
            Box(Modifier.weight(1f).fillMaxWidth()) {
                CardGrid(state.grid, images, imageOf, onOpen, onMore = viewModel::moreCategory)
            }
            if (state.grid.notice != null) Text(state.grid.notice, color = Palette.nowLine)
            if (ArchiveRules.showsTmdb(state.grid.cards)) TmdbFooter(state.grid.attribution)
        }
    }
}

@Composable
private fun sortLabel(sort: ArchiveSort): String = stringResource(
    when (sort) {
        ArchiveSort.POPULAR -> R.string.archive_sort_popular
        ArchiveSort.NEWEST -> R.string.archive_sort_newest
        ArchiveSort.AZ -> R.string.archive_sort_az
    },
)

@Composable
internal fun Chip(label: String, selected: Boolean, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Surface(
        onClick = onClick,
        modifier = modifier,
        shape = ClickableSurfaceDefaults.shape(RoundedCornerShape(18.dp)),
        scale = ClickableSurfaceDefaults.scale(focusedScale = 1.05f),
        colors = ClickableSurfaceDefaults.colors(
            containerColor = if (selected) Palette.accent else Palette.surface,
            contentColor = Palette.text,
            focusedContainerColor = Palette.focused,
            focusedContentColor = Palette.onFocused,
        ),
    ) {
        Text(label, style = MaterialTheme.typography.titleSmall, maxLines = 1, modifier = Modifier.padding(horizontal = 14.dp, vertical = 6.dp))
    }
}

/** The TMDB logo and attribution sentence, small, at the bottom of a screen whose cards show TMDb data. */
@Composable
internal fun TmdbFooter(attribution: String?) {
    Spacer(Modifier.height(8.dp))
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        TmdbLogo()
        Text(ArchiveRules.attribution(attribution), style = MaterialTheme.typography.labelSmall, color = Palette.textDim, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

/** Dimmed rows of what cannot be played; they still play, to show ČT's own text. */
internal fun Modifier.dimmed(dim: Boolean): Modifier = if (dim) alpha(0.5f) else this
