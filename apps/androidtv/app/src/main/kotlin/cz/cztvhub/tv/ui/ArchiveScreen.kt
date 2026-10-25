package cz.cztvhub.tv.ui

import android.view.KeyEvent as AndroidKeyEvent
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.focusGroup
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
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
import cz.cztvhub.core.archive.ArchiveMenu
import cz.cztvhub.core.archive.ArchiveMessages
import cz.cztvhub.core.archive.ArchiveRules
import cz.cztvhub.core.archive.ArchiveSort
import cz.cztvhub.core.archive.CardSource
import cz.cztvhub.core.archive.GridContent
import cz.cztvhub.core.archive.MenuItem
import cz.cztvhub.core.archive.MenuState
import cz.cztvhub.core.archive.SearchBack
import cz.cztvhub.tv.R
import cz.cztvhub.tv.tvApp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private val MENU_WIDTH = 260.dp
private const val GRID_COLUMNS = 3

/** Plays a request, with what "Pokračovat ve sledování" keeps with its entry. */
typealias PlayWithCard = (PlayRequest, ResumeCard?) -> Unit

/**
 * The Archiv tab (spec/apps.md "Archiv", T23 layout): a menu on the left (Hledat, Pokračovat ve sledování, Naše
 * nahrávky, the categories; a category's sub-menu "‹ Filmy", Všechny, its sub-genres) and on the right a grid of
 * what the selected item filters. The menu follows the core's [ArchiveMenu]; a show opens full screen over the tab
 * (Back returns to the same card) and stays under the player.
 */
@Composable
fun ArchiveScreen(viewModel: ArchiveViewModel, main: MainViewModel, onPlay: PlayWithCard) {
    val configured by viewModel.configured.collectAsStateWithLifecycle()
    if (!configured) {
        CenteredMessage(ArchiveMessages.NO_GATEWAY)
        return
    }
    // Entering the tab opens the menu afresh; back from the player only re-reads "Pokračovat".
    LaunchedEffect(Unit) { viewModel.entered() }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { viewModel.resumed() }

    val context = LocalContext.current
    val images = remember { ArchiveImages(context.tvApp.images) }
    val dvb by main.dvb.collectAsStateWithLifecycle()
    val thumbnailsOnTv by main.antennaThumbnails.collectAsStateWithLifecycle()
    val thumbnails = dvb?.thumbnails == true && thumbnailsOnTv
    val imageOf: (String?) -> ImageRequest? = remember(thumbnails) { { viewModel.image(it, thumbnails) } }

    val menu by viewModel.menu.collectAsStateWithLifecycle()
    val grid by viewModel.grid.collectAsStateWithLifecycle()
    val continueRow by viewModel.continueRow.collectAsStateWithLifecycle()
    val query by viewModel.query.collectAsStateWithLifecycle()
    val search by viewModel.search.collectAsStateWithLifecycle()
    val show by viewModel.show.collectAsStateWithLifecycle()
    val categoriesFailed by viewModel.categoriesFailed.collectAsStateWithLifecycle()
    var removing by remember { mutableStateOf<ContinueItem?>(null) }

    val state = menu ?: run {
        CenteredMessage(stringResource(R.string.loading))
        return
    }
    val requesters = remember { HashMap<String, FocusRequester>() }
    fun requester(level: Boolean, item: MenuItem) = requesters.getOrPut((if (level) "s:" else "m:") + item.key) { FocusRequester() }
    val selectedRequester = requester(state.isSubMenu, state.selected)
    val searchField = remember { FocusRequester() }
    var searchFocusTick by remember { mutableIntStateOf(0) }
    var menuHasFocus by remember { mutableStateOf(false) }
    var gridHasFocus by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val focusMenu: () -> Unit = { scope.launch { runCatching { selectedRequester.requestFocus() } } }

    // A sub-menu opened or closed: the focus goes to its selected item (Všechny, or the category back in the main
    // menu). Not on the first composition: the tab row keeps the focus while the viewer walks the tabs.
    var shownLevel by remember { mutableStateOf(state.category?.id) }
    LaunchedEffect(state.category?.id) {
        if (shownLevel != state.category?.id) {
            shownLevel = state.category?.id
            runCatching { selectedRequester.requestFocus() }
        }
    }

    // Back: a sub-menu returns to the main menu; the grid (after clearing a search) returns to the menu; the main
    // menu leaves the tab as elsewhere (not handled here).
    val inSearch = state.grid == GridContent.Search
    BackHandler(enabled = show == null && ((menuHasFocus && state.isSubMenu) || gridHasFocus)) {
        when {
            gridHasFocus && inSearch && ArchiveMenu.searchBack(query) == SearchBack.CLEAR -> viewModel.setQuery("")
            gridHasFocus -> focusMenu()
            else -> viewModel.back()
        }
    }

    Column(Modifier.fillMaxSize()) {
        Row(Modifier.weight(1f).fillMaxWidth()) {
            ArchiveMenuColumn(
                state = state,
                requester = { requester(state.isSubMenu, it) },
                selectedRequester = selectedRequester,
                onFocusInside = { menuHasFocus = it },
                onSettle = viewModel::settle,
                onOk = { item -> if (viewModel.ok(item)) searchFocusTick++ },
            )
            Spacer(Modifier.width(24.dp))
            Column(
                Modifier
                    .weight(1f)
                    .fillMaxHeight()
                    // Left from the grid's first column (a chip, the field) returns to the menu: see leftEdge.
                    .onFocusChanged { gridHasFocus = it.hasFocus },
            ) {
                // No category list: the menu has only its own items; said above the grid, retried on the next opening.
                if (categoriesFailed) Notice(ArchiveMessages.GENERIC)
                GridArea(
                    state = state,
                    grid = grid,
                    continueRow = continueRow,
                    query = query,
                    search = search,
                    searchField = searchField,
                    searchFocusTick = searchFocusTick,
                    images = images,
                    imageOf = imageOf,
                    viewModel = viewModel,
                    onPlay = onPlay,
                    onRemove = { removing = it },
                    onLeftEdge = focusMenu,
                )
            }
        }
        val shown = when (state.grid) {
            GridContent.Search -> search
            GridContent.Recordings, is GridContent.Category -> grid
            GridContent.Continue -> null
        }
        if (shown != null && ArchiveRules.showsTmdb(shown.cards)) TmdbFooter(shown.attribution)
    }

    show?.let { page ->
        ShowPageDialog(page, images, imageOf, viewModel, onPlay, onDismiss = viewModel::closeShow)
    }
    removing?.let { item ->
        RemoveDialog(item, onRemove = {
            viewModel.removeFromRow(item)
            removing = null
        }, onDismiss = { removing = null })
    }
}

