package cz.cztvhub.tv.ui

import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.darkColorScheme

object Palette {
    val background = Color(0xFF0E1116)
    val surface = Color(0xFF1A1F27)
    val surfaceDim = Color(0xFF14181E)
    val focused = Color(0xFFE8EAED)
    val onFocused = Color(0xFF0E1116)
    val accent = Color(0xFF3D7BFD)
    val airing = Color(0xFF213A6B)
    val text = Color(0xFFE8EAED)
    val textDim = Color(0xFF9AA0A6)
    val nowLine = Color(0xFFFF5252)
}

@Composable
fun TelevizeTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = Palette.accent,
            background = Palette.background,
            surface = Palette.surface,
            onSurface = Palette.text,
            onBackground = Palette.text,
        ),
        content = content,
    )
}
