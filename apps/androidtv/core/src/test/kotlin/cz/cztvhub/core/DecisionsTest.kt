package cz.cztvhub.core

import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class DecisionsTest {

    @Test fun goldenCases() {
        val root = Fixtures.json("decisions.json")
        val now = Instant.parse(root.getValue("now").jsonPrimitive.content)
        assertEquals(Decisions.TIMESHIFT_WINDOW.seconds, root.getValue("timeshiftWindowSeconds").jsonPrimitive.long)
        assertEquals(Decisions.START_MARGIN.seconds, root.getValue("startMarginSeconds").jsonPrimitive.long)
        val cases = root.getValue("programmes").jsonArray
        assertTrue(cases.size >= 7)
        for (case in cases) {
            val c = case.jsonObject
            val name = c.getValue("name").jsonPrimitive.content
            val expect = c.getValue("expect").jsonObject
            val decision = Decisions.decide(
                start = Instant.parse(c.getValue("start").jsonPrimitive.content),
                end = Instant.parse(c.getValue("end").jsonPrimitive.content),
                isPlayableNow = c.getValue("isPlayableNow").jsonPrimitive.boolean,
                now = now,
            )
            assertEquals(name, expect.getValue("action").jsonPrimitive.content, decision.action.name.lowercase())
            expect["offsetSeconds"]?.let { assertEquals(name, it.jsonPrimitive.long, decision.offsetSeconds) }
        }
    }

    @Test fun boundaries() {
        val start = Instant.parse("2026-10-17T10:00:00Z")
        val end = Instant.parse("2026-10-17T11:00:00Z")
        assertEquals(Action.STARTOVER, Decisions.decide(start, end, false, start).action)
        assertEquals(Action.VOD_UNAVAILABLE, Decisions.decide(start, end, false, start.plusSeconds(3 * 3600)).action)
        assertEquals(Action.STARTOVER, Decisions.decide(start, end, false, start.plusSeconds(3 * 3600 - 1)).action)
        assertEquals(Action.STARTOVER, Decisions.decide(start, end, true, end).action)
        assertEquals(Action.VOD, Decisions.decide(start, end, true, start.plusSeconds(3 * 3600)).action)
    }
}