/**
 * The menu: entering it (Down from the tabs, Left from the grid) lands on the selected item; the focus resting on
 * an item for [ArchiveMenu.SETTLE_MS] changes the grid, OK at once.
 */
@Composable
private fun ArchiveMenuColumn(
    state: MenuState,
    requester: (MenuItem) -> FocusRequester,
    selectedRequester: FocusRequester,
    onFocusInside: (Boolean) -> Unit,
    onSettle: (MenuItem) -> Boolean,
    onOk: (MenuItem) -> Unit,
) {
    val focusManager = LocalFocusManager.current
    var inside by remember { mutableStateOf(false) }
    var resting by remember { mutableStateOf<MenuItem?>(null) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(resting) {
        val item = resting ?: return@LaunchedEffect
        delay(ArchiveMenu.SETTLE_MS)
        onSettle(item)
    }
    Column(
        Modifier
            .width(MENU_WIDTH)
            .fillMaxHeight()
            .onFocusChanged {
                if (it.hasFocus && !inside) scope.launch { runCatching { selectedRequester.requestFocus() } }
                inside = it.hasFocus
                if (!it.hasFocus) resting = null
                onFocusInside(it.hasFocus)
            }
            .focusGroup()
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        state.items.forEach { item ->
            key(state.isSubMenu, item.key) {
                MenuEntry(
                    label = menuLabel(state, item),
                    selected = item == state.selected,
                    modifier = Modifier
                        .focusRequester(requester(item))
                        .onFocusChanged { if (it.isFocused) resting = item }
                        // Right before the 0.4 s: the focused item becomes the selection first, so the grid entered
                        // and "Left back to the selected item" agree.
                        .onPreviewKeyEvent {
                            if (it.key != Key.DirectionRight || it.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                            if (!onSettle(item)) return@onPreviewKeyEvent false
                            // The grid changes: enter it once it is composed, not the cards it replaces.
                            scope.launch {
                                withFrameNanos { }
                                withFrameNanos { }
                                focusManager.moveFocus(FocusDirection.Right)
                            }
                            true
                        },
                    onClick = { onOk(item) },
                )
            }
        }
    }
}

@Composable
private fun menuLabel(state: MenuState, item: MenuItem): String = when (item) {
    MenuItem.Search -> stringResource(R.string.archive_menu_search)
    MenuItem.Continue -> stringResource(R.string.archive_continue)
    MenuItem.Recordings -> stringResource(R.string.archive_recordings)
    is MenuItem.Category -> item.category.title
    MenuItem.Back -> state.title.orEmpty()
    MenuItem.All -> stringResource(R.string.archive_all)
    is MenuItem.Genre -> item.genre.title
}

@Composable
private fun MenuEntry(label: String, selected: Boolean, modifier: Modifier, onClick: () -> Unit) {
    Surface(
        onClick = onClick,
        modifier = modifier.fillMaxWidth(),
        shape = ClickableSurfaceDefaults.shape(RoundedCornerShape(8.dp)),
        scale = ClickableSurfaceDefaults.scale(focusedScale = 1.02f),
        colors = ClickableSurfaceDefaults.colors(
            containerColor = if (selected) Palette.accent else Palette.background,
            contentColor = Palette.text,
            focusedContainerColor = Palette.focused,
            focusedContentColor = Palette.onFocused,
        ),
    ) {
        Text(
            label,
            style = MaterialTheme.typography.titleMedium,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 8.dp),
        )
    }
}

/** The right side: what the selected menu item filters, with its sort chips or the search field on top. */
@Composable
private fun ColumnScope.GridArea(
    state: MenuState,
    grid: ArchiveGrid,
    continueRow: List<ContinueItem>,
    query: String,
    search: ArchiveGrid?,
    searchField: FocusRequester,
    searchFocusTick: Int,
    images: ArchiveImages,
    imageOf: (String?) -> ImageRequest?,
    viewModel: ArchiveViewModel,
    onPlay: PlayWithCard,
    onRemove: (ContinueItem) -> Unit,
    onLeftEdge: () -> Unit,
) {
    when (val content = state.grid) {
        GridContent.Continue -> {
            if (continueRow.isEmpty()) {
                CenteredMessage(stringResource(R.string.archive_continue_empty))
            } else {
                Grid(continueRow.size, key = { continueRow[it].key }, onLeftEdge = onLeftEdge) { index, modifier ->
                    ContinueCard(continueRow[index], images, imageOf, onPlay, onRemove, modifier)
                }
            }
        }
        GridContent.Search -> {
            SearchField(query, viewModel::setQuery, searchField, searchFocusTick, onLeftEdge)
            Spacer(Modifier.height(12.dp))
            search?.notice?.let { Notice(it) }
            val results = search
            if (results != null) {
                Box(Modifier.weight(1f).fillMaxWidth()) {
                    CardGrid(results, ArchiveMessages.NO_RESULTS, images, imageOf, viewModel::openShow, viewModel::moreSearch, viewModel::moreSearch, onLeftEdge)
                }
            }
        }
        GridContent.Recordings, is GridContent.Category -> {
            val recordings = content == GridContent.Recordings
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.focusGroup()) {
                state.sorts.forEachIndexed { index, sort ->
                    Chip(
                        sortLabel(sort, recordings),
                        selected = state.sort == sort,
                        modifier = if (index == 0) Modifier.leftEdge(onLeftEdge) else Modifier,
                    ) { viewModel.setSort(sort) }
                }
            }
            Spacer(Modifier.height(12.dp))
            grid.notice?.let { Notice(it) }
            Box(Modifier.weight(1f).fillMaxWidth()) {
                CardGrid(
                    grid,
                    if (recordings) stringResource(R.string.archive_recordings_empty) else ArchiveMessages.NO_RESULTS,
                    images, imageOf, viewModel::openShow, viewModel::moreGrid, viewModel::retry, onLeftEdge,
                )
            }
        }
    }
}

