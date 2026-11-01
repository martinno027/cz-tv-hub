package cz.cztvhub.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Duration
import java.time.Instant
import java.time.LocalDate

/** spec/apps.md "A failed ČT day is asked again by itself (T42)". */
class ScheduleRetryTest {
    private var now = Instant.parse("2026-10-16T07:10:00Z") // 09:10 Prague: the 16th's broadcast day
    private val day = LocalDate.of(2026, 10, 16)
    private var outage = true
    private var requests = 0

    /** ČT's API fails while [outage], then serves the schedule fixture; counts requests. */
    private val transport = HttpTransport { _, _ ->
        requests++
        Fixtures.response(if (outage) "graphql_error.json" else "schedule_ct1_ct24.json")
    }

    private fun retry() = ScheduleRetry(ScheduleRepository(CtApi(transport), clock = { now }), clock = { now })

    private fun failsWithScheduleText(block: () -> Unit) {
        try {
            block(); throw AssertionError("should fail")
        } catch (e: CtException) {
            assertEquals(Messages.SCHEDULE_FAILED, e.userMessage)
        }
    }

    @Test fun failedDayRecoversByItselfAfterTheOutage() {
        val r = retry()
        failsWithScheduleText { r.day(day) }
        assertTrue(r.hasFailed(day))
        outage = false
        // Not yet: 30 s after the failure.
        now = now.plusSeconds(29)
        assertTrue(r.retryDue(listOf(day)).isEmpty())
        assertEquals(1, requests)
        assertEquals(Duration.ofSeconds(1), r.untilNext(listOf(day)))
        now = now.plusSeconds(1)
        val loaded = r.retryDue(listOf(day))
        assertEquals(6, loaded.getValue(day).getValue("CH_1").size)
        assertFalse(r.hasFailed(day))
        assertNull(r.untilNext(listOf(day)))
        // Loaded: nothing more is asked.
        now = now.plusSeconds(60)
        assertTrue(r.retryDue(listOf(day)).isEmpty())
        assertEquals(2, requests)
    }

    @Test fun failedOnAirRecoversForZiveToo() {
        val r = retry()
        failsWithScheduleText { r.onAir() }
        assertTrue(r.hasFailed(day))
        outage = false
        now = now.plusSeconds(30)
        assertTrue(day in r.retryDue(listOf(day)))
        // Živě's cards from the same (now cached) day, no further request.
        val before = requests
        assertEquals("Testovací pořad 3", r.onAir(Instant.parse("2026-10-16T07:10:00Z")).getValue("CH_1").current?.title)
        assertTrue(requests - before <= 1) // only yesterday's plan for channels with nothing on air
    }

    @Test fun every30sForFiveMinutesThenEvery2MinutesUntilItLoads() {
        val r = retry()
        val first = now
        failsWithScheduleText { r.day(day) }
        // Ticks every 10 s, as a screen would wake: requests only when due.
        val askedAt = mutableListOf<Long>()
        while (Duration.between(first, now) < Duration.ofMinutes(15)) {
            now = now.plusSeconds(10)
            val before = requests
            r.retryDue(listOf(day))
            if (requests > before) askedAt += Duration.between(first, now).seconds
        }
        // 30 s apart while the outage is younger than 5 min (30 … 300 s), then 2 min apart.
        assertEquals(listOf(30L, 60, 90, 120, 150, 180, 210, 240, 270, 300, 420, 540, 660, 780, 900), askedAt)
        assertEquals(Duration.ofMinutes(2), r.untilNext(listOf(day)))
        // No cap: it is still asked, and loads once ČT is back.
        outage = false
        now = now.plusSeconds(120)
        assertTrue(day in r.retryDue(listOf(day)))
        assertFalse(r.hasFailed(day))
    }

    @Test fun aNewOutageAfterALoadStartsAt30sAgain() {
        val r = retry()
        failsWithScheduleText { r.day(day) }
        now = now.plusSeconds(600)
        r.retryDue(listOf(day)) // still failing, 10 min into the outage
        assertEquals(Duration.ofMinutes(2), r.untilNext(listOf(day)))
        outage = false
        now = now.plusSeconds(120)
        r.retryDue(listOf(day))
        outage = true
        now = now.plus(Duration.ofHours(2)) // the hourly refresh fails: a new outage
        failsWithScheduleText { r.day(day) }
        assertEquals(Duration.ofSeconds(30), r.untilNext(listOf(day)))
    }

    @Test fun isAskedAgainOnly30sAfterTheLatestFailure() {
        val r = retry()
        failsWithScheduleText { r.day(day) }
        now = now.plusSeconds(30)
        r.retryDue(listOf(day)) // fails again
        now = now.plusSeconds(20)
        assertTrue(r.retryDue(listOf(day)).isEmpty())
        assertEquals(2, requests)
        assertEquals(Duration.ofSeconds(10), r.untilNext(listOf(day)))
    }

    @Test fun onlyDaysShowingTheFailureAreAsked() {
        val r = retry()
        failsWithScheduleText { r.day(day) }
        now = now.plusSeconds(60)
        // The failed day is not on screen (another chip, or a refresh that kept the programmes shown).
        assertTrue(r.retryDue(listOf(day.plusDays(1))).isEmpty())
        assertNull(r.untilNext(emptyList()))
        assertEquals(1, requests)
    }
}
