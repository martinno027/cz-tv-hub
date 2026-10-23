package cz.cztvhub.tv.ui

import androidx.compose.ui.graphics.ImageBitmap
import cz.cztvhub.core.ImageQueue
import cz.cztvhub.core.ImageRequest
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.time.Duration
import java.time.Instant

/**
 * The guide's image loading (spec/apps.md "Image prefetch"): runs [ImageQueue]'s requests into the [cache].
 * Lives as long as the guide screen, and so does its memory of `404`s. Main thread only.
 */
class GuideImages(private val cache: ImageCache, private val scope: CoroutineScope) {
    private val queue = ImageQueue(cache::has)
    private val waiting = HashMap<String, MutableList<CompletableDeferred<Unit>>>()
    private var retry: Job? = null

    fun decoded(request: ImageRequest): ImageBitmap? = cache.decoded(request.url)

    /** Replaces the queue with [requests], nearest to the focus first. */
    fun prefetch(requests: List<ImageRequest>) {
        queue.prefetch(requests, Instant.now())
        pump()
    }

    /** The focused programme's image: ahead of the queue, loaded now unless it is cached or known missing. */
    suspend fun image(request: ImageRequest): ImageBitmap? {
        if (!cache.has(request.url)) {
            if (queue.isMissing(request.url, Instant.now())) return null
            val done = CompletableDeferred<Unit>()
            waiting.getOrPut(request.url) { mutableListOf() } += done
            queue.focus(request)
            pump()
            try {
                done.await()
            } finally {
                waiting[request.url]?.remove(done)
                // Focus moved on: not wanted ahead of the queue any more.
                queue.unfocus(request)
            }
        }
        return cache.bitmap(request.url)
    }

    private fun pump() {
        while (true) {
            val request = queue.take(Instant.now()) ?: break
            scope.launch {
                val fetched = try {
                    cache.fetch(request.url, request.headers)
                } catch (e: Throwable) {
                    queue.finished(request, ImageQueue.Outcome.FAILED, Instant.now())
                    throw e
                }
                queue.finished(request, fetched.outcome, Instant.now(), fetched.retryAfter)
                // A busy gateway: whoever waits keeps waiting for the retry.
                if (fetched.outcome != ImageQueue.Outcome.BUSY) waiting.remove(request.url)?.forEach { it.complete(Unit) }
                pump()
            }
        }
        scheduleRetry()
    }

    /** Wakes the queue when the earliest busy request may be asked again. */
    private fun scheduleRetry() {
        val at = queue.nextRetry(Instant.now()) ?: return
        retry?.cancel()
        retry = scope.launch {
            delay(Duration.between(Instant.now(), at).toMillis().coerceAtLeast(0))
            pump()
        }
    }
}
