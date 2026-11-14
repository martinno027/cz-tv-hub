package cz.cztvhub.tv.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.DrawableRes
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.tv.material3.Button
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import cz.cztvhub.core.Days
import cz.cztvhub.core.dvb.WarningLevel
import cz.cztvhub.core.dvb.Weather
import cz.cztvhub.core.dvb.WeatherRules
import cz.cztvhub.tv.DeviceLocation
import cz.cztvhub.tv.R
import java.time.Instant

/** The gateway's `icon` keys (spec/dvb-gateway.md "Weather"); an unknown key looks like `cloud`, as on the gateway. */
object WeatherIcons {
    val KEYS: Map<String, Int> = mapOf(
        "sun" to R.drawable.weather_sun,
        "partly" to R.drawable.weather_partly,
        "cloud" to R.drawable.weather_cloud,
        "fog" to R.drawable.weather_fog,
        "drizzle" to R.drawable.weather_drizzle,
        "rain" to R.drawable.weather_rain,
        "snow" to R.drawable.weather_snow,
        "storm" to R.drawable.weather_storm,
    )

    @DrawableRes
    fun of(key: String?): Int = KEYS[key] ?: R.drawable.weather_cloud
}

private fun WarningLevel?.color(): Color = when (this) {
    WarningLevel.YELLOW -> Palette.warningYellow
    WarningLevel.ORANGE -> Palette.warningOrange
    WarningLevel.RED -> Palette.warningRed
    null -> Palette.text
}

/**
 * Weather now next to the date (spec/apps.md "Domů" block 1): icon, `8 °C, polojasno`, `Slunce 7:13–16:19`, then
 * air, pollen, ČHMÚ warnings in their colour and the attribution, each only when present.
 */
@Composable
fun WeatherNowBlock(weather: Weather, now: Instant, modifier: Modifier = Modifier) {
    Column(modifier.widthIn(max = 520.dp), horizontalAlignment = Alignment.End, verticalArrangement = Arrangement.spacedBy(2.dp)) {
        weather.now?.let { current ->
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                Image(painterResource(WeatherIcons.of(current.icon)), contentDescription = current.text, modifier = Modifier.size(56.dp))
                Column(horizontalAlignment = Alignment.End) {
                    WeatherRules.nowLine(current)?.let { Text(it, style = MaterialTheme.typography.headlineSmall) }
                    weather.sun?.let { Text(WeatherRules.sunLine(it), style = MaterialTheme.typography.bodyLarge, color = Palette.textDim) }
                }
            }
        }
        val lines = listOfNotNull(WeatherRules.airLine(weather.air), WeatherRules.pollenLine(weather.air))
        lines.forEach { Text(it, style = MaterialTheme.typography.bodyLarge, color = Palette.textDim, textAlign = TextAlign.End) }
        WeatherRules.current(weather.warnings, now).forEach {
            Text(WeatherRules.warningLine(it, now), style = MaterialTheme.typography.titleSmall, color = it.level.color(), textAlign = TextAlign.End)
        }
        weather.attribution?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = Palette.textDim, textAlign = TextAlign.End) }
    }
}

/** Block 8 "Počasí na týden": the days as cards; not focusable (focus skips it). */
@Composable
fun WeatherWeek(weather: Weather, now: Instant) {
    val today = Days.today(now)
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(stringResource(R.string.home_weather_week), style = MaterialTheme.typography.titleLarge, color = Palette.textDim)
        Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            weather.days.forEach { day ->
                Column(
                    Modifier.weight(1f).background(Palette.surface, RoundedCornerShape(16.dp)).padding(vertical = 14.dp, horizontal = 8.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Text(WeatherRules.dayLabel(day.date, today), style = MaterialTheme.typography.titleMedium)
                    Image(painterResource(WeatherIcons.of(day.icon)), contentDescription = day.text, modifier = Modifier.size(44.dp))
                    WeatherRules.temperatures(day)?.let { Text(it, style = MaterialTheme.typography.titleMedium) }
                    Text(WeatherRules.rainLine(day) ?: "", style = MaterialTheme.typography.bodyMedium, color = Palette.textDim)
                }
            }
        }
    }
}

/**
 * Povolit (spec/apps.md "Location for weather"): the switch on, then the OS prompt when the permission is not granted
 * yet; [then] after the prompt (or at once).
 */
@Composable
fun rememberWeatherAllow(viewModel: MainViewModel, then: () -> Unit): () -> Unit {
    val context = LocalContext.current
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { then() }
    return {
        viewModel.setWeatherByLocation(true)
        if (DeviceLocation.granted(context)) then() else launcher.launch(DeviceLocation.PERMISSION)
    }
}

/** The one-time question before the first OS prompt: Povolit / Nepoužívat (= never ask the OS). */
@Composable
fun WeatherConsentDialog(onAllow: () -> Unit, onDecline: () -> Unit, onDismiss: () -> Unit) {
    Dialog(onDismissRequest = onDismiss) {
        Column(
            Modifier.widthIn(max = 640.dp).background(Palette.surface, RoundedCornerShape(16.dp)).padding(32.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            Text(stringResource(R.string.weather_consent), style = MaterialTheme.typography.titleMedium, color = Palette.text)
            val first = remember { FocusRequester() }
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                Button(onClick = onAllow, modifier = Modifier.focusRequester(first)) { Text(stringResource(R.string.weather_allow)) }
                Button(onClick = onDecline) { Text(stringResource(R.string.weather_decline)) }
            }
            LaunchedEffect(Unit) { runCatching { first.requestFocus() } }
        }
    }
}
