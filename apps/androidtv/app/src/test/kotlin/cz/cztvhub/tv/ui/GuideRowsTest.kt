package cz.cztvhub.tv.ui

import cz.cztvhub.core.Channels
import cz.cztvhub.core.GuideLayout
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.Programme
import cz.cztvhub.core.PspStream
import cz.cztvhub.core.dvb.DvbChannel
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.Instant
import java.time.LocalDate

/** T38 (spec/apps.md): a guide filler covering now (any filler of a failed ČT row today) plays its row's channel live. */
class GuideRowsTest {
    private val day = LocalDate.parse("2026-10-17")
    private val ct1 = Channels.byId("CH_1")!!
    private val nova = DvbChannel("c9", 9, "Nova HD", null)
    private val psp = PspStream("stream1", "Schůze PS", "https://example.invalid/psp.m3u8")
    private val channels = listOf(
        ListedChannel(key = "ct:CH_1", name = ct1.name, ct = ct1),
        ListedChannel(key = "dvb:c9", name = nova.name, dvb = nova),
        ListedChannel(key = psp.key, name = psp.name, psp = psp),
    )

    /** What selecting the slot under [now] of each row plays, when it is a filler. */
    private fun fillerPlays(rows: List<GuideRow>, now: Instant): List<PlayRequest?> {
        val (axisStart, axisEnd) = GuideLayout.axis(day)
        return rows.map { row ->
            val slots = GuideLayout.row(row.entries, axisStart, axisEnd)
            GuideLayout.fillerPlay(slots[GuideLayout.slotAt(slots, now)!!], row.live, row.filler, axisStart, axisEnd, now)
        }
    }

    @Test fun failedCtDayAndAntennaGapPlayLiveAsTheZiveCards() {
        val now = Instant.parse("2026-10-17T18:00:00Z")
        val rows = guideRows(emptyMap(), ctFailed = true, channels, emptyMap(), day)
        assertEquals(
            listOf(PlayRequest.Live("CH_1", ct1.name), PlayRequest.DvbLive("c9", "Nova HD"), null),
            fillerPlays(rows, now),
        )
    }

    @Test fun ctProgrammeAiringNowIsNotAFiller() {
        val now = Instant.parse("2026-10-17T18:00:00Z")
        val plan = mapOf("CH_1" to listOf(Programme("1", "Zprávy", now.minusSeconds(600), now.plusSeconds(600), true)))
        val rows = guideRows(plan, ctFailed = false, channels, emptyMap(), day)
        assertNull(fillerPlays(rows, now)[0])
        // a gap of a ČT row with a schedule plays live too
        val later = now.plusSeconds(3600)
        assertEquals(PlayRequest.Live("CH_1", ct1.name), fillerPlays(rows, later)[0])
    }
}
