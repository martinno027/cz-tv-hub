package cz.cztvhub.tv.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.gestures.BringIntoViewSpec
import androidx.compose.foundation.gestures.LocalBringIntoViewSpec
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusGroup
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Button
import androidx.tv.material3.ClickableSurfaceDefaults
import androidx.tv.material3.LocalContentColor
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Surface
import androidx.tv.material3.Text
import cz.cztvhub.core.Action
import cz.cztvhub.core.ChannelPrefs
import cz.cztvhub.core.Days
import cz.cztvhub.core.GuideLayout
import cz.cztvhub.core.Decisions
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.dvb.DvbAction
import cz.cztvhub.core.dvb.DvbChannel
import cz.cztvhub.core.dvb.DvbDecisions
import cz.cztvhub.core.dvb.DvbEvent
import cz.cztvhub.tv.OfficialAppLauncher
import cz.cztvhub.tv.R
import cz.cztvhub.tv.tvApp
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import java.time.Duration
import java.time.Instant
import java.time.LocalDate

private val DP_PER_MINUTE = 6.dp // 30 min = 180 dp, about two hours on screen
private val CHANNEL_COLUMN = 112.dp
private val HEADER_HEIGHT = 24.dp

/** Focus link between the detail panel's "Otevřít v iVysílání" button and the grid below it. */
private class AppButtonLink {
    val button = FocusRequester()
    var shown = false
    /** Cell to return to when leaving the button downwards. */
    var lastCell: FocusRequester = FocusRequester.Default
}

private class GridNav(var cursor: Instant, var live: Boolean) {
    var row = -1
    var vertical = false
}

/** A laid-out slot; [entry] is null for a "Mimo vysílání" filler. */
private class Cell(val slot: GuideLayout.Slot<Entry>, val x: Dp, val width: Dp, val startLabel: String) {
    val entry: Entry? get() = slot.item
    val requester = FocusRequester()
}

/** Focus-driven state read only by the parts that show it, so moving focus never recomposes the grid. */
@Stable
private class GuideSelection {
    var focused by mutableStateOf<Entry?>(null)
    var notice by mutableStateOf<String?>(null)
    var choice by mutableStateOf<Selection.Choice?>(null)
}

