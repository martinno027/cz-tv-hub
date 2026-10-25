package cz.cztvhub.core

import kotlinx.serialization.json.double
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Duration
import java.time.Instant

class ResumeTest {
    private val root = Fixtures.json("resume_cases.json")

    @Test fun constantsMatchGoldenFile() {
        assertEquals(ResumeRules.MIN_SECONDS, root.getValue("minSeconds").jsonPrimitive.double, 0.0)
        assertEquals(ResumeRules.END_MARGIN_SECONDS, root.getValue("endMarginSeconds").jsonPrimitive.double, 0.0)
        assertEquals(ResumeRules.MAX_ENTRIES, root.getValue("maxEntries").jsonPrimitive.int)
        assertEquals(ResumeRules.MAX_AGE_DAYS, root.getValue("maxAgeDays").jsonPrimitive.long)
    }

    @Test fun goldenOfferCases() {
        val cases = root.getValue("offer").jsonArray
        assertTrue(cases.size >= 9)
        for (case in cases) {
            val c = case.jsonObject
            val progress = c.getValue("progress").jsonPrimitive.double
            val length = c.getValue("length").jsonPrimitive.double
            val expected = ResumeAction.valueOf(c.getValue("action").jsonPrimitive.content.uppercase())
            assertEquals("$c", expected, ResumeRules.action(progress, length))
            c["label"]?.let { assertEquals("$c", it.jsonPrimitive.content, ResumeRules.label(progress)) }
        }
    }

    @Test fun goldenStartoverCases() {
        val cases = root.getValue("startover").jsonArray
        assertTrue(cases.size >= 3)
        for (case in cases) {
            val c = case.jsonObject
            val name = c.getValue("name").jsonPrimitive.content
            val now = Instant.parse(c.getValue("now").jsonPrimitive.content)
            val start = Instant.parse(c.getValue("start").jsonPrimitive.content)
            val progress = c.getValue("progress").jsonPrimitive.double
            val expect = c.getValue("expect").jsonObject
            val transport = FakeTransport(mapOf("channel/CH_1" to "live_CH_1.json"))
            val api = CtApi(transport)
            val planner = Planner(api, LiveResolver(api, clock = { now }), clock = { now })
            val target = planner.plan(
                PlayRequest.Startover("CH_1", "x", start, "226411010101016", start.plusSeconds(3600), resumeSeconds = progress),
            )
            assertEquals(name, PlayKind.TIMESHIFT, target.kind)
            assertEquals(name, Instant.parse(expect.getValue("seekToWallClock").jsonPrimitive.content), target.seekToWallClock)
            assertEquals(name, expect.getValue("fallbackOffsetSeconds").jsonPrimitive.long * 1000, target.fallbackOffsetMs)
        }
    }

    @Test fun keysAndSpots() {
        val start = Instant.parse("2026-10-17T16:00:00Z")
        val end = start.plusSeconds(3600)
        assertEquals(
            ResumeSpot("ct:123", start, 3600.0),
            ResumeRules.spot(PlayRequest.Programme("CH_1", "x", "123", start, end, true)),
        )
        assertEquals(
            ResumeSpot("ct:123", start, 3600.0),
            ResumeRules.spot(PlayRequest.Startover("CH_1", "x", start, "123", end)),
        )
        assertNull(ResumeRules.spot(PlayRequest.Startover("CH_1", "x", start)))
        assertEquals(
            ResumeSpot("dvb:r1", null, 1800.0),
            ResumeRules.spot(PlayRequest.DvbRecorded("c1", "x", "r1", false, lengthSeconds = 1800.0)),
        )
        assertNull(ResumeRules.spot(PlayRequest.Live("CH_1", "x")))
        assertNull(ResumeRules.spot(PlayRequest.DvbLive("c1", "x")))
    }

    private var now = Instant.parse("2026-10-18T12:00:00Z")
    private val backend = MemoryBackend()
    private val store = ResumeStore(backend) { now }

    @Test fun storeOffersOnlyWithinRules() {
        store.save("ct:1", 1390.0, 3600.0)
        assertEquals(1390.0, store.offer("ct:1")!!, 0.0)
        assertNull(store.offer("ct:2"))
        store.save("ct:3", 30.0, 3600.0)
        assertNull("below the minimum nothing is stored", backend.values["ct:3"])
    }

    @Test fun watchedClearsAndShortProgressKeepsEntry() {
        store.save("ct:1", 1390.0, 3600.0)
        store.save("ct:1", 20.0, 3600.0)
        assertEquals(1390.0, store.offer("ct:1")!!, 0.0)
        store.save("ct:1", 3480.0, 3600.0)
        assertNull(backend.values["ct:1"])
        assertNull(store.offer("ct:1"))
    }