/** iVysílání did not answer: its text above the grid, our recordings are still listed. */
@Composable
private fun Notice(text: String) {
    Text(text, style = MaterialTheme.typography.bodyLarge, color = Palette.nowLine, modifier = Modifier.padding(bottom = 8.dp))
}

/** Left on the grid's left edge goes back to the menu's selected item. */
private fun Modifier.leftEdge(onLeftEdge: () -> Unit): Modifier = onPreviewKeyEvent {
    if (it.key != Key.DirectionLeft) return@onPreviewKeyEvent false
    if (it.type == KeyEventType.KeyDown) onLeftEdge()
    true
}

/** The 3-column grid; the first column's cards go back to the menu on Left. */
@Composable
private fun Grid(
    count: Int,
    key: (Int) -> Any,
    onLeftEdge: () -> Unit,
    onFocused: (Int) -> Unit = {},
    item: @Composable (Int, Modifier) -> Unit,
) {
    LazyVerticalGrid(
        columns = GridCells.Fixed(GRID_COLUMNS),
        horizontalArrangement = Arrangement.spacedBy(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
        contentPadding = PaddingValues(8.dp),
    ) {
        itemsIndexed(List(count) { it }, key = { _, index -> key(index) }) { _, index ->
            val modifier = Modifier
                .onFocusChanged { if (it.isFocused) onFocused(index) }
                .let { if (index % GRID_COLUMNS == 0) it.leftEdge(onLeftEdge) else it }
            item(index, modifier)
        }
    }
}

/** The search field: read-only until OK (walking past it never opens the keyboard), then the TV's keyboard. */
@Composable
private fun SearchField(
    value: String,
    onChange: (String) -> Unit,
    requester: FocusRequester,
    focusTick: Int,
    onLeftEdge: () -> Unit,
) {
    var focused by remember { mutableStateOf(false) }
    var editing by remember { mutableStateOf(false) }
    val focusManager = LocalFocusManager.current
    val keyboard = LocalSoftwareKeyboardController.current
    // OK on "Hledat" puts the focus into the field, ready to type.
    LaunchedEffect(focusTick) {
        if (focusTick == 0) return@LaunchedEffect
        if (runCatching { requester.requestFocus() }.isSuccess) {
            editing = true
            keyboard?.show()
        }
    }
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
            .focusRequester(requester)
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
                    Key.DirectionLeft -> {
                        if (editing) return@onPreviewKeyEvent false
                        if (event.type == KeyEventType.KeyDown) onLeftEdge()
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
        CardImage(images, remember(card.image, imageOf) { imageOf(card.image) }, card.source == CardSource.REC)
        Column(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 8.dp)) {
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
private fun CardImage(images: ArchiveImages, request: ImageRequest?, dvb: Boolean) {
    ArchiveImage(
        images, request,
        if (dvb) ChannelSource.DVB else ChannelSource.CT,
        Modifier.fillMaxWidth().aspectRatio(16f / 9f).clip(RoundedCornerShape(topStart = 10.dp, topEnd = 10.dp)),
    )
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
        modifier = modifier.fillMaxWidth(),
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

/**
 * A card of "Pokračovat ve sledování": OK plays at once from the saved position; long OK or the menu key offers
 * "Odebrat z řady".
 */
@Composable
private fun ContinueCard(
    item: ContinueItem,
    images: ArchiveImages,
    imageOf: (String?) -> ImageRequest?,
    onPlay: PlayWithCard,
    onRemove: (ContinueItem) -> Unit,
    modifier: Modifier,
) {
    val card = item.card
    CardSurface(
        onClick = { ArchiveRules.continueRequest(item)?.let { onPlay(it, card) } },
        onLongClick = { onRemove(item) },
        modifier = modifier.onPreviewKeyEvent {
            val menu = it.nativeKeyEvent.keyCode == AndroidKeyEvent.KEYCODE_MENU
            if (menu && it.type == KeyEventType.KeyUp) onRemove(item)
            menu
        },
    ) {
        CardImage(images, remember(card.image, imageOf) { imageOf(card.image) }, card.source == "dvb")
        Column(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 8.dp)) {
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

/**
 * A paged grid of cards: the next page when the focus nears the end ([ArchiveRules.PAGE_AHEAD]); [empty] when the
 * answer lists nothing, the gateway's text with "Zkusit znovu" ([onRetry]) when it failed.
 */
@Composable
private fun CardGrid(
    grid: ArchiveGrid,
    empty: String,
    images: ArchiveImages,
    imageOf: (String?) -> ImageRequest?,
    onOpen: (ArchiveCard) -> Unit,
    onMore: () -> Unit,
    onRetry: () -> Unit,
    onLeftEdge: () -> Unit,
) {
    when {
        grid.cards.isEmpty() && grid.loading -> CenteredMessage(stringResource(R.string.loading))
        grid.cards.isEmpty() && grid.error != null -> CenteredMessage(grid.error) {
            Button(onClick = onRetry, modifier = Modifier.leftEdge(onLeftEdge)) { Text(stringResource(R.string.retry)) }
        }
        grid.cards.isEmpty() -> CenteredMessage(empty)
        else -> Grid(
            grid.cards.size,
            key = { grid.cards[it].id },
            onLeftEdge = onLeftEdge,
            onFocused = { if (it >= grid.cards.size - ArchiveRules.PAGE_AHEAD) onMore() },
        ) { index, modifier ->
            val card = grid.cards[index]
            ShowCard(card, images, imageOf, modifier) { onOpen(card) }
        }
    }
}

@Composable
private fun sortLabel(sort: ArchiveSort, recordings: Boolean): String = stringResource(
    when (sort) {
        ArchiveSort.POPULAR -> R.string.archive_sort_popular
        ArchiveSort.NEWEST -> if (recordings) R.string.archive_sort_recordings_newest else R.string.archive_sort_newest
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