/** "TV průvodce": EPG grid with channels as rows and time on the horizontal axis. */
@Composable
fun GuideScreen(viewModel: MainViewModel, onPlay: (PlayRequest) -> Unit) {
    val day by viewModel.selectedDay.collectAsStateWithLifecycle()
    val plans by viewModel.plans.collectAsStateWithLifecycle()
    val now by viewModel.now.collectAsStateWithLifecycle()
    val selection = remember { GuideSelection() }
    val appButton = remember { AppButtonLink() }
    val currentOnPlay by rememberUpdatedState(onPlay)
    val onFocus: (Entry?) -> Unit = remember(selection) {
        { entry ->
            selection.focused = entry
            selection.notice = null
        }
    }
    val resume = LocalContext.current.tvApp.resume
    val onSelect: (Entry) -> Unit = remember(selection, resume) {
        { entry ->
            when (val action = entry.select(Instant.now(), resume::offer)) {
                is Selection.Notice -> selection.notice = action.text
                is Selection.Choice -> selection.choice = action
                is Selection.Play -> currentOnPlay(action.request)
            }
        }
    }
    val dvb by viewModel.dvb.collectAsStateWithLifecycle()
    val dvbPlans by viewModel.dvbPlans.collectAsStateWithLifecycle()
    val prefs by viewModel.channelPrefs.collectAsStateWithLifecycle()

    val context = LocalContext.current
    val onOpenApp: () -> Unit = remember(selection) {
        { OfficialAppLauncher.open(context)?.let { selection.notice = it } }
    }

    Column(Modifier.fillMaxSize()) {
        DayPicker(viewModel.today, day, onSelect = { selection.focused = null; viewModel.selectDay(it) })
        Spacer(Modifier.height(8.dp))
        DetailPanel(selection, now, appButton, onOpenApp)
        Spacer(Modifier.height(8.dp))
        when (val plan = plans[day]) {
            null, Load.Loading -> CenteredMessage(stringResource(R.string.loading))
            is Load.Failed -> CenteredMessage(plan.message) {
                Button(onClick = { viewModel.selectDay(day, force = true) }) { Text(stringResource(R.string.retry)) }
            }
            is Load.Ready -> {
                val dvbChannels = dvb?.channels.orEmpty()
                val dvbPlan = dvbPlans[day].orEmpty()
                val rows = remember(plan.value, dvbChannels, dvbPlan, prefs) {
                    guideRows(plan.value, ChannelOrder.visibleDvb(prefs, dvbChannels), dvbPlan, prefs)
                }
                if (rows.isEmpty() && (dvb == null || dvbChannels.isNotEmpty())) {
                    CenteredMessage(stringResource(R.string.channels_all_hidden))
                } else if (rows.all { it.entries.isEmpty() }) {
                    CenteredMessage(stringResource(R.string.empty_day))
                } else {
                    key(day) {
                        EpgGrid(rows = rows, now = now, appButton = appButton, onFocus = onFocus, onSelect = onSelect)
                    }
                }
            }
        }
    }

    selection.choice?.let { picked ->
        val play: (PlayRequest) -> () -> Unit = { request ->
            {
                selection.choice = null
                onPlay(request)
            }
        }
        PlayChoiceDialog(
            title = if (picked.live != null) {
                stringResource(R.string.choice_title, picked.entry.title)
            } else {
                picked.entry.title
            },
            onDismiss = { selection.choice = null },
            resumeLabel = picked.resumeLabel,
            onResume = picked.resume?.let(play),
            onFromStart = picked.fromStart?.let(play),
            onLive = picked.live?.let(play),
            // CT's official app only helps with CT's own programmes.
            onOpenApp = if (picked.entry is CtEntry) {
                {
                    selection.choice = null
                    onOpenApp()
                }
            } else null,
        )
    }
}

@Composable
private fun DayPicker(today: LocalDate, selected: LocalDate, onSelect: (LocalDate) -> Unit) {
    val selectedRequester = remember { FocusRequester() }
    var inside by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    Row(
        Modifier
            .horizontalScroll(rememberScrollState())
            // Entering the row always lands on the selected day.
            .onFocusChanged {
                if (it.hasFocus && !inside) scope.launch { runCatching { selectedRequester.requestFocus() } }
                inside = it.hasFocus
            }
            .focusGroup(),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Days.guideDays(today).forEach { day ->
            val isSelected = day == selected
            Surface(
                onClick = { onSelect(day) },
                modifier = if (isSelected) Modifier.focusRequester(selectedRequester) else Modifier,
                shape = ClickableSurfaceDefaults.shape(RoundedCornerShape(18.dp)),
                scale = ClickableSurfaceDefaults.scale(focusedScale = 1.05f),
                colors = ClickableSurfaceDefaults.colors(
                    containerColor = if (isSelected) Palette.accent else Palette.surface,
                    contentColor = Palette.text,
                    focusedContainerColor = Palette.focused,
                    focusedContentColor = Palette.onFocused,
                ),
            ) {
                Text(
                    Days.label(day, today),
                    style = MaterialTheme.typography.titleSmall,
                    maxLines = 1,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                )
            }
        }
    }
}

