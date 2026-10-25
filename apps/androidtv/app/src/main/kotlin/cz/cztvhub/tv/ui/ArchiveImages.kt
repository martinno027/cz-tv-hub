package cz.cztvhub.tv.ui

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
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import cz.cztvhub.core.ImageQueue
import cz.cztvhub.core.ImageRequest
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit

/**
 * Card images of the Archiv tab, into the app's memory [ImageCache] (no disk, like the guide's): every card on
 * screen loads its own, at most 4 from ČT and 2 from the gateway at a time (the gateway's ffmpeg slots); a `404` is
 * not asked again while the tab lives, a busy gateway (`503`) is asked again after `Retry-After` while the card is
 * on screen. Main thread only.
 */
class ArchiveImages(private val cache: ImageCache) {
    private val ct = Semaphore(ImageQueue.CT_LIMIT)
    private val gateway = Semaphore(ImageQueue.GATEWAY_LIMIT)
    private val missing = HashSet<String>()

    fun decoded(request: ImageRequest): ImageBitmap? = cache.decoded(request.url)

    suspend fun image(request: ImageRequest): ImageBitmap? {
        cache.decoded(request.url)?.let { return it }
        while (!cache.has(request.url)) {
            if (request.url in missing) return null
            val fetched = (if (request.gateway) gateway else ct).withPermit {
                if (cache.has(request.url)) null else cache.fetch(request.url, request.headers)
            } ?: break
            when (fetched.outcome) {
                ImageQueue.Outcome.OK -> break
                ImageQueue.Outcome.MISSING -> missing += request.url
                ImageQueue.Outcome.BUSY -> delay(fetched.retryAfter.toMillis())
                ImageQueue.Outcome.FAILED -> return null
            }
        }
        return cache.bitmap(request.url)
    }
}

/** A card's image ([request] null = none), else the source icon of the card as placeholder. */
@Composable
fun ArchiveImage(images: ArchiveImages, request: ImageRequest?, placeholder: ChannelSource?, modifier: Modifier = Modifier) {
    val bitmap by produceState<ImageBitmap?>(null, request) {
        value = request?.let(images::decoded)
        if (request != null && value == null) value = images.image(request)
    }
    val image = bitmap
    if (image != null) {
        Image(image, contentDescription = null, modifier = modifier, contentScale = ContentScale.Crop)
    } else {
        Box(modifier.background(Palette.surfaceDim), contentAlignment = Alignment.Center) {
            if (placeholder != null) SourceIcon(placeholder, size = 40.dp, modifier = Modifier.alpha(0.6f))
        }
    }
}
