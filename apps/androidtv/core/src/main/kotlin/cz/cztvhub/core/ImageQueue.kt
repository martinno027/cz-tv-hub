package cz.cztvhub.core

import java.time.Duration
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
 * The guide's image requests in order (spec/apps.md "Image prefetch"): the focused programme's image first, then the
 * prefetch window nearest first; at most 4 CT and 2 gateway requests at a time (the gateway runs at most 2 ffmpeg).
 * A new window replaces the queue, so requests that left it are dropped (running ones finish). A `404` is
 * remembered ([MissingImages]); a `503` (gateway busy) never is: the request goes back into the queue and is
 * retried after `Retry-After` while it is still in the window, the focused one always. [cached] tells which URLs
 * the app already has. Not thread-safe: one thread drives it.
 */
class ImageQueue(private val cached: (String) -> Boolean) {
    enum class Outcome { OK, MISSING, BUSY, FAILED }

    private val missing = MissingImages()
    private val busyUntil = HashMap<String, Instant>()
    private var queue = ArrayList<ImageRequest>()
    private var window = emptySet<String>()
    private var focused: ImageRequest? = null
    private val running = HashSet<String>()
    private var ctRunning = 0
    private var gatewayRunning = 0

    fun isMissing(url: String, now: Instant): Boolean = missing.isMissing(url, now)

    /** A new prefetch window, nearest to the focus first; replaces the queue. */
    fun prefetch(requests: List<ImageRequest>, now: Instant) {
        queue = ArrayList(requests.filter { !cached(it.url) && !missing.isMissing(it.url, now) }.distinctBy { it.url })
        window = queue.mapTo(HashSet()) { it.url }
    }

    /** The focused programme's image goes ahead of the queue until [unfocus] (also across `503` retries). */
    fun focus(request: ImageRequest) {
        focused = request
    }

    fun unfocus(request: ImageRequest) {
        if (focused == request) focused = null
    }

    /** The next request to start now (it counts as running until [finished]), or null. */
    fun take(now: Instant): ImageRequest? {
        focused?.let { if (startable(it, now)) return start(it) }
        var i = 0
        while (i < queue.size) {
            val request = queue[i]
            if (request.url in running || cached(request.url) || missing.isMissing(request.url, now)) {
                queue.removeAt(i)
                continue
            }
            if (startable(request, now)) {
                queue.removeAt(i)
                return start(request)
            }
            i++
        }
        return null
    }

    /** [request] answered [outcome]; [retryAfter] is the gateway's `Retry-After` for [Outcome.BUSY]. */
    fun finished(request: ImageRequest, outcome: Outcome, now: Instant, retryAfter: Duration = BUSY_RETRY) {
        running -= request.url
        if (request.gateway) gatewayRunning-- else ctRunning--
        when (outcome) {
            Outcome.MISSING -> missing.add(request.url, request.stillRecording, now)
            Outcome.BUSY -> {
                busyUntil[request.url] = now.plus(retryAfter)
                if (request.url in window && queue.none { it.url == request.url }) queue.add(0, request)
            }
            Outcome.OK, Outcome.FAILED -> Unit
        }
        // Only a busy gateway keeps the focused image coming; anything else is its answer.
        if (outcome != Outcome.BUSY && focused?.url == request.url) focused = null
    }

    /**
     * When the next busy request still wanted (focused or queued) may be asked again, if later than [now]; null =
     * none is waiting (one that may already go waits for a free slot, which the next finished request brings).
     */
    fun nextRetry(now: Instant): Instant? {
        val wanted = queue.mapTo(HashSet()) { it.url }
        focused?.let { wanted += it.url }
        return busyUntil.filter { (url, at) -> url in wanted && at.isAfter(now) }.values.minOrNull()
    }

    private fun startable(request: ImageRequest, now: Instant): Boolean {
        if (request.url in running || cached(request.url) || missing.isMissing(request.url, now)) return false
        busyUntil[request.url]?.let { if (now.isBefore(it)) return false else busyUntil.remove(request.url) }
        return if (request.gateway) gatewayRunning < GATEWAY_LIMIT else ctRunning < CT_LIMIT
    }

    private fun start(request: ImageRequest): ImageRequest {
        running += request.url
        if (request.gateway) gatewayRunning++ else ctRunning++
        return request
    }

    companion object {
        const val CT_LIMIT = 4
        const val GATEWAY_LIMIT = 2
        /** The gateway's `Retry-After` when busy (spec/dvb-gateway.md "Thumbnails"); used when it sends none. */
        val BUSY_RETRY: Duration = Duration.ofSeconds(5)

        /** A `Retry-After` header in seconds, kept within 1–60 s; [BUSY_RETRY] when missing or not a number. */
        fun retryAfter(header: String?): Duration =
            header?.trim()?.toLongOrNull()?.coerceIn(1, 60)?.let(Duration::ofSeconds) ?: BUSY_RETRY
    }
}
