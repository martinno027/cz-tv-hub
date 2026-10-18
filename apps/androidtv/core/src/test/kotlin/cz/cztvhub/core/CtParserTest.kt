package cz.cztvhub.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.time.Instant

class CtParserTest {

    private inline fun <reified T : CtException> expect(block: () -> Unit): T {
        try {
            block()
        } catch (e: CtException) {
            if (e is T) return e
            fail("expected ${T::class.simpleName}, got $e")
        }
        fail("expected ${T::class.simpleName}")
        throw AssertionError()
    }

    private fun live(name: String): LiveStreams {
        val r = Fixtures.response(name)
        return CtParser.parseLive("CH_1", r.status, r.body)
    }

    private fun vod(name: String): String {
        val r = Fixtures.response(name)
        return CtParser.parseVod("226411010101016", r.status, r.body)
    }

    @Test fun liveReturnsMainAndTimeshift() {
        val streams = live("live_CH_1.json")
        assertTrue(streams.main.contains("contentType=live"))
        assertTrue(streams.timeshift!!.contains("contentType=timeshift"))
    }

    @Test fun liveBlockedSurfacesCzechMessage() {
        val e = expect<CtException> { live("live_blocked.json") }
        assertEquals(Messages.BLOCKED, e.userMessage)
    }

    @Test fun liveWithoutMainIsGenericError() {
        val e = expect<CtException> { CtParser.parseLive("CH_1", 200, """{"streamUrls":{"main":null}}""") }
        assertEquals(Messages.GENERIC, e.userMessage)
    }

    @Test fun liveMissingTimeshiftIsNull() {
        assertNull(CtParser.parseLive("CH_1", 200, """{"streamUrls":{"main":"https://x"}}""").timeshift)
    }

    @Test fun liveGarbageBodyDoesNotCrash() {
        expect<CtException> { CtParser.parseLive("CH_1", 200, "<html>") }
        expect<CtException> { CtParser.parseLive("CH_1", 200, "[1,2]") }
        expect<CtException> { CtParser.parseLive("CH_1", 200, """{"streamUrls":"x"}""") }
    }

    @Test fun vodReturnsFirstStreamUrl() {
        assertTrue(vod("vod_ok.json").contains("contentType=vod"))
    }

    @Test fun vodSkipsEmptyStreamUrls() {
        val body = """{"streams":[{"url":""},{"url":null},{"url":"https://b"}]}"""
        assertEquals("https://b", CtParser.parseVod("1", 200, body))
    }

    @Test fun vodStillLiveNamesTheChannel() {
        val e = expect<StillLiveException> { vod("vod_still_live.json") }
        assertEquals("CH_24", e.channelId)
    }

    @Test fun vodNoLicenceUsesCtText() {
        val e = expect<CtException> { vod("vod_no_licence.json") }
        assertEquals("ČT nemá práva pro\u00A0internet", e.userMessage)
        assertEquals("MISSING_LICENCE_NOW", e.status)
    }

    @Test fun vodUnsupportedDrmAddsHintToCtText() {
        val body = """{"message":{"status":{"name":"UNSUPPORTED_DRM"},"labels":{"textShort":"Video vyžaduje DRM"}}}"""
        val e = expect<CtException> { CtParser.parseVod("1", 403, body) }
        assertEquals("Video vyžaduje DRM\n${Messages.DRM_HINT}", e.userMessage)
        assertEquals("UNSUPPORTED_DRM", e.status)
    }

    @Test fun vodDrmFixtureShowsCtTextAndOfficialAppHint() {
        val e = expect<CtException> { vod("vod_drm.json") }
        assertEquals("Video vyžaduje prohlížeč s\u00A0ochranou DRM\n${Messages.DRM_HINT}", e.userMessage)
        assertEquals("UNSUPPORTED_DRM", e.status)
    }

    @Test fun vodNotFoundUsesCtText() {
        val e = expect<CtException> { vod("vod_not_found.json") }
        assertEquals("Video není k\u00A0dispozici", e.userMessage)
    }

    @Test fun vodNonJsonErrorIsGenericUnavailable() {
        val e = expect<CtException> { CtParser.parseVod("1", 500, "Bad gateway") }
        assertEquals(Messages.VIDEO_UNAVAILABLE, e.userMessage)
    }

    @Test fun vodEmptyOkIsUnavailable() {
        val e = expect<CtException> { CtParser.parseVod("1", 200, "{}") }
        assertEquals(Messages.VIDEO_UNAVAILABLE, e.userMessage)
    }

    @Test fun scheduleParsesAllChannelsAndFields() {
        val r = Fixtures.response("schedule_ct1_ct24.json")
        val plan = CtParser.parseSchedule(r.status, r.body)
        assertEquals(setOf("ct1", "ct24"), plan.keys)
        assertEquals(6, plan.getValue("ct1").size)
        assertEquals(6, plan.getValue("ct24").size)
        val second = plan.getValue("ct1")[1]
        assertEquals("224381492050002", second.idec)
        assertEquals("Testovací pořad 3", second.title)
        assertEquals("Testovací pořad 4", second.episodeTitle)
        assertEquals("Seriály", second.genre)
        assertEquals(Instant.parse("2026-10-16T07:00:00Z"), second.start)
        assertEquals(Instant.parse("2026-10-16T07:51:00Z"), second.end)
        assertFalse(second.isPlayableNow)
        assertTrue(second.imageUrl.startsWith("https://"))
    }

    @Test fun scheduleSkipsMalformedEntries() {
        val body = """{"data":{"TVProgramDailyChannelsPlanV2":[{"channel":"ct1","program":[
            {"idec":"1","title":"ok","start":"2026-10-16T07:00:00.000Z","end":"2026-10-16T08:00:00.000Z"},
            {"idec":"2","title":"no end","start":"2026-10-16T08:00:00.000Z"},
            {"idec":"3","start":"yesterday","end":"2026-10-16T08:00:00.000Z"},
            "garbage", null,
            {"title":"no idec","start":"2026-10-16T08:00:00.000Z","end":"2026-10-16T09:00:00.000Z"}
        ]},{"program":[]},{"channel":"ct2","program":null}]}}"""
        val plan = CtParser.parseSchedule(200, body)
        assertEquals(listOf("1"), plan.getValue("ct1").map { it.idec })
        assertEquals(emptyList<Programme>(), plan.getValue("ct2"))
        assertEquals("", plan.getValue("ct1")[0].genre)
    }

    @Test fun scheduleGraphqlErrorFails() {
        val r = Fixtures.response("graphql_error.json")
        val e = expect<CtException> { CtParser.parseSchedule(r.status, r.body) }
        assertEquals(Messages.SCHEDULE_FAILED, e.userMessage)
    }

    @Test fun scheduleHttpErrorFails() {
        val e = expect<CtException> { CtParser.parseSchedule(502, "oops") }
        assertEquals(Messages.SCHEDULE_FAILED, e.userMessage)
    }
}
