package cz.cztvhub.core

import cz.cztvhub.core.ImageQueue.Outcome
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.Duration
import java.time.Instant

class ImageQueueTest {
    private val now = Instant.parse("2026-10-24T18:00:00Z")
    private val cached = HashSet<String>()
    private val queue = ImageQueue { it in cached }

    private fun ct(n: Int) = ImageRequest("https://ct/$n.jpg")
    private fun thumb(n: Int, recording: Boolean = false) =
        ImageRequest("http://pi/dvb/recording/$n/thumb.jpg", mapOf("X-Key" to "k"), gateway = true, stillRecording = recording)

    private fun takeAll(at: Instant = now): List<ImageRequest> = generateSequence { queue.take(at) }.toList()

    @Test
    fun windowOrderWithFourCtAndTwoGatewayAtATime() {
        val window = (1..6).map(::ct) + (1..3).map { thumb(it) }
        queue.prefetch(window, now)
        assertEquals((1..4).map(::ct) + (1..2).map { thumb(it) }, takeAll())
        queue.finished(ct(1), Outcome.OK, now)
        cached += ct(1).url
        queue.finished(thumb(1), Outcome.OK, now)
        assertEquals(listOf(ct(5), thumb(3)), takeAll())
    }

    @Test
    fun focusedGoesFirstAndANewWindowDropsTheRest() {
        queue.prefetch((1..8).map(::ct), now)
        assertEquals((1..4).map(::ct), takeAll())
        queue.focus(ct(20))
        queue.prefetch(listOf(ct(30), ct(31)), now) // ct(5)…ct(8) left the window
        (1..4).forEach { queue.finished(ct(it), Outcome.OK, now) }
        assertEquals(listOf(ct(20), ct(30), ct(31)), takeAll())
    }

    @Test
    fun notFoundIsRemembered() {
        queue.prefetch(listOf(thumb(1), thumb(2, recording = true)), now)
        val started = takeAll()
        started.forEach { queue.finished(it, Outcome.MISSING, now) }
        queue.prefetch(listOf(thumb(1), thumb(2, recording = true)), now.plusSeconds(30))
        assertEquals(emptyList<ImageRequest>(), takeAll(now.plusSeconds(30)))
        // A recording's thumbnail is asked again after 60 s, a finished one's not for the guide's lifetime.
        queue.prefetch(listOf(thumb(1), thumb(2, recording = true)), now.plusSeconds(61))
        assertEquals(listOf(thumb(2, recording = true)), takeAll(now.plusSeconds(61)))
    }

    @Test
    fun busyGatewayIsRetriedAfterRetryAfterWhileInTheWindow() {
        queue.prefetch(listOf(thumb(1), thumb(2), thumb(3)), now)
        assertEquals(listOf(thumb(1), thumb(2)), takeAll())
        queue.finished(thumb(1), Outcome.BUSY, now, Duration.ofSeconds(5))
        // Not missing: back at the front, but not before Retry-After.
        assertEquals(now.plusSeconds(5), queue.nextRetry(now))
        assertEquals(listOf(thumb(3)), takeAll(now.plusSeconds(1)))
        queue.finished(thumb(2), Outcome.OK, now.plusSeconds(2))
        queue.finished(thumb(3), Outcome.OK, now.plusSeconds(2))
        assertEquals(emptyList<ImageRequest>(), takeAll(now.plusSeconds(4)))
        assertEquals(listOf(thumb(1)), takeAll(now.plusSeconds(5)))
    }

    @Test
    fun busyRequestThatLeftTheWindowIsDropped() {
        queue.prefetch(listOf(thumb(1)), now)
        assertEquals(listOf(thumb(1)), takeAll())
        queue.prefetch(listOf(thumb(9)), now)
        queue.finished(thumb(1), Outcome.BUSY, now)
        assertEquals(listOf(thumb(9)), takeAll(now.plusSeconds(10)))
        assertNull(queue.nextRetry(now))
    }

    @Test
    fun busyFocusedIsAlwaysRetried() {
        queue.focus(thumb(7))
        assertEquals(listOf(thumb(7)), takeAll())
        queue.prefetch(emptyList(), now) // not in any window
        queue.finished(thumb(7), Outcome.BUSY, now, Duration.ofSeconds(5))
        assertEquals(now.plusSeconds(5), queue.nextRetry(now))
        assertEquals(emptyList<ImageRequest>(), takeAll(now.plusSeconds(3)))
        assertEquals(listOf(thumb(7)), takeAll(now.plusSeconds(5)))
        // Any other answer ends it, so a failing image is not asked in a loop.
        queue.finished(thumb(7), Outcome.FAILED, now.plusSeconds(6))
        assertEquals(emptyList<ImageRequest>(), takeAll(now.plusSeconds(6)))
    }

    @Test
    fun retryAfterHeader() {
        assertEquals(Duration.ofSeconds(5), ImageQueue.retryAfter("5"))
        assertEquals(Duration.ofSeconds(5), ImageQueue.retryAfter(null))
        assertEquals(Duration.ofSeconds(5), ImageQueue.retryAfter("Fri, 23 Oct 2026 20:00:00 GMT"))
        assertEquals(Duration.ofSeconds(1), ImageQueue.retryAfter("0"))
        assertEquals(Duration.ofSeconds(60), ImageQueue.retryAfter("3600"))
    }
}
