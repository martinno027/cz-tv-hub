package cz.cztvhub.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Duration
import java.time.Instant
import java.time.LocalDate

class ScheduleRepositoryTest {
    private var now = Instant.parse("2026-10-16T08:00:00Z")

    private fun repo(transport: HttpTransport) = ScheduleRepository(CtApi(transport), clock = { now })

    @Test fun requestBodyHasAllSlugsAndUsDate() {
        val t = FakeTransport(mapOf("graphql" to "schedule_ct1_ct24.json"))
        repo(t).day(LocalDate.of(2026, 10, 16))
        val (url, body) = t.requests.single()
        assertTrue(url.startsWith("https://api.ceskatelevize.cz/graphql/?client=iVysilaniWeb"))
        assertTrue(body!!.contains("\"date\":\"10.16.2026\""))
        assertTrue(body.contains("[\"ct1\",\"ct2\",\"ct24\",\"sport\",\"decko\",\"art\"]"))
        assertTrue(body.contains("TVProgramDailyChannelsPlanV2"))
    }

    @Test fun mapsSlugsToChannelIdsAndCaches() {
        val t = FakeTransport(mapOf("graphql" to "schedule_ct1_ct24.json"))
        val r = repo(t)
        val plan = r.day(LocalDate.of(2026, 10, 16))
        assertEquals(6, plan.getValue("CH_1").size)
        assertEquals(6, plan.getValue("CH_24").size)
        assertEquals(0, plan.getValue("CH_2").size)
        r.day(LocalDate.of(2026, 10, 16))
        assertEquals(1, t.requests.size)
        now = now.plus(Duration.ofMinutes(61))
        r.day(LocalDate.of(2026, 10, 16))
        assertEquals(2, t.requests.size)
    }

    @Test fun pastDaysAreNotRefetched() {
        val t = FakeTransport(mapOf("graphql" to "schedule_ct1_ct24.json"))
        val r = repo(t)
        r.day(LocalDate.of(2026, 10, 15))
        now = now.plus(Duration.ofHours(5))
        r.day(LocalDate.of(2026, 10, 15))
        assertEquals(1, t.requests.size)
    }

    @Test fun broadcastDayOnAirAfterMidnightIsStillRefetched() {
        val t = FakeTransport(mapOf("graphql" to "schedule_ct1_ct24.json"))
        val r = repo(t)
        now = Instant.parse("2026-10-16T22:20:00Z") // 00:20 on the 17th in Prague: the 16th's broadcast day
        r.day(LocalDate.of(2026, 10, 16))
        now = now.plus(Duration.ofMinutes(61))
        r.day(LocalDate.of(2026, 10, 16))
        assertEquals(2, t.requests.size)
        now = Instant.parse("2026-10-17T03:10:00Z") // 05:10: a past day now, kept as it is
        r.day(LocalDate.of(2026, 10, 16))
        assertEquals(2, t.requests.size)
    }

    @Test fun failedDayIsRetriedNotCached() {
        var fail = true
        val transport = HttpTransport { _, _ ->
            Fixtures.response(if (fail) "graphql_error.json" else "schedule_ct1_ct24.json")
        }
        val r = repo(transport)
        try {
            r.day(LocalDate.of(2026, 10, 16)); throw AssertionError("should fail")
        } catch (e: CtException) {
            assertEquals(Messages.SCHEDULE_FAILED, e.userMessage)
        }
        fail = false
        assertEquals(6, r.day(LocalDate.of(2026, 10, 16)).getValue("CH_1").size)
    }

    @Test fun onAirFindsCurrentAndNext() {
        val t = FakeTransport(mapOf("graphql" to "schedule_ct1_ct24.json"))
        now = Instant.parse("2026-10-16T07:10:00Z")
        val onAir = repo(t).onAir()
        assertEquals("Testovací pořad 3", onAir.getValue("CH_1").current?.title)
        assertEquals("Testovací pořad 5", onAir.getValue("CH_1").next?.title)
        assertEquals("Testovací pořad 10", onAir.getValue("CH_24").current?.title)
        // Nothing found for the other channels, so yesterday's plan was asked too.
        assertEquals(2, t.requests.size)
    }

    @Test fun onAirInAGapShowsNext() {
        val t = FakeTransport(mapOf("graphql" to "schedule_ct1_ct24.json"))
        now = Instant.parse("2026-10-16T07:52:00Z")
        val ct1 = repo(t).onAir().getValue("CH_1")
        assertEquals(null, ct1.current)
        assertEquals("Testovací pořad 5", ct1.next?.title)
    }
}
