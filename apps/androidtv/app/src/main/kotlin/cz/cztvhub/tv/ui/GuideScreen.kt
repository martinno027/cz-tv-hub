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
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
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
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Button
import androidx.tv.material3.ClickableSurfaceDefaults
import androidx.tv.material3.LocalContentColor
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Surface
import androidx.tv.material3.Text
import cz.cztvhub.core.Action
import cz.cztvhub.core.ChannelPrefs
import cz.cztvhub.core.Channels
import cz.cztvhub.core.CtAntennaMatch
import cz.cztvhub.core.Days
import cz.cztvhub.core.GuideLayout
import cz.cztvhub.core.GuideWindow
import cz.cztvhub.core.ImagePrefetch
import cz.cztvhub.core.ImageRequest
import cz.cztvhub.core.Decisions
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.PspState
import cz.cztvhub.core.dvb.DvbAction
import cz.cztvhub.core.dvb.DvbDecisions
import cz.cztvhub.core.dvb.DvbEvent
import cz.cztvhub.core.dvb.ProgrammeDetails
import cz.cztvhub.core.dvb.RecordingState
import cz.cztvhub.tv.OfficialAppLauncher
import cz.cztvhub.tv.R
import cz.cztvhub.tv.tvApp
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import java.time.Duration
import java.time.Instant
import java.time.LocalDate

/**
 * Poslanecká sněmovna states for the cells and the detail: read only where a stream is shown, so a state change
 * recomposes those cells and never rebuilds the grid.
 */
private val LocalPspStates = compositionLocalOf<Map<String, PspState>> { emptyMap() }

private fun Map<String, PspState>.of(entry: Entry?): PspState =
    (entry as? PspEntry)?.let { this[it.stream.id] } ?: PspState.UNKNOWN

private val DP_PER_MINUTE = 6.dp // 30 min = 180 dp, about two hours on screen
private val CHANNEL_COLUMN = 112.dp
private val HEADER_HEIGHT = 24.dp
/** Title, episode, meta and status lines and the description's lines (see DetailPanel). */
private val DETAIL_HEIGHT = 120.dp
private const val DESCRIPTION_LINES = 2

/**
 * Focus links of the day strip, the detail panel's "Otevřít v iVysílání" button and the grid (spec/apps.md). The
 * vertical order is grid ↔ day strip ↔ button: up from the grid's first row lands on the highlighted chip, up from
 * the strip on the button (when offered), down from the button on the strip, down from the strip in the grid (one
 * press, although the button sits between them on screen).
 */
private class AppButtonLink {
    val button = FocusRequester()
    /** The day strip's highlighted chip. */
    val chip = FocusRequester()
    var shown = false
    /** The grid's focused cell, where down from the strip returns. */
    var lastCell: FocusRequester = FocusRequester.Default
}

/** Where focus lands after scrolling past a day's edge: [rowKey]'s first cell, or its last when [atEnd]. */
private data class DayEdge(val rowKey: String, val atEnd: Boolean)

private class GridNav(var cursor: Instant, var live: Boolean) {
    var row = -1
    /** Index of the focused cell in its row. */
    var cell = -1
    /** The focused cell; always composed (the window follows it), so focus can return to it. */
    var last: Cell? = null
    var vertical = false
}

/**
 * A laid-out slot; [entry] is null for a "Mimo vysílání" filler. [fromMin]/[toMin] are minutes from the
 * axis start (for the composed [GuideWindow]).
 */
private class Cell(
    val slot: GuideLayout.Slot<Entry>,
    val x: Dp,
    val width: Dp,
    val startLabel: String,
    val fromMin: Double,
    val toMin: Double,
) {
    val entry: Entry? get() = slot.item
    val requester = FocusRequester()
}

