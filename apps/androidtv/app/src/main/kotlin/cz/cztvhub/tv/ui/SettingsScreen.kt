package cz.cztvhub.tv.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
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
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Button
import androidx.tv.material3.ClickableSurfaceDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Surface
import androidx.tv.material3.Text
import cz.cztvhub.core.CtException
import cz.cztvhub.core.dvb.DvbClient
import cz.cztvhub.core.dvb.DvbConfig
import cz.cztvhub.tv.BuildConfig
import cz.cztvhub.tv.R
import cz.cztvhub.tv.dvb.GatewayDiscovery
import cz.cztvhub.tv.tvApp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** The three Nastavení sections (spec/apps.md: "Nastavení is a choice of three sections"). */
enum class SettingsSection { CHANNELS, PROFILE, SERVER }

/**
 * "Nastavení": a choice of three large items, "Kanály" (hide/reorder on this TV) first, then "Profil" (settings in
 * a profile on the gateway) and "Server" (the household DVB gateway for "Anténa", and "O aplikaci"). The chosen
 * section is shown full screen (MainScreen hides its tab row while [open] is set); Back returns to the choice with
 * focus on the item just left.
 */
@Composable
fun SettingsScreen(viewModel: MainViewModel, open: SettingsSection?, onOpen: (SettingsSection?) -> Unit) {
    val requesters = remember { SettingsSection.entries.associateWith { FocusRequester() } }
    var lastLeft by rememberSaveable { mutableStateOf<SettingsSection?>(null) }
    // Nastavení opened (spec/apps.md "Profil"): send a pending change or read a newer profile version.
    LaunchedEffect(Unit) { viewModel.syncProfile() }

    if (open != null) {
        BackHandler {
            lastLeft = open
            onOpen(null)
        }
        Column(Modifier.fillMaxSize()) {
            Text(
                stringResource(
                    when (open) {
                        SettingsSection.CHANNELS -> R.string.settings_tab_channels
                        SettingsSection.PROFILE -> R.string.settings_tab_profile
                        SettingsSection.SERVER -> R.string.settings_tab_server
                    },
                ),
                style = MaterialTheme.typography.headlineMedium,
            )
            Spacer(Modifier.height(8.dp))
            when (open) {
                SettingsSection.CHANNELS ->
                    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(top = 8.dp, bottom = 16.dp)) {
                        ChannelsSection(viewModel)
                    }
                SettingsSection.PROFILE -> ProfileSettings(viewModel)
                SettingsSection.SERVER -> ServerSettings(viewModel)
            }
        }
        return
    }

    LaunchedEffect(Unit) {
        // Back from a section: focus the item just left (the requester is attached by now).
        lastLeft?.let { runCatching { requesters.getValue(it).requestFocus() } }
    }
    Column(
        Modifier.fillMaxSize().padding(top = 24.dp),
        verticalArrangement = Arrangement.spacedBy(24.dp),
    ) {
        // Which build this is (spec/apps.md "Version line").
        Text(
            stringResource(
                R.string.version_line,
                BuildConfig.VERSION_NAME,
                BuildConfig.BUILD_NUMBER,
                BuildConfig.GIT_COMMIT,
                BuildConfig.BUILD_TIME,
            ),
            style = MaterialTheme.typography.bodyLarge,
            color = Palette.textDim,
        )
        SectionItem(
            stringResource(R.string.settings_tab_channels),
            stringResource(R.string.settings_channels_detail),
            Modifier.focusRequester(requesters.getValue(SettingsSection.CHANNELS)),
        ) { onOpen(SettingsSection.CHANNELS) }
        val profile by viewModel.profiles.state.collectAsState()
        SectionItem(
            stringResource(R.string.settings_tab_profile),
            profile.profile?.let { stringResource(R.string.profile_signed_in, it) } ?: stringResource(R.string.profile_signed_out),
            Modifier.focusRequester(requesters.getValue(SettingsSection.PROFILE)),
        ) { onOpen(SettingsSection.PROFILE) }
        SectionItem(
            stringResource(R.string.settings_tab_server),
            stringResource(R.string.settings_server_detail),
            Modifier.focusRequester(requesters.getValue(SettingsSection.SERVER)),
        ) { onOpen(SettingsSection.SERVER) }
    }
}

@Composable
private fun SectionItem(title: String, detail: String, modifier: Modifier, onClick: () -> Unit) {
    Surface(
        onClick = onClick,
        modifier = modifier.widthIn(max = 900.dp).fillMaxWidth(),
        shape = ClickableSurfaceDefaults.shape(RoundedCornerShape(16.dp)),
        scale = ClickableSurfaceDefaults.scale(focusedScale = 1.03f),
        colors = ClickableSurfaceDefaults.colors(
            containerColor = Palette.surface,
            contentColor = Palette.text,
            focusedContainerColor = Palette.focused,
            focusedContentColor = Palette.onFocused,
        ),
    ) {
        Column(Modifier.padding(horizontal = 28.dp, vertical = 22.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(title, style = MaterialTheme.typography.headlineSmall)
            Text(detail, style = MaterialTheme.typography.bodyLarge)
        }
    }
}

/** "Server": the household DVB gateway ("Anténa"); "O aplikaci" at the end. */
@Composable
private fun ServerSettings(viewModel: MainViewModel) {
    val context = LocalContext.current
    val app = context.tvApp
    val settings = app.dvbSettings
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
        TailnetSettings()

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
                    // With Tailscale on, a new address restarts the bridge and the check goes through it.
                    app.tailnet.ready()
                    val reached = app.route(config) ?: config
                    message = withContext(Dispatchers.IO) {
                        try {
                            val client = DvbClient(reached)
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
                    // Signed in: sign out on this TV first (spec/apps.md "Profil"), the TV's own settings apply again.
                    viewModel.profiles.signOutLocally()
                    settings.clear()
                    key = ""
                    message = null
                }) { Text(stringResource(R.string.settings_remove)) }
            }
        }
        message?.let { Text(it, style = MaterialTheme.typography.titleMedium) }

        // "Náhledy anténních pořadů": only offered when the gateway allows thumbnails (its /admin switch).
        val dvb by viewModel.dvb.collectAsState()
        val thumbnailsOn by viewModel.antennaThumbnails.collectAsState()
        if (configured != null && dvb?.thumbnails == true) {
            Button(
                onClick = { viewModel.setAntennaThumbnails(!thumbnailsOn) },
                modifier = Modifier.padding(top = 8.dp),
            ) { Text((if (thumbnailsOn) "☑  " else "☐  ") + stringResource(R.string.settings_thumbnails)) }
            Text(stringResource(R.string.settings_thumbnails_hint), style = MaterialTheme.typography.bodySmall, color = Palette.textDim)
        }

        Text(stringResource(R.string.settings_about), style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 16.dp))
        Text(stringResource(R.string.disclaimer), style = MaterialTheme.typography.bodySmall, color = Palette.textDim)
    }
}

@Composable
internal fun Field(
    label: String,
    value: String,
    hint: String,
    secret: Boolean = false,
    keyboardType: KeyboardType = KeyboardType.Uri,
    onChange: (String) -> Unit,
) {
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
                keyboardType = if (secret) KeyboardType.Password else keyboardType,
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
