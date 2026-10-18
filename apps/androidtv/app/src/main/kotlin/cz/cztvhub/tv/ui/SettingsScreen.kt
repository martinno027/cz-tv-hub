package cz.cztvhub.tv.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusDirection
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
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Button
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import cz.cztvhub.core.CtException
import cz.cztvhub.core.dvb.DvbClient
import cz.cztvhub.core.dvb.DvbConfig
import cz.cztvhub.tv.R
import cz.cztvhub.tv.dvb.GatewayDiscovery
import cz.cztvhub.tv.tvApp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** "Nastavení": the household DVB gateway ("Anténa"). */
@Composable
fun SettingsScreen(viewModel: MainViewModel) {
    val context = LocalContext.current
    val settings = context.tvApp.dvbSettings
    val configured by settings.config.collectAsState()
    val discovered by remember { GatewayDiscovery.discover(context) }.collectAsState(initial = emptyList())
    var address by remember { mutableStateOf(settings.address) }
    var key by remember { mutableStateOf(configured?.key.orEmpty()) }
    var message by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val badAddress = stringResource(R.string.settings_bad_address)
    val badKey = stringResource(R.string.settings_bad_key)
    val okTemplate = stringResource(R.string.settings_ok)

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(top = 16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text(stringResource(R.string.settings_title), style = MaterialTheme.typography.headlineSmall)
        Text(stringResource(R.string.settings_intro), color = Palette.textDim)
        configured?.let { Text(stringResource(R.string.settings_configured, it.baseUrl), color = Palette.accent) }

        Field(stringResource(R.string.settings_address), address, stringResource(R.string.settings_address_hint)) { address = it }
        Field(stringResource(R.string.settings_key), key, "", secret = true) { key = it }

        Text(
            if (discovered.isEmpty()) stringResource(R.string.settings_searching) else stringResource(R.string.settings_discovered),
            color = Palette.textDim,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            discovered.forEach { gateway ->
                Button(onClick = { address = gateway.address }) { Text("${gateway.name} (${gateway.address})") }
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(onClick = {
                val base = DvbConfig.normalizeAddress(address)
                message = when {
                    base == null -> badAddress
                    key.trim().length < DvbConfig.MIN_KEY_LENGTH -> badKey
                    else -> null
                }
                if (base == null || message != null) return@Button
                val config = DvbConfig(base, key.trim())
                settings.save(config, address.trim())
                scope.launch {
                    message = withContext(Dispatchers.IO) {
                        try {
                            val client = DvbClient(config)
                            okTemplate.format(client.channels().size, client.status().retentionDays)
                        } catch (e: CtException) {
                            e.userMessage
                        }
                    }
                    viewModel.refreshDvb()
                }
            }) { Text(stringResource(R.string.settings_save)) }
            if (configured != null) {
                Button(onClick = {
                    settings.clear()
                    key = ""
                    message = null
                }) { Text(stringResource(R.string.settings_remove)) }
            }
        }
        message?.let { Text(it, style = MaterialTheme.typography.titleMedium) }
        Text(stringResource(R.string.disclaimer), style = MaterialTheme.typography.bodySmall, color = Palette.textDim)
    }
}

@Composable
private fun Field(label: String, value: String, hint: String, secret: Boolean = false, onChange: (String) -> Unit) {
    var focused by remember { mutableStateOf(false) }
    // Read-only until OK is pressed, so walking past a field with the D-pad never pops up the keyboard.
    var editing by remember { mutableStateOf(false) }
    val focusManager = LocalFocusManager.current
    val keyboard = LocalSoftwareKeyboardController.current
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(label, style = MaterialTheme.typography.titleSmall)
        BasicTextField(
            value = value,
            onValueChange = onChange,
            singleLine = true,
            readOnly = !editing,
            textStyle = MaterialTheme.typography.titleMedium.copy(color = Palette.text),
            cursorBrush = SolidColor(Palette.accent),
            keyboardOptions = KeyboardOptions(
                keyboardType = if (secret) KeyboardType.Password else KeyboardType.Uri,
                imeAction = ImeAction.Next,
            ),
            keyboardActions = KeyboardActions(onNext = { focusManager.moveFocus(FocusDirection.Down) }),
            visualTransformation = if (secret) PasswordVisualTransformation() else VisualTransformation.None,
            modifier = Modifier
                .widthIn(max = 720.dp)
                .fillMaxWidth()
                .onFocusChanged {
                    focused = it.isFocused
                    if (!it.isFocused) editing = false
                }
                // Text fields keep D-pad up/down for themselves; on a TV remote they must move focus.
                .onPreviewKeyEvent { event ->
                    val direction = when (event.key) {
                        Key.DirectionDown -> FocusDirection.Down
                        Key.DirectionUp -> FocusDirection.Up
                        Key.DirectionCenter, Key.Enter, Key.NumPadEnter -> {
                            if (editing) return@onPreviewKeyEvent false
                            if (event.type == KeyEventType.KeyUp) {
                                editing = true
                                keyboard?.show()
                            }
                            return@onPreviewKeyEvent true
                        }
                        else -> return@onPreviewKeyEvent false
                    }
                    if (event.type == KeyEventType.KeyDown) focusManager.moveFocus(direction)
                    true
                }
                .background(Palette.surface, RoundedCornerShape(8.dp))
                .border(2.dp, if (focused) Palette.focused else Palette.surface, RoundedCornerShape(8.dp))
                .padding(horizontal = 14.dp, vertical = 10.dp),
            decorationBox = { inner ->
                if (value.isEmpty() && hint.isNotEmpty()) Text(hint, color = Palette.textDim)
                inner()
            },
        )
    }
}