    @Test fun entriesOlderThan30DaysAreDropped() {
        store.save("dvb:old", 600.0, 3600.0)
        now = now.plus(Duration.ofDays(30)).plusSeconds(1)
        assertNull(store.offer("dvb:old"))
        store.save("dvb:new", 600.0, 3600.0)
        assertEquals(setOf("dvb:new"), backend.values.keys)
    }

    @Test fun atMost200NewestEntriesAreKept() {
        for (i in 0 until 205) {
            store.save("ct:$i", 600.0, 3600.0)
            now = now.plusSeconds(1)
        }
        assertEquals(200, backend.values.size)
        assertNull(store.offer("ct:0"))
        assertNull(store.offer("ct:4"))
        assertEquals(600.0, store.offer("ct:5")!!, 0.0)
        assertEquals(600.0, store.offer("ct:204")!!, 0.0)
    }

    @Test fun unreadableEntriesAreIgnoredAndPruned() {
        backend.put("ct:bad", "nonsense")
        assertNull(store.offer("ct:bad"))
        store.save("ct:1", 600.0, 3600.0)
        assertEquals(setOf("ct:1"), backend.values.keys)
    }

    @Test fun entryRoundTrips() {
        val entry = ResumeEntry(1390.5, 3600.0, Instant.ofEpochMilli(1_760_000_000_123))
        assertEquals(entry, ResumeEntry.decode(entry.encode()))
    }

    // -- T13: the card kept for "Pokračovat ve sledování" ----------------------------------------------------

    private val card = ResumeCard(
        "1/26 Od začátku; díl", "Most!", "/dvb/details/image/w342/a.jpg", "ct", Instant.ofEpochMilli(1_760_000_000_000), "CH_1",
    )

    @Test fun entryWithCardRoundTrips() {
        val entry = ResumeEntry(1390.5, 3600.0, Instant.ofEpochMilli(1_760_000_000_123), card)
        assertTrue("a card is stored as JSON (titles may hold ';')", entry.encode().startsWith("{"))
        assertEquals(entry, ResumeEntry.decode(entry.encode()))
        val minimal = entry.copy(card = ResumeCard("A", source = "dvb"))
        assertEquals(minimal, ResumeEntry.decode(minimal.encode()))
        assertNull("a card without a title is none", ResumeEntry.decode(minimal.encode().replace("\"A\"", "\"\""))?.card)
        val bare = ResumeEntry(1390.5, 3600.0, Instant.ofEpochMilli(1_760_000_000_123))
        assertEquals("1390.5;3600.0;1760000000123", bare.encode())
    }

    @Test fun oldEntriesStillOfferAndAreLeftOutOfTheRow() {
        backend.put("ct:old", "600.0;3600.0;${now.toEpochMilli()}")
        assertEquals(600.0, store.offer("ct:old")!!, 0.0)
        assertTrue(store.continueRow().isEmpty())
    }

    @Test fun saveWithoutCardKeepsTheStoredCard() {
        store.save("ct:1", 600.0, 3600.0, card)
        store.save("ct:1", 900.0, 3600.0)
        assertEquals(card, store.entry("ct:1")!!.card)
        assertEquals(900.0, store.offer("ct:1")!!, 0.0)
    }

    @Test fun goldenContinueRow() {
        val cases = Fixtures.json("archive_cases.json").getValue("continueRow").jsonArray
        assertTrue(cases.isNotEmpty())
        for (case in cases) {
            val c = case.jsonObject
            val entries = c.getValue("entries").jsonArray.associate { item ->
                val e = item.jsonObject
                val title = e["title"]?.jsonPrimitive?.content
                val key = e.getValue("key").jsonPrimitive.content
                key to ResumeEntry(
                    600.0, 3600.0, Instant.parse(e.getValue("savedAt").jsonPrimitive.content),
                    title?.let { ResumeCard(it, source = ResumeRules.source(key)) },
                )
            }
            val expected = c.getValue("row").jsonArray.map { it.jsonPrimitive.content }
            assertEquals("$c", expected, ResumeRules.continueRow(entries).map { it.key })
        }
    }

    @Test fun continueRowHoldsAtMost20NewestOfferable() {
        for (i in 0 until 25) {
            store.save("ct:$i", 600.0, 3600.0, card)
            now = now.plusSeconds(1)
        }
        val row = store.continueRow()
        assertEquals(ResumeRules.ROW_SIZE, row.size)
        assertEquals("ct:24", row.first().key)
        assertEquals("ct:5", row.last().key)
        store.save("ct:24", 3590.0, 3600.0, card) // watched: removed, the next one moves up
        assertEquals("ct:23", store.continueRow().first().key)
        assertEquals(listOf("ct", "dvb"), listOf(ResumeRules.source("ct:1"), ResumeRules.source("dvb:b1-1-2")))
    }
}