/** Focus-driven state read only by the parts that show it, so moving focus never recomposes the grid. */
@Stable
private class GuideSelection {
    var focused by mutableStateOf<Entry?>(null)
    /** Start of the focused cell (a programme or "Mimo vysílání"): the day strip highlights its calendar day. */
    var focusedStart by mutableStateOf<Instant?>(null)
    /** The day strip's highlight while no cell is focused: the picked chip, "Dnes" on open. */
    var chip by mutableStateOf<LocalDate?>(null)
    var notice by mutableStateOf<String?>(null)
    /** The open programme page (T15) and the broadcast day of the grid it was opened from. */
    var page by mutableStateOf<OpenPage?>(null)
}

private data class OpenPage(val model: ProgrammePageModel, val day: LocalDate)

/** "TV průvodce": EPG grid with channels as rows and time on the horizontal axis. */
@Composable
fun GuideScreen(viewModel: MainViewModel, onPlay: (PlayRequest) -> Unit) {
    val day by viewModel.selectedDay.collectAsStateWithLifecycle()
    val plans by viewModel.plans.collectAsStateWithLifecycle()
    val now by viewModel.now.collectAsStateWithLifecycle()
    val selection = remember { GuideSelection() }
    val appButton = remember { AppButtonLink() }
    val currentOnPlay by rememberUpdatedState(onPlay)
    val onFocus: (Entry?, Instant) -> Unit = remember(selection) {
        { entry, start ->
            selection.focused = entry
            selection.focusedStart = entry?.start ?: start
            selection.notice = null
        }
    }
    val resume = LocalContext.current.tvApp.resume
    val currentDay by rememberUpdatedState(day)
    val onSelect: (Entry) -> Unit = remember(selection, resume) {
        val act: (Selection) -> Unit = { action ->
            when (action) {
                is Selection.Notice -> selection.notice = action.text
                is Selection.Play -> currentOnPlay(action.request)
            }
        }
        val select: (Entry) -> Unit = { entry ->
            val now = Instant.now()
            when {
                // Right before playback: a fresh enough answer, else one more check (spec/apps.md).
                entry is PspEntry && entry.isAiring(now) ->
                    viewModel.checkPsp(entry.stream.id) { state -> act(entry.select(Instant.now(), psp = state)) }
                entry is PspEntry -> act(entry.select(now))
                // Every programme opens its page (T15), also future and not recorded ones.
                else -> entry.page(now, resume::offer)?.let { selection.page = OpenPage(it, currentDay) }
            }
        }
        select
    }
    val dvb by viewModel.dvb.collectAsStateWithLifecycle()
    val dvbPlans by viewModel.dvbPlans.collectAsStateWithLifecycle()
    val prefs by viewModel.channelPrefs.collectAsStateWithLifecycle()
    val thumbnailsOnTv by viewModel.antennaThumbnails.collectAsStateWithLifecycle()
    val pspStates by viewModel.psp.collectAsStateWithLifecycle()
    // Tailscale is not Running yet or being checked: the Anténa rows come back once it answers (spec/apps.md T21, T18).
    val tailnetNotice by viewModel.tailnetNotice.collectAsStateWithLifecycle()
    val notice = tailnetNotice?.takeIf { prefs.isSourceOn(ChannelPrefs.SOURCE_DVB) }
    val connecting = notice != null
    PspPolling(viewModel)
    val today = Days.today(now)

    // "Across days" (spec/apps.md): left/right past a row's end opens the neighbouring day.
    var edge by remember { mutableStateOf<DayEdge?>(null) }
    // Where a picked chip opens its grid; null = now (as on every open of the guide).
    var anchorAt by remember { mutableStateOf<Instant?>(null) }
    // Picking a chip rebuilds the grid even when the day stays (e.g. "Dnes" from the grid containing now).
    var generation by remember { mutableIntStateOf(0) }
    // After picking a chip, focus moves into the new grid onto its target cell (once).
    var focusPicked by remember { mutableStateOf(false) }
    val onEdge: (String, Boolean) -> Unit = { rowKey, forward ->
        Days.adjacentGuideDay(day, today, forward)?.let { next ->
            edge = DayEdge(rowKey, atEnd = !forward)
            anchorAt = null
            selection.focused = null
            selection.focusedStart = null
            selection.chip = next
            viewModel.selectDay(next)
        }
    }
    val onChip: (LocalDate) -> Unit = { chip ->
        val target = Days.select(chip, Instant.now())
        edge = null
        anchorAt = target.anchor
        generation++
        focusPicked = true
        selection.focused = null
        selection.focusedStart = null
        selection.chip = chip
        viewModel.selectDay(target.day)
    }

    val context = LocalContext.current
    val onOpenApp: () -> Unit = remember(selection) {
        { OfficialAppLauncher.open(context)?.let { selection.notice = it } }
    }

    // "ČT images for Anténa ČT channels": the day's CT schedule by slug.
    val ctBySlug = remember(plans[day]) {
        val ready = plans[day] as? Load.Ready
        ready?.value?.let { plan -> Channels.all.associate { it.slug to plan[it.id].orEmpty() } }.orEmpty()
    }
    // Detail image priority: CT's image (ČT channels on the antenna too) → gateway thumbnail, when the gateway
    // (dvb) and this TV allow it → none (the antenna placeholder).
    val imageOf: (Entry) -> ImageRequest? = remember(dvb?.thumbnails, thumbnailsOnTv, ctBySlug) {
        { entry ->
            when (entry) {
                is CtEntry -> entry.imageUrl.takeIf { it.startsWith("https://") }?.let { ImageRequest(it) }
                is DvbEntry -> CtAntennaMatch.programme(entry.channel.name, entry.title, entry.start, entry.end, ctBySlug)
                    ?.imageUrl?.takeIf { it.startsWith("https://") }?.let { ImageRequest(it) }
                    ?: viewModel.thumbnail(entry.event)?.let { (url, headers) ->
                        ImageRequest(url, headers, gateway = true,
                            stillRecording = entry.event.recording?.state == RecordingState.RECORDING)
                    }
                is PspEntry -> null // no thumbnails for the streams
            }
        }
    }
    val scope = rememberCoroutineScope()
    val images = remember { GuideImages(context.tvApp.images, scope) }

    CompositionLocalProvider(LocalPspStates provides pspStates) {
    Column(Modifier.fillMaxSize()) {
        DayPicker(today, day, selection, appButton, onSelect = onChip)
        Spacer(Modifier.height(8.dp))
        DetailPanel(selection, now, appButton, onOpenApp, images, imageOf)
        Spacer(Modifier.height(8.dp))
        if (notice != null) Text(notice, color = Palette.textDim)
        when (val plan = plans[day]) {
            null, Load.Loading -> CenteredMessage(stringResource(R.string.loading))
            is Load.Failed -> CenteredMessage(plan.message) {
                Button(onClick = { viewModel.selectDay(day, force = true) }) { Text(stringResource(R.string.retry)) }
            }
            is Load.Ready -> {
                val dvbChannels = dvb?.channels.orEmpty()
                val dvbPlan = dvbPlans[day].orEmpty()
                val rows = remember(plan.value, dvbChannels, dvbPlan, prefs) {
                    guideRows(plan.value, ChannelOrder.visible(prefs, dvbChannels), dvbPlan, day)
                }
                // Anténa on but its channels not loaded yet: not "everything hidden".
                val antennaPending = connecting || dvb != null && prefs.isSourceOn(ChannelPrefs.SOURCE_DVB) &&
                    dvbChannels.isEmpty() && dvb?.error == null
                if (rows.isEmpty() && !antennaPending) {
                    CenteredMessage(stringResource(R.string.channels_all_hidden))
                } else if (rows.all { it.entries.isEmpty() }) {
                    CenteredMessage(stringResource(R.string.empty_day))
                } else {
                    // "Image prefetch": a new window when the focus moved more than 1 h or 3 rows from the last one.
                    val onFocusAt: (Int, Instant) -> Unit = remember(rows, imageOf) {
                        var last: ImagePrefetch.Point? = null
                        { row, time ->
                            val point = ImagePrefetch.Point(row, time)
                            if (ImagePrefetch.moved(last, point)) {
                                last = point
                                val entries = rows.map { it.entries }
                                images.prefetch(
                                    ImagePrefetch.window(entries, point).mapNotNull { imageOf(entries[it.row][it.index]) },
                                )
                            }
                        }
                    }
                    key(day, generation) {
                        EpgGrid(
                            rows = rows, day = day, now = now, appButton = appButton, onFocus = onFocus, onSelect = onSelect,
                            anchorAt = anchorAt, startAt = edge, onStarted = { edge = null }, onEdge = onEdge,
                            onFocusAt = onFocusAt, focusOnOpen = focusPicked, onFocusedOnOpen = { focusPicked = false },
                        )
                    }
                }
            }
        }
    }
    }

    selection.page?.let { open ->
        val entry = open.model.entry
        // Back from the player: buttons and "Pokračovat" again from what it saved (on its pause, before this).
        LifecycleEventEffect(Lifecycle.Event.ON_RESUME) {
            selection.page?.let { current ->
                current.model.entry.page(Instant.now(), resume::offer)?.let { selection.page = current.copy(model = it) }
            }
        }
        val details by produceState<ProgrammeDetails?>(null, entry, open.day) { value = viewModel.details(entry, open.day) }
        val poster = remember(details) { details?.match?.poster?.let(viewModel::poster) }
        ProgrammePageDialog(
            page = open.model,
            now = now,
            details = details,
            images = images,
            guideImage = remember(entry, imageOf) { imageOf(entry) },
            poster = poster,
            notice = selection.notice,
            // The page stays under the player (spec/apps.md).
            onPlay = { request ->
                selection.notice = null
                onPlay(request)
            },
            onOpenApp = onOpenApp,
            onDismiss = {
                selection.page = null
                selection.notice = null
            },
        )
    }
}

