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
import androidx.compose.ui.unit.dp
import cz.cztvhub.core.CtApi
import cz.cztvhub.core.ImageQueue
import cz.cztvhub.core.ImageRequest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.URI
import java.time.Duration

/**
 * In-memory cache of programme images (no disk cache, no image library). Anténa thumbnails go through it too:
 * by the approval of 2026-10-23 they must never be stored on disk. The guide prefetches around the focus
 * (spec/apps.md "Image prefetch", about 300 images), so it keeps the downloaded bytes (a few dozen kB each) and
 * decodes only the few images the detail actually shows.
 */
class ImageCache {
    /** A download's answer; [retryAfter] is the gateway's `Retry-After` when it is busy (`503`). */
    data class Fetched(val outcome: ImageQueue.Outcome, val retryAfter: Duration = ImageQueue.BUSY_RETRY)

    /** A share of the heap, at most 48 MB; every entry counts at least budget/300, so about 300 images at most. */
    private val budget = minOf(Runtime.getRuntime().maxMemory() / 8, 48L shl 20).toInt()
    private val bytes = object : LruCache<String, ByteArray>(budget) {
        override fun sizeOf(key: String, value: ByteArray): Int = maxOf(value.size, budget / MAX_IMAGES)
    }
    private val bitmaps = LruCache<String, ImageBitmap>(DECODED)

    fun has(url: String): Boolean = bytes.get(url) != null

    /** The decoded image if it is at hand, without waiting. */
    fun decoded(url: String): ImageBitmap? = bitmaps.get(url)

    /** Downloads [url] into the cache ([headers] = the gateway key for Anténa thumbnails). */
    suspend fun fetch(url: String, headers: Map<String, String>): Fetched = withContext(Dispatchers.IO) {
        try {
            val connection = URI(url).toURL().openConnection() as HttpURLConnection
            connection.connectTimeout = 8000
            connection.readTimeout = 8000
            connection.useCaches = false
            connection.setRequestProperty("User-Agent", CtApi.USER_AGENT)
            headers.forEach { (k, v) -> connection.setRequestProperty(k, v) }
            try {
                when (connection.responseCode) {
                    200 -> {
                        bytes.put(url, connection.inputStream.use { it.readBytes() })
                        Fetched(ImageQueue.Outcome.OK)
                    }
                    404 -> Fetched(ImageQueue.Outcome.MISSING)
                    // The gateway's ffmpeg slots are busy: try again later, never "missing".
                    503 -> Fetched(ImageQueue.Outcome.BUSY, ImageQueue.retryAfter(connection.getHeaderField("Retry-After")))
                    else -> Fetched(ImageQueue.Outcome.FAILED)
                }
            } finally {
                connection.disconnect()
            }
        } catch (_: Exception) {
            Fetched(ImageQueue.Outcome.FAILED)
        }
    }

    /** The cached image decoded (about 480 px wide), or null when it is not cached or not an image. */
    suspend fun bitmap(url: String): ImageBitmap? {
        bitmaps.get(url)?.let { return it }
        val data = bytes.get(url) ?: return null
        return withContext(Dispatchers.Default) {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeByteArray(data, 0, data.size, bounds)
            var sample = 1
            while (bounds.outWidth / (sample * 2) >= 480) sample *= 2
            val options = BitmapFactory.Options().apply { inSampleSize = sample }
            BitmapFactory.decodeByteArray(data, 0, data.size, options)?.asImageBitmap()?.also { bitmaps.put(url, it) }
        }
    }

    private companion object {
        const val MAX_IMAGES = 300
        const val DECODED = 12
    }
}

/**
 * The guide detail's image ([request] null = none): a CT image, or an Anténa programme's gateway thumbnail with
 * the antenna placeholder ([antenna]) until it arrives or when there is none. Memory only.
 */
@Composable
fun GuideImage(images: GuideImages, request: ImageRequest?, antenna: Boolean, modifier: Modifier = Modifier) {
    val bitmap by produceState<ImageBitmap?>(null, request) {
        // Never the previous programme's image while this one loads.
        value = request?.let(images::decoded)
        if (request != null && value == null) value = images.image(request)
    }
    val image = bitmap
    if (image != null) {
        Image(image, contentDescription = null, modifier = modifier, contentScale = ContentScale.Crop)
    } else {
        Box(modifier.background(Palette.surfaceDim), contentAlignment = Alignment.Center) {
            if (antenna) SourceIcon(antenna = true, size = 44.dp, modifier = Modifier.alpha(0.6f))
        }
    }
}
