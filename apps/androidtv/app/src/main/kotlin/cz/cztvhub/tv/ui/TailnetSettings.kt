package cz.cztvhub.tv.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Button
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import cz.cztvhub.tv.R
import cz.cztvhub.bridge.tailnet.Tailnet
import cz.cztvhub.tv.tvApp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Nastavení → Server, below the key (spec/apps.md "Tailscale v aplikaci"): the switch, the status line and, as the
 * state asks, the login QR code, "Přihlásit klíčem" and "Odhlásit z Tailscale". The auth key lives only in this
 * screen's memory and is cleared once used.
 */
@Composable
internal fun TailnetSettings() {
    val tailnet = LocalContext.current.tvApp.tailnet
    DisposableEffect(tailnet) {
        tailnet.settingsOpen = true
        onDispose { tailnet.settingsOpen = false }
    }
    val state by tailnet.state.collectAsState()
    val status = state.status
    var authKey by remember { mutableStateOf("") }

    Button(onClick = { tailnet.setEnabled(!state.enabled) }, modifier = Modifier.padding(top = 8.dp)) {
        Text((if (state.enabled) "☑  " else "☐  ") + stringResource(R.string.tailnet_switch))
    }
    Text(stringResource(R.string.tailnet_hint), style = MaterialTheme.typography.bodySmall, color = Palette.textDim)
    if (!state.enabled) return

    Text(status.line, style = MaterialTheme.typography.titleMedium)
    val loginUrl = state.info?.loginUrl.orEmpty()
    if (status.qr) {
        QrCode(loginUrl)
        Text(loginUrl, style = MaterialTheme.typography.bodySmall, color = Palette.textDim)
    }
    if (state.info?.state != RUNNING) {
        Field(
            stringResource(R.string.tailnet_key), authKey, stringResource(R.string.tailnet_key_hint), secret = true,
            revealable = true,
        ) { authKey = it }
        Button(enabled = authKey.isNotBlank(), onClick = {
            tailnet.loginWithKey(authKey)
            authKey = ""
        }) { Text(stringResource(R.string.tailnet_login)) }
    }
    if (status.logout) Button(onClick = tailnet::logout) { Text(stringResource(R.string.tailnet_logout)) }
}

/** The bridge's QR matrix (rows of '1'/'0', quiet zone included): black modules on white, at least 6 px each. */
@Composable
private fun QrCode(text: String) {
    val rows by produceState(emptyList<String>(), text) {
        value = withContext(Dispatchers.Default) {
            runCatching { Tailnet.qrMatrix(text) }.getOrNull()?.split('\n')?.filter { it.isNotEmpty() }.orEmpty()
        }
    }
    val modules = rows.size
    if (modules == 0) return
    val density = LocalDensity.current
    // About 360 dp on screen, rounded to whole pixels per module so the modules stay sharp.
    val modulePx = maxOf(MIN_MODULE_PX, with(density) { 360.dp.toPx() }.toInt() / modules)
    val side = with(density) { (modulePx * modules).toDp() }
    // Centred over the width of the fields above and below.
    Box(Modifier.widthIn(max = 720.dp).fillMaxWidth().padding(vertical = 8.dp), contentAlignment = Alignment.Center) {
        Canvas(Modifier.size(side).background(Color.White)) {
            rows.forEachIndexed { y, row ->
                row.forEachIndexed { x, c ->
                    if (c == '1') {
                        drawRect(Color.Black, Offset((x * modulePx).toFloat(), (y * modulePx).toFloat()), Size(modulePx.toFloat(), modulePx.toFloat()))
                    }
                }
            }
        }
    }
}

private const val RUNNING = "Running"
private const val MIN_MODULE_PX = 6
