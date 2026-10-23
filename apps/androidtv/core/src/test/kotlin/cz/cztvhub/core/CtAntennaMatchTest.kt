package cz.cztvhub.core

import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Test

/** spec/fixtures/ct_antenna_match.json: every case. */
class CtAntennaMatchTest {
    private val root = Fixtures.json("ct_antenna_match.json")

    @Test
    fun channelSlugs() {
        for (c in root.getValue("channels").jsonArray.map { it.jsonObject }) {
            val name = c.getValue("name").jsonPrimitive.content
            val slugs = c.getValue("slugs").jsonArray.map { it.jsonPrimitive.content }
            assertEquals(name, slugs, CtAntennaMatch.slugs(name))
        }
    }

    @Test
    fun programmes() {
        val programmes = root.getValue("programmes").jsonObject
        val schedule = programmes.getValue("schedule").jsonArray.map { it.jsonObject }.groupBy(
            { it.getValue("slug").jsonPrimitive.content },
            {
                Programme(
                    idec = it.getValue("idec").jsonPrimitive.content,
                    title = it.getValue("title").jsonPrimitive.content,
                    start = Instant.parse(it.getValue("start").jsonPrimitive.content),
                    end = Instant.parse(it.getValue("end").jsonPrimitive.content),
                    isPlayableNow = false,
                    imageUrl = it.getValue("imageUrl").jsonPrimitive.content,
                )
            },
        )
        for (c in programmes.getValue("cases").jsonArray.map { it.jsonObject }) {
            val got = CtAntennaMatch.programme(
                c.getValue("channel").jsonPrimitive.content,
                c.getValue("title").jsonPrimitive.content,
                Instant.parse(c.getValue("start").jsonPrimitive.content),
                Instant.parse(c.getValue("end").jsonPrimitive.content),
                schedule,
            )
            val want = c.getValue("idec").let { if (it is JsonNull) null else it.jsonPrimitive.content }
            assertEquals(c.getValue("name").jsonPrimitive.content, want, got?.idec)
        }
    }
}
