package cz.cztvhub.tv.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Button
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import cz.cztvhub.tv.R
import cz.cztvhub.tv.tvApp
import kotlinx.coroutines.launch

/**
 * "Profil" (spec/apps.md): signed out, the forms "Přihlásit se" and "Vytvořit profil"; signed in, the name, whether
 * the settings are saved, and "Odhlásit". Needs the gateway. Passwords live only in this screen's memory (never
 * saved state) and are cleared after each attempt.
 */
@Composable
fun ProfileSettings(viewModel: MainViewModel) {
    val configured by LocalContext.current.tvApp.dvbSettings.config.collectAsState()
    val profiles = viewModel.profiles
    val state by profiles.state.collectAsState()
    val saveFailed by profiles.saveFailed.collectAsState()
    val scope = rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(top = 16.dp, bottom = 16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        val name = state.profile
        when {
            configured == null -> Text(stringResource(R.string.profile_needs_gateway), color = Palette.textDim)
            name != null -> {
                Text(stringResource(R.string.profile_signed_in, name), style = MaterialTheme.typography.headlineSmall)
                Text(
                    stringResource(if (state.pending && saveFailed) R.string.profile_waiting else R.string.profile_saved),
                    color = Palette.textDim,
                )
                Button(enabled = !busy, onClick = {
                    busy = true
                    scope.launch {
                        profiles.signOut()
                        busy = false
                    }
                }) { Text(stringResource(R.string.profile_sign_out)) }
            }
            else -> SignedOutForms(viewModel, busy) { busy = it }
        }
    }
}

@Composable
private fun SignedOutForms(viewModel: MainViewModel, busy: Boolean, setBusy: (Boolean) -> Unit) {
    var creating by rememberSaveable { mutableStateOf(false) }
    var name by rememberSaveable { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var again by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Button(onClick = { creating = false; error = null }) {
            Text((if (!creating) "● " else "") + stringResource(R.string.profile_sign_in))
        }
        Button(onClick = { creating = true; error = null }) {
            Text((if (creating) "● " else "") + stringResource(R.string.profile_create))
        }
    }
    Field(stringResource(R.string.profile_name), name, "", keyboardType = KeyboardType.Text) { name = it }
    Field(stringResource(R.string.profile_password), password, "", secret = true) { password = it }
    if (creating) Field(stringResource(R.string.profile_password_again), again, "", secret = true) { again = it }
    Button(enabled = !busy, onClick = {
        setBusy(true)
        scope.launch {
            error = if (creating) viewModel.profiles.create(name, password, again) else viewModel.profiles.signIn(name, password)
            password = ""
            again = ""
            setBusy(false)
        }
    }) { Text(stringResource(if (creating) R.string.profile_create_button else R.string.profile_sign_in_button)) }
    error?.let { Text(it, style = MaterialTheme.typography.titleMedium) }
}
