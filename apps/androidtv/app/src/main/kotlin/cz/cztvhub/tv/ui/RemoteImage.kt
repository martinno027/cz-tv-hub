package cz.cztvhub.tv.ui

import android.graphics.BitmapFactory
import android.util.LruCache
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import cz.cztvhub.core.CtApi
import cz.cztvhub.tv.tvApp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.URI

/**
 * Small in-memory cache of programme images (no disk cache, no image library). Anténa thumbnails go through it too:
 * by the approval of 2026-10-23 they must never be stored on disk.
 */
class ImageCache {
    private val cache = LruCache<String, ImageBitmap>(48)

    suspend fun load(url: String, headers: Map<String, String> = emptyMap()): ImageBitmap? {
        cache.get(url)?.let { return it }
        return withContext(Dispatchers.IO) {
            try {
                val connection = URI(url).toURL().openConnection() as HttpURLConnection
                connection.connectTimeout = 8000
                connection.readTimeout = 8000
                connection.useCaches = false
                connection.setRequestProperty("User-Agent", CtApi.USER_AGENT)
                headers.forEach { (k, v) -> connection.setRequestProperty(k, v) }
                val bytes = try {
                    if (connection.responseCode != 200) return@withContext null
                    connection.inputStream.use { it.readBytes() }
                } finally {
                    connection.disconnect()
                }
                val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
                var sample = 1
                while (bounds.outWidth / (sample * 2) >= 480) sample *= 2
                val options = BitmapFactory.Options().apply { inSampleSize = sample }
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)?.asImageBitmap()
                    ?.also { cache.put(url, it) }
            } catch (_: Exception) {
                null
            }
        }
    }
}

@Composable
fun RemoteImage(url: String, modifier: Modifier = Modifier) {
    val images = LocalContext.current.tvApp.images
    val bitmap by produceState<ImageBitmap?>(null, url) {
        value = if (url.startsWith("https://")) images.load(url) else null
    }
    val image = bitmap
    if (image != null) {
        Image(image, contentDescription = null, modifier = modifier, contentScale = ContentScale.Crop)
    } else {
        Box(modifier.background(Palette.surfaceDim))
    }
}

/**
 * An Anténa programme's gateway thumbnail ([source] = URL + key headers, or null): loaded only once the focus has
 * rested on the programme for a moment, memory only; the antenna placeholder otherwise or until it arrives.
 */
@Composable
fun AntennaImage(source: Pair<String, Map<String, String>>?, modifier: Modifier = Modifier) {
    val images = LocalContext.current.tvApp.images
    val bitmap by produceState<ImageBitmap?>(null, source) {
        value = null
        if (source == null) return@produceState
        delay(THUMBNAIL_FOCUS_DELAY_MS)
        value = images.load(source.first, source.second)
    }
    val image = bitmap
    if (image != null) {
        Image(image, contentDescription = null, modifier = modifier, contentScale = ContentScale.Crop)
    } else {
        Box(modifier.background(Palette.surfaceDim), contentAlignment = Alignment.Center) {
            SourceIcon(antenna = true, size = 44.dp, modifier = Modifier.alpha(0.6f))
        }
    }
}

private const val THUMBNAIL_FOCUS_DELAY_MS = 300L