@Composable
private fun DetailPanel(selection: GuideSelection, now: Instant, appButton: AppButtonLink, onOpenApp: () -> Unit) {
    val focused = selection.focused
    val notice = selection.notice
    appButton.shown = focused is CtEntry && !now.isBefore(focused.start)
    Row(Modifier.fillMaxWidth().height(118.dp)) {
        if (focused == null) return@Row
        val p = focused
        RemoteImage(
            p.imageUrl,
            Modifier
                .width(210.dp)
                .fillMaxHeight()
                .background(Palette.surfaceDim, RoundedCornerShape(8.dp)),
        )
        Spacer(Modifier.width(16.dp))
        Column(Modifier.weight(1f)) {
            Text(p.title, style = MaterialTheme.typography.titleLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (p.subtitle.isNotBlank()) {
                Text(
                    p.subtitle,
                    style = MaterialTheme.typography.bodyMedium,
                    color = Palette.textDim,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            val meta = listOf(p.channelName, Days.timeRange(p.start, p.end), p.genre).filter { it.isNotBlank() }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(meta.joinToString("  ·  "), style = MaterialTheme.typography.bodyMedium)
                Spacer(Modifier.width(12.dp))
                Text(
                    notice ?: stateLabel(p, now),
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (notice != null) Palette.nowLine else Palette.accent,
                )
            }
            Spacer(Modifier.height(4.dp))
            Text(
                p.description,
                style = MaterialTheme.typography.bodySmall,
                color = Palette.textDim,
                maxLines = 3,
                overflow = TextOverflow.Ellipsis,
            )
        }
        // For programmes this app may not play (e.g. DRM-only), offer CT's official app.
        if (appButton.shown) {
            Spacer(Modifier.width(16.dp))
            Button(
                onClick = onOpenApp,
                modifier = Modifier
                    .focusRequester(appButton.button)
                    .focusProperties { down = appButton.lastCell },
            ) { Text(stringResource(R.string.open_ivysilani)) }
        }
    }
}

@Composable
private fun stateLabel(entry: Entry, now: Instant): String = when (entry) {
    is CtEntry -> when {
        entry.isAiring(now) -> stringResource(R.string.on_air_badge)
        else -> when (Decisions.decide(entry.programme, now).action) {
            Action.FUTURE -> stringResource(R.string.state_future)
            Action.VOD -> stringResource(R.string.state_vod)
            Action.STARTOVER -> stringResource(R.string.state_startover)
            Action.VOD_UNAVAILABLE -> stringResource(R.string.state_unavailable)
        }
    }
    is DvbEntry -> when (DvbDecisions.decide(entry.event, now)) {
        DvbAction.FUTURE -> stringResource(R.string.state_future)
        DvbAction.LIVE -> stringResource(R.string.state_live)
        DvbAction.LIVE_OR_STARTOVER -> stringResource(R.string.on_air_badge)
        DvbAction.RECORDING -> stringResource(R.string.state_recording)
        DvbAction.NOT_RECORDED -> stringResource(R.string.not_recorded)
    }
}

/**
 * CT internet channels first, then the "Anténa" channels (empty rows show as "Mimo vysílání"); only the channels
 * "Kanály" shows, in its order ([dvbChannels] already arranged).
 */
private fun guideRows(
    plan: DayPlan,
    dvbChannels: List<DvbChannel>,
    dvbPlan: Map<String, List<DvbEvent>>,
    prefs: ChannelPrefs,
): List<GuideRow> =
    ChannelOrder.visibleCt(prefs).map { ch -> GuideRow(ch.id, ch.name, plan[ch.id].orEmpty().map { CtEntry(ch, it) }) } +
        dvbChannels.map { ch -> GuideRow("dvb:${ch.id}", ch.name, dvbPlan[ch.id].orEmpty().map { DvbEntry(ch, it) }) }

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun EpgGrid(
    rows: List<GuideRow>,
    now: Instant,
    appButton: AppButtonLink,
    onFocus: (Entry?) -> Unit,
    onSelect: (Entry) -> Unit,
) {
    val plan = rows // remember() key
    val all = rows.flatMap { it.entries }
    val axisStart = remember(plan) { GuideLayout.axisStart(all) ?: Instant.now() }
    val axisEnd = remember(plan) { all.maxOfOrNull { it.end } ?: axisStart }
    val totalWidth = durationToDp(axisStart, axisEnd) + 8.dp
    // Open at "now" on the current day, at the same time of day on other days.
    val anchor = remember(plan) {
        val current = Instant.now()
        val sameTimeOfDay = Days.today(axisStart).atTime(current.atZone(Days.ZONE).toLocalTime())
            .atZone(Days.ZONE).toInstant()
        val candidate = if (current in axisStart..axisEnd) current else sameTimeOfDay
        if (candidate.isBefore(axisStart) || candidate.isAfter(axisEnd)) axisStart else candidate
    }
    val cellRows = remember(plan) {
        rows.map { row ->
            row to GuideLayout.row(row.entries, axisStart, axisEnd).map { slot ->
                Cell(
                    slot = slot,
                    x = durationToDp(axisStart, slot.start),
                    width = durationToDp(slot.start, slot.end),
                    startLabel = slot.item?.let { Days.time(it.start) }.orEmpty(),
                )
            }
        }
    }
    val anchorRequester = remember(plan) {
        val cells = cellRows.firstOrNull { it.second.isNotEmpty() }?.second.orEmpty()
        val cell = cellAt(cells, anchor) ?: cells.lastOrNull()
        cell?.requester ?: FocusRequester.Default
    }
    // Plain memory, not state (nothing renders from it): the focused cell and the time cursor
    // that up/down keep, so a column of the guide can be walked without drifting.
    // "Live column first" (spec/apps.md): on today's guide up/down walk the programmes airing now
    // until the first left/right; a new plan (guide opened, today reloaded) starts it again.
    val nav = remember(plan) { GridNav(cursor = anchor, live = Instant.now() in axisStart..axisEnd) }
    val scroll = rememberScrollState()
    val density = LocalDensity.current
    var entered by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(plan) {
        snapshotFlow { scroll.maxValue }.first { it in 1 until Int.MAX_VALUE }
        val x = with(density) { durationToDp(axisStart, anchor.minusSeconds(30 * 60)).toPx() }
        scroll.scrollTo(x.toInt().coerceAtLeast(0))
    }

    CompositionLocalProvider(LocalBringIntoViewSpec provides EpgBringIntoView) {
        BoxWithConstraints(Modifier.fillMaxSize()) {
            val rowHeight = ((maxHeight - HEADER_HEIGHT) / rows.size).coerceIn(44.dp, 64.dp)
            val viewportPx = with(density) { (maxWidth - CHANNEL_COLUMN).toPx() }
            // With "Anténa" rows the grid can be taller than the screen; focus scrolls it vertically.
            Row(Modifier.verticalScroll(rememberScrollState())) {
                Column(Modifier.width(CHANNEL_COLUMN)) {
                    Spacer(Modifier.height(HEADER_HEIGHT))
                    rows.forEach { row ->
                        Box(Modifier.height(rowHeight).fillMaxWidth(), contentAlignment = Alignment.CenterStart) {
                            Text(
                                row.name,
                                style = MaterialTheme.typography.titleMedium,
                                maxLines = 2,
                                overflow = TextOverflow.Ellipsis,
                                color = if (row.key.startsWith("dvb:")) Palette.accent else Color.Unspecified,
                            )
                        }
                    }
                }
                Box(
                    Modifier
                        .weight(1f)
                        .horizontalScroll(scroll)
                        // First entry lands on the programme at the anchor (now), later ones restore.
                        .onFocusChanged {
                            if (it.hasFocus && !entered) {
                                entered = true
                                scope.launch { runCatching { anchorRequester.requestFocus() } }
                            }
                        }
                        .focusRestorer(anchorRequester)
                        .focusGroup()
                        // Up/down keep the time position, like a classic EPG, instead of the
                        // geometric default that jumps to whatever cell is nearest on screen.
                        .onPreviewKeyEvent { event ->
                            val step = when (event.key) {
                                Key.DirectionUp -> -1
                                Key.DirectionDown -> 1
                                Key.DirectionLeft, Key.DirectionRight -> {
                                    if (event.type == KeyEventType.KeyDown) nav.live = false
                                    return@onPreviewKeyEvent false
                                }
                                else -> return@onPreviewKeyEvent false
                            }
                            if (event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                            if (nav.row < 0) return@onPreviewKeyEvent false
                            var next = nav.row + step
                            while (next in cellRows.indices && cellRows[next].second.isEmpty()) next += step
                            // Up from the top row: the detail panel's button if shown, else the day chips.
                            if (next < 0 && appButton.shown) {
                                return@onPreviewKeyEvent runCatching { appButton.button.requestFocus() }.isSuccess
                            }
                            val moment = if (nav.live) Instant.now() else nav.cursor
                            val target = cellRows.getOrNull(next)?.second?.let { cellAt(it, moment) }
                                ?: return@onPreviewKeyEvent false
                            nav.vertical = true
                            val moved = runCatching { target.requester.requestFocus() }.isSuccess
                            nav.vertical = false
                            moved
                        },
                ) {
                    Column(Modifier.width(totalWidth)) {
                        TimeHeader(axisStart, axisEnd, visibleStartPx = { scroll.value.toFloat() }, viewportPx = viewportPx)
                        cellRows.forEachIndexed { rowIndex, (_, cells) ->
                            Box(Modifier.height(rowHeight).fillMaxWidth()) {
                                cells.forEach { cell ->
                                    val p = cell.entry
                                    val x = cell.x
                                    val w = cell.width
                                    val modifier = Modifier
                                        .offset(x = x)
                                        .width(w)
                                        .fillMaxHeight()
                                        .padding(1.dp)
                                        .focusRequester(cell.requester)
                                    ProgrammeCell(
                                        p = p,
                                        startLabel = cell.startLabel,
                                        now = now,
                                        stickyShiftPx = {
                                            val maxShift = with(density) { (w - 48.dp).toPx() }.coerceAtLeast(0f)
                                            (scroll.value - with(density) { x.toPx() }).coerceIn(0f, maxShift)
                                        },
                                        modifier = modifier,
                                        onFocus = {
                                            nav.row = rowIndex
                                            appButton.lastCell = cell.requester
                                            if (!nav.vertical) {
                                                val visibleStart = axisStart.plusSeconds(
                                                    (with(density) { scroll.value.toDp() } / DP_PER_MINUTE * 60).toLong(),
                                                )
                                                nav.cursor = maxOf(cell.slot.start, visibleStart).plusSeconds(60)
                                            }
                                            onFocus(p)
                                        },
                                        onClick = { if (p != null) onSelect(p) },
                                    )
                                }
                            }
                        }
                    }
                    if (now in axisStart..axisEnd) {
                        // matchParentSize: the grid sits in a vertical scroll, so fillMaxHeight would be 0.
                        Box(Modifier.matchParentSize()) {
                            Box(
                                Modifier
                                    .offset(x = durationToDp(axisStart, now))
                                    .width(2.dp)
                                    .fillMaxHeight()
                                    .background(Palette.nowLine),
                            )
                        }
                    }
                }
            }
        }
    }
}

/** Half-hour labels; a label only shows when it fits the visible area entirely, never cut off. */
@Composable
private fun TimeHeader(axisStart: Instant, axisEnd: Instant, visibleStartPx: () -> Float, viewportPx: Float) {
    val density = LocalDensity.current
    Box(Modifier.height(HEADER_HEIGHT).fillMaxWidth()) {
        GuideLayout.ticks(axisStart, axisEnd).forEach { tick ->
            val x = durationToDp(axisStart, tick) + 4.dp
            Text(
                Days.time(tick),
                style = MaterialTheme.typography.labelLarge,
                color = Palette.textDim,
                maxLines = 1,
                modifier = Modifier
                    .offset(x = x)
                    .graphicsLayer {
                        val left = with(density) { x.toPx() } - visibleStartPx()
                        alpha = if (left >= 0f && left + size.width <= viewportPx) 1f else 0f
                    },
            )
        }
    }
}

@Composable
private fun ProgrammeCell(
    p: Entry?,
    startLabel: String,
    now: Instant,
    stickyShiftPx: () -> Float,
    modifier: Modifier,
    onFocus: () -> Unit,
    onClick: () -> Unit,
) {
    val look = p?.look(now)
    val container = when (look) {
        null -> Palette.background
        EntryLook.AIRING -> Palette.airing
        EntryLook.FUTURE, EntryLook.DIMMED -> Palette.surfaceDim
        EntryLook.PLAYABLE -> Palette.surface
    }
    val contentAlpha = when (look) {
        null -> 0.5f
        EntryLook.DIMMED -> 0.4f
        EntryLook.FUTURE -> 0.75f
        else -> 1f
    }
    // A plain clickable Box: hundreds of these are on screen, so skip Surface's animations.
    var isFocused by remember { mutableStateOf(false) }
    val shape = RoundedCornerShape(6.dp)
    Box(
        modifier
            .onFocusChanged {
                isFocused = it.isFocused
                if (it.isFocused) onFocus()
            }
            .clip(shape)
            .background(if (isFocused) Palette.focused else container, shape)
            .clickable(interactionSource = null, indication = null, onClick = onClick),
    ) {
        // Keep the title readable when the cell starts left of the visible area.
        CompositionLocalProvider(LocalContentColor provides if (isFocused) Palette.onFocused else Palette.text) {
            Column(
                Modifier
                    .fillMaxSize()
                    .offset { IntOffset(stickyShiftPx().toInt(), 0) }
                    .padding(horizontal = 8.dp, vertical = 3.dp)
                    .alpha(if (isFocused) 1f else contentAlpha),
                verticalArrangement = Arrangement.Center,
            ) {
                Text(
                    p?.title ?: stringResource(R.string.off_air),
                    style = MaterialTheme.typography.titleSmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (startLabel.isNotEmpty()) {
                    Text(startLabel, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Clip)
                }
            }
        }
    }
}

@Composable
private fun PlayChoiceDialog(
    title: String,
    onDismiss: () -> Unit,
    resumeLabel: String?,
    onResume: (() -> Unit)?,
    onFromStart: (() -> Unit)?,
    onLive: (() -> Unit)?,
    onOpenApp: (() -> Unit)?,
) {
    val first = remember { FocusRequester() }
    // The first offered option gets the focus: "Pokračovat od …", else "Od začátku", else "Živě".
    val firstOption = onResume ?: onFromStart ?: onLive
    fun focusFirst(option: () -> Unit) = if (option === firstOption) Modifier.focusRequester(first) else Modifier
    Dialog(onDismissRequest = onDismiss) {
        Column(
            Modifier
                .background(Palette.surface, RoundedCornerShape(16.dp))
                .padding(28.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(title, style = MaterialTheme.typography.titleLarge)
            Spacer(Modifier.height(20.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                if (onResume != null && resumeLabel != null) {
                    Button(onClick = onResume, modifier = focusFirst(onResume)) {
                        Text(stringResource(R.string.choice_resume, resumeLabel))
                    }
                }
                if (onFromStart != null) {
                    Button(onClick = onFromStart, modifier = focusFirst(onFromStart)) {
                        Text(stringResource(R.string.choice_from_start))
                    }
                }
                if (onLive != null) {
                    Button(onClick = onLive, modifier = focusFirst(onLive)) { Text(stringResource(R.string.choice_live)) }
                }
                if (onOpenApp != null) {
                    Button(onClick = onOpenApp) { Text(stringResource(R.string.open_ivysilani)) }
                }
            }
        }
    }
    LaunchedEffect(Unit) { runCatching { first.requestFocus() } }
}

/**
 * Scroll the guide only when a focused programme is barely visible. A long programme that already
 * fills a good part of the screen keeps the time axis where it is (so "now" stays in view).
 */
@OptIn(ExperimentalFoundationApi::class)
private object EpgBringIntoView : BringIntoViewSpec {
    override fun calculateScrollDistance(offset: Float, size: Float, containerSize: Float): Float {
        val trailing = offset + size
        val visible = minOf(trailing, containerSize) - maxOf(offset, 0f)
        if (visible >= minOf(size, containerSize * 0.4f) - 1f) return 0f
        return if (offset < 0f) maxOf(offset, trailing - containerSize) else minOf(trailing - containerSize, offset)
    }
}

/** The cell airing at [moment], else the next one, else the last one before it. */
private fun cellAt(cells: List<Cell>, moment: Instant): Cell? =
    GuideLayout.slotAt(cells.map { it.slot }, moment)?.let(cells::get)

private fun durationToDp(from: Instant, to: Instant): Dp =
    DP_PER_MINUTE * (Duration.between(from, to).seconds / 60f)