/**
 * The day strip. It highlights the calendar day of the focused programme's start (`chipDay`; reading the focus here
 * keeps the grid out of it), and entering it from below lands on that chip.
 */
@Composable
private fun DayPicker(
    today: LocalDate,
    gridDay: LocalDate,
    selection: GuideSelection,
    appButton: AppButtonLink,
    onSelect: (LocalDate) -> Unit,
) {
    val days = Days.guideDays(today)
    val selected = selection.focusedStart?.let { Days.highlightedChip(it, gridDay, today) } ?: selection.chip ?: today
    val selectedRequester = appButton.chip
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
        days.forEach { day ->
            val isSelected = day == selected
            Surface(
                onClick = { onSelect(day) },
                modifier = (if (isSelected) Modifier.focusRequester(selectedRequester) else Modifier)
                    // Up from the strip: the detail panel's button when it is offered; down: the grid, past the button.
                    .focusProperties {
                        up = if (appButton.shown) appButton.button else FocusRequester.Default
                        down = appButton.lastCell
                    },
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

/**
 * The focused programme's detail. Fixed layout (spec/apps.md): constant height, and every element keeps its place
 * whatever the programme, so nothing moves while the viewer clicks through cells. The episode and status lines and
 * the description's lines are reserved even when empty; the "Otevřít v iVysílání" button keeps its place top right
 * (invisible and unfocusable when not offered).
 */
@Composable
private fun DetailPanel(
    selection: GuideSelection,
    now: Instant,
    appButton: AppButtonLink,
    onOpenApp: () -> Unit,
    images: GuideImages,
    imageOf: (Entry) -> ImageRequest?,
) {
    val focused = selection.focused
    val notice = selection.notice
    appButton.shown = focused is CtEntry && !now.isBefore(focused.start)
    Row(Modifier.fillMaxWidth().height(DETAIL_HEIGHT)) {
        val p = focused
        val imageModifier = Modifier
            .width(DETAIL_HEIGHT * 16 / 9)
            .fillMaxHeight()
            .clip(RoundedCornerShape(8.dp))
        if (p != null) {
            val placeholder = when (p) {
                is DvbEntry -> ChannelSource.DVB
                is PspEntry -> ChannelSource.PSP
                else -> null
            }
            GuideImage(images, remember(p, imageOf) { imageOf(p) }, placeholder, modifier = imageModifier)
        } else {
            Spacer(imageModifier)
        }
        Spacer(Modifier.width(16.dp))
        Column(Modifier.weight(1f)) {
            val line = Modifier.fillMaxWidth()
            Text(
                p?.title.orEmpty(),
                style = MaterialTheme.typography.titleLarge,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = line,
            )
            Text(
                p?.subtitle.orEmpty(),
                style = MaterialTheme.typography.bodyMedium,
                color = Palette.textDim,
                minLines = 1,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = line,
            )
            // "Today follows the clock": a programme airing at 0:10 says "Dnes 0:10" once midnight has passed.
            // A Poslanecká sněmovna stream has no programme data: only where it comes from.
            val pspSource = stringResource(R.string.psp_source_line)
            val meta = p?.let {
                if (it is PspEntry) return@let listOf(pspSource)
                val day = Days.label(Days.chipDay(it.start), Days.today(now))
                listOf(it.channelName, "$day ${Days.timeRange(it.start, it.end)}", it.genre).filter(String::isNotBlank)
            }.orEmpty()
            val pspState = LocalPspStates.current.of(p)
            Text(
                meta.joinToString("  ·  "),
                style = MaterialTheme.typography.bodyMedium,
                minLines = 1,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = line,
            )
            Text(
                notice ?: p?.let { stateLabel(it, now, pspState) }.orEmpty(),
                style = MaterialTheme.typography.bodyMedium,
                color = if (notice != null) Palette.nowLine else Palette.accent,
                minLines = 1,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = line,
            )
            Text(
                p?.description.orEmpty(),
                style = MaterialTheme.typography.bodySmall,
                color = Palette.textDim,
                minLines = DESCRIPTION_LINES,
                maxLines = DESCRIPTION_LINES,
                overflow = TextOverflow.Ellipsis,
                modifier = line,
            )
        }
        // For programmes this app may not play (e.g. DRM-only), offer CT's official app.
        Spacer(Modifier.width(16.dp))
        Button(
            onClick = { if (appButton.shown) onOpenApp() },
            modifier = Modifier
                .alpha(if (appButton.shown) 1f else 0f)
                .focusRequester(appButton.button)
                .focusProperties {
                    canFocus = appButton.shown
                    down = appButton.chip
                },
        ) { Text(stringResource(R.string.open_ivysilani)) }
    }
}

@Composable
internal fun stateLabel(entry: Entry, now: Instant, psp: PspState): String = when (entry) {
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
    is PspEntry -> entry.cellTitle(now, psp)
}

/**
 * One row per channel "Kanály" shows, CT internet, Anténa and Poslanecká sněmovna mixed in its order ([channels]
 * already arranged); empty rows show as "Mimo vysílání", a stream's row is one cell over the whole [day].
 */
private fun guideRows(
    plan: DayPlan,
    channels: List<ListedChannel>,
    dvbPlan: Map<String, List<DvbEvent>>,
    day: LocalDate,
): List<GuideRow> {
    val (axisStart, axisEnd) = GuideLayout.axis(day)
    return channels.mapNotNull { listed ->
        val ct = listed.ct
        val ch = listed.dvb
        val stream = listed.psp
        when {
            ct != null -> GuideRow(ct.id, ct.name, plan[ct.id].orEmpty().map { CtEntry(ct, it) })
            ch != null -> GuideRow("dvb:${ch.id}", ch.name, dvbPlan[ch.id].orEmpty().map { DvbEntry(ch, it) })
            stream != null -> GuideRow(stream.key, stream.name, listOf(PspEntry(stream, axisStart, axisEnd)))
            else -> null
        }
    }
}

@OptIn(ExperimentalFoundationApi::class, ExperimentalComposeUiApi::class)
@Composable
private fun EpgGrid(
    rows: List<GuideRow>,
    day: LocalDate,
    now: Instant,
    appButton: AppButtonLink,
    onFocus: (entry: Entry?, start: Instant) -> Unit,
    onSelect: (Entry) -> Unit,
    anchorAt: Instant? = null,
    startAt: DayEdge? = null,
    onStarted: () -> Unit = {},
    onEdge: (rowKey: String, forward: Boolean) -> Unit = { _, _ -> },
    onFocusAt: (row: Int, time: Instant) -> Unit = { _, _ -> },
    focusOnOpen: Boolean = false,
    onFocusedOnOpen: () -> Unit = {},
) {
    val focusAt by rememberUpdatedState(onFocusAt)
    val plan = rows // remember() key
    // Opened by scrolling past the previous/next day's edge: start on that row's first/last cell.
    val edgeStart = remember(plan) { startAt }
    // Exactly the broadcast day 05:00–05:00 Prague; programmes crossing 05:00 are clipped.
    val axis = remember(day) { GuideLayout.axis(day) }
    val axisStart = axis.first
    val axisEnd = axis.second
    val totalWidth = durationToDp(axisStart, axisEnd) + 8.dp
    // Open at the picked chip's moment (`Days.select`), else at "now" on the grid containing it (every open of the
    // guide), else at the same time of day.
    val anchor = remember(plan) {
        val current = Instant.now()
        val sameTimeOfDay = day.atTime(current.atZone(Days.ZONE).toLocalTime())
            .atZone(Days.ZONE).toInstant()
        val candidate = anchorAt?.takeIf { it in axisStart..axisEnd }
            ?: if (current in axisStart..axisEnd) current else sameTimeOfDay
        if (candidate.isBefore(axisStart) || candidate.isAfter(axisEnd)) axisStart else candidate
    }
    val cellRows = remember(plan) {
        rows.map { row ->
            row to GuideLayout.row(row.entries, axisStart, axisEnd).map { slot ->
                Cell(
                    slot = slot,
                    x = durationToDp(axisStart, slot.start),
                    width = durationToDp(slot.start, slot.end),
                    startLabel = slot.item?.takeIf { it !is PspEntry }?.let { Days.time(it.start) }.orEmpty(),
                    fromMin = minutesBetween(axisStart, slot.start),
                    toMin = minutesBetween(axisStart, slot.end),
                )
            }
        }
    }
    val axisEndMin = minutesBetween(axisStart, axisEnd)
    val anchorRow = remember(plan) {
        val edgeRow = edgeStart?.let { e -> cellRows.indexOfFirst { it.first.key == e.rowKey && it.second.isNotEmpty() } }
        edgeRow?.takeIf { it >= 0 } ?: cellRows.indexOfFirst { it.second.isNotEmpty() }.coerceAtLeast(0)
    }
    val anchorCell = remember(plan) {
        val cells = cellRows.getOrNull(anchorRow)?.second.orEmpty()
        when {
            edgeStart == null -> cellAt(cells, anchor) ?: cells.lastOrNull()
            edgeStart.atEnd -> cells.lastOrNull()
            else -> cells.firstOrNull()
        }
    }
    // The time the grid opens at (scroll position, up/down cursor).
    val opensAt = remember(plan) { if (edgeStart != null) anchorCell?.slot?.start ?: anchor else anchor }
    val anchorRequester = anchorCell?.requester ?: FocusRequester.Default
    // Only the cells in this window are composed (spec/apps.md "Performance"); it follows focus.
    var window by remember(plan) {
        val at = anchorCell?.fromMin ?: minutesBetween(axisStart, anchor)
        mutableStateOf(GuideWindow.around(anchorRow, at, anchorCell?.toMin ?: at, rows.size))
    }
    // Plain memory, not state (nothing renders from it): the focused cell and the time cursor
    // that up/down keep, so a column of the guide can be walked without drifting.
    // "Live column first" (spec/apps.md): on the grid containing now (the broadcast day, so after midnight the
    // previous date's grid) up/down walk the programmes airing now from the open at now until the first left/right
    // (a new plan, i.e. guide opened or the day reloaded, starts it again), and always from a programme airing now.
    val nav = remember(plan) {
        // A day change, or a chip opening the grid at 20:00, starts without it.
        GridNav(cursor = opensAt, live = edgeStart == null && anchorAt == null && Instant.now() in axisStart..axisEnd)
    }
    val scroll = rememberScrollState()
    val density = LocalDensity.current
    var entered by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    /**
     * Moves focus to [cell] of [rowIndex]. A cell outside the window is composed first (the window moves),
     * then focused a frame later. Every move in the grid goes through here, so focus never lands on
     * whatever composed cell is geometrically nearest in another row.
     */
    fun focusCell(rowIndex: Int, cell: Cell, vertical: Boolean): Boolean {
        fun request(): Boolean {
            nav.vertical = vertical
            val moved = runCatching { cell.requester.requestFocus() }.isSuccess
            nav.vertical = false
            return moved
        }
        if (window.contains(rowIndex, cell.fromMin, cell.toMin) && request()) return true
        window = GuideWindow.around(rowIndex, cell.fromMin, cell.toMin, rows.size)
        scope.launch {
            repeat(3) {
                withFrameNanos { }
                if (request()) return@launch
            }
        }
        return true
    }

    LaunchedEffect(plan) {
        // A rebuilt grid has new cells: down from the day strip lands on its anchor until a cell is focused.
        if (nav.last == null) appButton.lastCell = anchorRequester
        // "Image prefetch": the window around where the grid opens, before focus enters it.
        if (anchorRow < cellRows.size) focusAt(anchorRow, opensAt)
        snapshotFlow { scroll.maxValue }.first { it in 1 until Int.MAX_VALUE }
        val x = with(density) { durationToDp(axisStart, opensAt.minusSeconds(30 * 60)).toPx() }
        scroll.scrollTo(x.toInt().coerceAtLeast(0))
        if (edgeStart != null || focusOnOpen) {
            // The previous day's grid (and its focus) is gone, or a chip was picked: focus the target cell.
            entered = true
            for (attempt in 1..5) {
                withFrameNanos { }
                if (runCatching { anchorRequester.requestFocus() }.isSuccess) {
                    if (focusOnOpen) onFocusedOnOpen()
                    break
                }
            }
        }
    }

    CompositionLocalProvider(LocalBringIntoViewSpec provides EpgBringIntoView) {
        BoxWithConstraints(Modifier.fillMaxSize()) {
            val rowHeight = ((maxHeight - HEADER_HEIGHT) / rows.size).coerceIn(44.dp, 64.dp)
            val viewportPx = with(density) { (maxWidth - CHANNEL_COLUMN).toPx() }
            // With "Anténa" rows the grid can be taller than the screen; focus scrolls it vertically.
            Row(Modifier.verticalScroll(rememberScrollState())) {
                Column(Modifier.width(CHANNEL_COLUMN)) {
                    Spacer(Modifier.height(HEADER_HEIGHT))
                    rows.forEachIndexed { index, row ->
                        Row(
                            Modifier.height(rowHeight).fillMaxWidth(),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            // Rows are the visible channels in order: the row index is the channel number.
                            ChannelNumber(index + 1, style = MaterialTheme.typography.bodyMedium)
                            SourceIcon(ChannelSource.ofKey(row.key), size = 18.dp)
                            Text(
                                row.name,
                                style = MaterialTheme.typography.titleMedium,
                                maxLines = 2,
                                overflow = TextOverflow.Ellipsis,
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
                        // Back into the grid: the cell focused last (always composed), else the anchor.
                        .focusRestorer { nav.last?.requester ?: anchorRequester }
                        .focusGroup()
                        // Up/down keep the time position, like a classic EPG, instead of the
                        // geometric default that jumps to whatever cell is nearest on screen.
                        // Left/right go to the neighbour in the same row (it may not be composed yet).
                        .onPreviewKeyEvent { event ->
                            // The edge start is used up by the first key (until then a late Anténa
                            // plan for the day, which rebuilds the grid, still opens at the edge).
                            if (edgeStart != null && event.type == KeyEventType.KeyDown) onStarted()
                            val step = when (event.key) {
                                Key.DirectionUp -> -1
                                Key.DirectionDown -> 1
                                Key.DirectionLeft, Key.DirectionRight -> {
                                    if (event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                                    nav.live = false
                                    val cells = cellRows.getOrNull(nav.row)?.second ?: return@onPreviewKeyEvent false
                                    val forward = event.key == Key.DirectionRight
                                    val target = cells.getOrNull(nav.cell + if (forward) 1 else -1)
                                    // Past either end of the row: the neighbouring day ("Across days"),
                                    // never another row; at the range's ends nothing happens.
                                    if (target == null) {
                                        cellRows.getOrNull(nav.row)?.first?.key?.let { onEdge(it, forward) }
                                        return@onPreviewKeyEvent true
                                    }
                                    return@onPreviewKeyEvent focusCell(nav.row, target, vertical = false)
                                }
                                else -> return@onPreviewKeyEvent false
                            }
                            if (event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                            if (nav.row < 0) return@onPreviewKeyEvent false
                            var next = nav.row + step
                            while (next in cellRows.indices && cellRows[next].second.isEmpty()) next += step
                            // Up from the top row: the highlighted day chip, never the detail panel's button first.
                            if (next < 0) {
                                return@onPreviewKeyEvent runCatching { appButton.chip.requestFocus() }.isSuccess
                            }
                            // From a programme airing now: the next row's airing programme too.
                            val moment = GuideLayout.verticalMoment(nav.live, nav.last?.slot, Instant.now(), nav.cursor)
                            val target = cellRows.getOrNull(next)?.second?.let { cellAt(it, moment) }
                                ?: return@onPreviewKeyEvent false
                            focusCell(next, target, vertical = true)
                        },
                ) {
                    Column(Modifier.width(totalWidth)) {
                        TimeHeader(axisStart, axisEnd, visibleStartPx = { scroll.value.toFloat() }, viewportPx = viewportPx)
                        val shown = window
                        cellRows.forEachIndexed { rowIndex, (_, cells) ->
                            Box(Modifier.height(rowHeight).fillMaxWidth()) {
                                if (rowIndex in shown.rows) cells.forEachIndexed { cellIndex, cell ->
                                    if (shown.overlaps(cell.fromMin, cell.toMin)) key(cell) {
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
                                            nav.cell = cellIndex
                                            nav.last = cell
                                            window = GuideWindow.follow(
                                                window, rowIndex, cell.fromMin, cell.toMin, rows.size, axisEndMin,
                                            )
                                            appButton.lastCell = cell.requester
                                            if (!nav.vertical) {
                                                val visibleStart = axisStart.plusSeconds(
                                                    (with(density) { scroll.value.toDp() } / DP_PER_MINUTE * 60).toLong(),
                                                )
                                                nav.cursor = maxOf(cell.slot.start, visibleStart).plusSeconds(60)
                                            }
                                            onFocus(p, cell.slot.start)
                                            val at = nav.cursor
                                            focusAt(rowIndex, if (at in cell.slot.start..cell.slot.end) at else cell.slot.start)
                                        },
                                        onClick = { if (p != null) onSelect(p) },
                                    )
                                    }
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
    val psp = if (p is PspEntry) LocalPspStates.current.of(p) else PspState.UNKNOWN
    val look = p?.look(now, psp)
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
                    when (p) {
                        null -> stringResource(R.string.off_air)
                        is PspEntry -> p.cellTitle(now, psp)
                        else -> p.title
                    },
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

private fun minutesBetween(from: Instant, to: Instant): Double = Duration.between(from, to).seconds / 60.0

private fun durationToDp(from: Instant, to: Instant): Dp =
    DP_PER_MINUTE * (Duration.between(from, to).seconds / 60f)
