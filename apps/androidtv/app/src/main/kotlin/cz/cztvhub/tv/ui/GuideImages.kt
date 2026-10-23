package cz.cztvhub.tv.ui

import androidx.compose.ui.graphics.ImageBitmap
import cz.cztvhub.core.MissingImages
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import java.time.Instant

/**
 * An image the guide may show: a CT image (CT's CDN, no key) or an Anténa thumbnail ([gateway], [headers] = the
 * key; [stillRecording] = its recording is still running, so a `404` may turn into an image later).
 */
data class ImageRequest(
    val url: String,
    val headers: Map<String, String> = emptyMap(),
    val gateway: Boolean = false,
    val stillRecording: Boolean = false,
)

/**
 * The guide's image requests (spec/apps.md "Image prefetch"): the focused programme's image first, then the
 * prefetch window in its order; at most 4 CT and 2 gateway requests at a time (the gateway runs at most 2 ffmpeg).
 * A new window replaces the queue, so requests that left it are dropped (running ones finish into the cache).
 * Lives as long as the guide screen, and so does its memory of `404`s. Main thread only.
 */
class GuideImages(private val cache: ImageCache, private val scope: CoroutineScope) {
    private val missing = MissingImages()
    private var queue = ArrayDeque<ImageRequest>()
    private var focused: ImageRequest? = null
    private val running = HashSet<String>()
    private var ctRunning = 0
    private var gatewayRunning = 0
    private val waiting = HashMap<String, MutableList<CompletableDeferred<Unit>>>()

    fun decoded(request: ImageRequest): ImageBitmap? = cache.decoded(request.url)

    /** Replaces the queue with [requests], nearest to the focus first. */
    fun prefetch(requests: List<ImageRequest>) {
        queue = ArrayDeque(requests.filter(::wanted).distinctBy { it.url })
        pump()
    }

    /** The focused programme's image: ahead of the queue, loaded now unless it is cached or known missing. */
    suspend fun image(request: ImageRequest): ImageBitmap? {
        if (!cache.has(request.url)) {
            if (missing.isMissing(request.url, Instant.now())) return null
            val done = CompletableDeferred<Unit>()
            waiting.getOrPut(request.url) { mutableListOf() } += done
            if (request.url !in running) focused = request
            pump()
            try {
                done.await()
            } finally {
                waiting[request.url]?.remove(done)
                // Focus moved on before it started: not wanted any more.
                if (focused == request) focused = null
            }
        }
        return cache.bitmap(request.url)
    }

    private fun wanted(request: ImageRequest): Boolean =
        request.url !in running && !cache.has(request.url) && !missing.isMissing(request.url, Instant.now())

    private fun next(gateway: Boolean): ImageRequest? {
        focused?.let {
            if (it.gateway == gateway && wanted(it)) {
                focused = null
                return it
            }
        }
        while (true) {
            val index = queue.indexOfFirst { it.gateway == gateway }
            if (index < 0) return null
            val request = queue.removeAt(index)
            if (wanted(request)) return request
        }
    }

    private fun pump() {
        while (ctRunning < CT_LIMIT) start(next(gateway = false) ?: break)
        while (gatewayRunning < GATEWAY_LIMIT) start(next(gateway = true) ?: break)
    }

    private fun start(request: ImageRequest) {
        running += request.url
        if (request.gateway) gatewayRunning++ else ctRunning++
        scope.launch {
            val result = try {
                cache.fetch(request.url, request.headers)
            } finally {
                running -= request.url
                if (request.gateway) gatewayRunning-- else ctRunning--
            }
            if (result == ImageCache.Fetched.MISSING) missing.add(request.url, request.stillRecording, Instant.now())
            waiting.remove(request.url)?.forEach { it.complete(Unit) }
            pump()
        }
    }

    private companion object {
        const val CT_LIMIT = 4
        const val GATEWAY_LIMIT = 2
    }
}
