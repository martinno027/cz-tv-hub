package cz.cztvhub.tv.ui

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.ClickableSurfaceDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Surface
import androidx.tv.material3.Text
import cz.cztvhub.core.ContinueItem
import cz.cztvhub.core.HomeCalendar
import cz.cztvhub.core.ImageRequest
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.tv.R
import cz.cztvhub.tv.tvApp
import kotlinx.coroutines.launch

private const val MORE_CHANNELS = "more"

/**
 * Domů (spec/apps.md "Domů", T58): the date with the name day and calendar lines, the gateway's warning,
 * "Pokračovat", "Teď běží", the live Chamber streams and the week's weather (T59). Nothing new is fetched for the
 * rows: they read what Živě and Archiv already have; only the weather comes from `/dvb/weather`, when Domů is shown
 * and the last answer is older than 30 min. A block with nothing to show is left out. Up/down between rows keeps the
 * column; the played card gets the focus back after the player.
 */
@Composable
fun HomeScreen(viewModel: MainViewModel, archive: ArchiveViewModel, onPlay: PlayWithCard, onMoreChannels: () -> Unit) {
    PspPolling(viewModel)
    ScheduleRetrying(viewModel, live = true)

    val cards = rememberLiveCards(viewModel)
    val prefs by viewModel.channelPrefs.collectAsStateWithLifecycle()
    val visible = ChannelOrder.visible(prefs, cards.antenna?.channels.orEmpty())
    val nowOn = HomeRows.nowOn(visible)
    val pspLive = HomeRows.pspLive(visible, cards.psp)
    val continueRow by archive.continueRow.collectAsStateWithLifecycle()
    val tailnetNotice by viewModel.tailnetNotice.collectAsStateWithLifecycle()
    val gatewayNotice = HomeRows.notice(cards.antenna, connecting = tailnetNotice != null)
    val header = HomeCalendar.header(cards.now)
    val weather by viewModel.weather.collectAsStateWithLifecycle()
    val weatherAsked by viewModel.weatherAsked.collectAsStateWithLifecycle()

    val context = LocalContext.current
    val gateway by context.tvApp.gateway.collectAsState()
    val images = remember { ArchiveImages(context.tvApp.images) }
    val thumbnailsOnTv by viewModel.antennaThumbnails.collectAsStateWithLifecycle()
    val thumbnails = cards.antenna?.thumbnails == true && thumbnailsOnTv
    val imageOf: (String?) -> ImageRequest? = remember(thumbnails) { { archive.image(it, thumbnails) } }
    var removing by remember { mutableStateOf<ContinueItem?>(null) }

    // The focusable rows as card keys, top to bottom; a row with nothing is left out.
    val rows = listOfNotNull(
        continueRow.map { "c:${it.key}" }.takeIf { it.isNotEmpty() },
        nowOn.takeIf { it.isNotEmpty() }?.let { row -> row.map { "n:${it.channel.key}" } + MORE_CHANNELS },
        pspLive.map { "p:${it.channel.key}" }.takeIf { it.isNotEmpty() },
    )
    val requesters = remember { HashMap<String, FocusRequester>() }
    fun requester(key: String) = requesters.getOrPut(key) { FocusRequester() }
    val scope = rememberCoroutineScope()
    val currentRows by rememberUpdatedState(rows)
    // After the rows recomposed: the card [key], else the first card.
    fun focus(key: String?) {
        scope.launch {
            withFrameNanos { }
            withFrameNanos { }
            val target = key?.let(requesters::get)
            if (target == null || runCatching { target.requestFocus() }.isFailure) {
                currentRows.firstOrNull()?.firstOrNull()?.let { first -> runCatching { requester(first).requestFocus() } }
            }
        }
    }

    // Domů shown (also when the gateway or its route changes): the weather when the last answer is old.
    LaunchedEffect(gateway) { viewModel.refreshWeather() }
    // The one-time location question (only with a gateway, the only place weather comes from).
    var consentClosed by remember { mutableStateOf(false) }
    val askConsent = gateway != null && !weatherAsked && !consentClosed && !viewModel.weatherQuestionPostponed
    val allow = rememberWeatherAllow(viewModel) { viewModel.refreshWeather(force = true) }

    // Back from the player (and every return to the app): the resume store again, the played card focused.
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) {
        archive.refreshContinueRow()
        viewModel.refreshWeather()
        viewModel.homePlayedCard?.let {
            viewModel.homePlayedCard = null
            focus(it)
        }
    }
    // App start: the first card of the first row (Pokračovat, else Teď běží).
    LaunchedEffect(Unit) {
        if (!viewModel.homeInitialFocusDone) {
            viewModel.homeInitialFocusDone = true
            focus(null)
        }
    }

    /** A card at [index] of row [row]: up/down to the same column of the neighbour row as far as it allows. */
    fun cardModifier(row: Int, index: Int): Modifier = Modifier
        .focusRequester(requester(rows[row][index]))
        .focusProperties {
            if (row > 0) up = requester(rows[row - 1].let { it[HomeRows.column(index, it.size)] })
            if (row < rows.lastIndex) down = requester(rows[row + 1].let { it[HomeRows.column(index, it.size)] })
        }
    fun rowOf(key: String) = rows.indexOfFirst { key in it }
    fun playing(key: String): (PlayRequest) -> Unit = { request ->
        viewModel.homePlayedCard = key
        onPlay(request, null)
    }

    val scroll = rememberScrollState()
    // "Počasí na týden" is no focus stop: the last focusable row scrolls the page to its end so the cards show.
    val lastRowFocus = Modifier.onFocusChanged { if (it.hasFocus) scope.launch { scroll.animateScrollTo(scroll.maxValue) } }
    fun rowModifier(row: Int): Modifier = if (row == rows.lastIndex) lastRowFocus else Modifier
    Column(
        Modifier.fillMaxSize().verticalScroll(scroll).padding(top = 8.dp, bottom = 16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Row(verticalAlignment = Alignment.Top) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(header.fullDate, style = MaterialTheme.typography.headlineMedium)
                header.nameLine?.let { Text(it, style = MaterialTheme.typography.titleMedium) }
                header.lines.forEach { Text(it, style = MaterialTheme.typography.bodyLarge, color = Palette.textDim) }
            }
            weather?.let {
                Spacer(Modifier.width(24.dp))
                WeatherNowBlock(it, cards.now)
            }
        }
        when (gatewayNotice) {
            // As on Živě: "Připojuji se k Tailscale…" (or the wait for a login), dimmed.
            GatewayNotice.CONNECTING -> tailnetNotice?.let { Text(it, style = MaterialTheme.typography.titleMedium, color = Palette.textDim) }
            GatewayNotice.UNAVAILABLE -> Text(stringResource(R.string.home_gateway_unavailable), style = MaterialTheme.typography.titleMedium, color = Palette.nowLine)
            GatewayNotice.KEY_REJECTED -> Text(stringResource(R.string.home_gateway_key_rejected), style = MaterialTheme.typography.titleMedium, color = Palette.nowLine)
            GatewayNotice.RECORDING_PAUSED -> Text(stringResource(R.string.home_recording_paused), style = MaterialTheme.typography.titleMedium, color = Palette.nowLine)
            null -> Unit
        }
        if (continueRow.isNotEmpty()) {
            val row = rowOf("c:${continueRow.first().key}")
            HomeRow(stringResource(R.string.home_continue), rowModifier(row)) {
                continueRow.forEachIndexed { index, item ->
                    val key = "c:${item.key}"
                    ContinueCard(
                        item, images, imageOf,
                        onPlay = { request, card ->
                            viewModel.homePlayedCard = key
                            onPlay(request, card)
                        },
                        onRemove = { removing = it },
                        modifier = cardModifier(row, index).width(300.dp),
                    )
                }
            }
        }
        if (nowOn.isNotEmpty()) {
            val row = rowOf(MORE_CHANNELS)
            HomeRow(stringResource(R.string.home_now), rowModifier(row)) {
                nowOn.forEachIndexed { index, (number, channel) ->
                    val key = "n:${channel.key}"
                    LiveChannelCard(channel, number, cards, viewModel, cardModifier(row, index).width(380.dp).height(200.dp), playing(key))
                }
                MoreChannelsCard(cardModifier(row, nowOn.size), onMoreChannels)
            }
        }
        if (pspLive.isNotEmpty()) {
            val row = rowOf("p:${pspLive.first().channel.key}")
            HomeRow(stringResource(R.string.home_psp), rowModifier(row)) {
                pspLive.forEachIndexed { index, (number, channel) ->
                    val key = "p:${channel.key}"
                    LiveChannelCard(channel, number, cards, viewModel, cardModifier(row, index).width(380.dp).height(200.dp), playing(key))
                }
            }
        }
        weather?.takeIf { it.days.isNotEmpty() }?.let { WeatherWeek(it, cards.now) }
    }

    if (askConsent) {
        WeatherConsentDialog(
            onAllow = {
                consentClosed = true
                allow()
                focus(null)
            },
            onDecline = {
                consentClosed = true
                viewModel.setWeatherByLocation(false)
                focus(null)
            },
            // Back: no answer; asked again at the next start.
            onDismiss = {
                viewModel.weatherQuestionPostponed = true
                consentClosed = true
                focus(null)
            },
        )
    }

    removing?.let { item ->
        RemoveDialog(item, onRemove = {
            // The focus goes to the neighbour card (the one after, else before), else the first card left.
            val keys = continueRow.map { "c:${it.key}" }
            val at = keys.indexOf("c:${item.key}")
            val next = keys.getOrNull(at + 1) ?: keys.getOrNull(at - 1)
            archive.removeFromRow(item)
            removing = null
            focus(next)
        }, onDismiss = { removing = null })
    }
}

@Composable
private fun HomeRow(title: String, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(title, style = MaterialTheme.typography.titleLarge, color = Palette.textDim)
        Row(
            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(20.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) { content() }
    }
}

/** "Další kanály" at the end of "Teď běží": opens Živě. */
@Composable
private fun MoreChannelsCard(modifier: Modifier, onClick: () -> Unit) {
    Surface(
        onClick = onClick,
        modifier = modifier.width(200.dp).height(200.dp),
        shape = ClickableSurfaceDefaults.shape(RoundedCornerShape(16.dp)),
        scale = ClickableSurfaceDefaults.scale(focusedScale = 1.04f),
        colors = ClickableSurfaceDefaults.colors(
            containerColor = Palette.surface,
            contentColor = Palette.text,
            focusedContainerColor = Palette.focused,
            focusedContentColor = Palette.onFocused,
        ),
    ) {
        Box(Modifier.fillMaxSize().padding(20.dp), contentAlignment = Alignment.Center) {
            Text(stringResource(R.string.home_more_channels), style = MaterialTheme.typography.titleLarge)
        }
    }
}
