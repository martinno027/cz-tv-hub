package cz.cztvhub.tv.ui

import cz.cztvhub.core.ChannelPrefs
import cz.cztvhub.core.GuideLayout
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.PspMessages
import cz.cztvhub.core.PspState
import cz.cztvhub.core.PspStreams
import cz.cztvhub.tv.ZapChannel
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.Instant
import java.time.LocalDate

/** Poslanecká sněmovna in the guide, Živě and zapping (spec/apps.md); no network. */
class PspEntryTest {
    private val now = Instant.parse("2026-10-24T08:00:00Z") // 10:00 in Prague, broadcast day 24 Oct
    private val stream = PspStreams.byId("stream1")!!

    private fun entry(day: LocalDate): PspEntry {
        val (start, end) = GuideLayout.axis(day)
        return PspEntry(stream, start, end)
    }

    @Test fun todayFollowsTheState() {
        val today = entry(LocalDate.of(2026, 10, 24))
        assertEquals(EntryLook.AIRING, today.look(now, PspState.LIVE))
        assertEquals(EntryLook.DIMMED, today.look(now, PspState.OFF))
        assertEquals(EntryLook.PLAYABLE, today.look(now, PspState.UNKNOWN))
        assertEquals(PspMessages.LIVE, today.cellTitle(now, PspState.LIVE))
        assertEquals(PspMessages.OFF, today.cellTitle(now, PspState.OFF))
        assertEquals(PspMessages.CHECKING, today.cellTitle(now, PspState.UNKNOWN))

        val play = today.select(now, psp = PspState.LIVE) as Selection.Play
        assertEquals(PlayRequest.PspLive("stream1", "Schůze PS"), play.request)
        // Not checked yet: try to play.
        assertEquals(play, today.select(now, psp = PspState.UNKNOWN))
        assertEquals(Selection.Notice(PspMessages.NOT_ON), today.select(now, psp = PspState.OFF))
        // Live only: never a "Pokračovat" offer.
        assertEquals(play, today.select(now, { 120.0 }, PspState.LIVE))
    }

    @Test fun otherDaysAreLiveOnly() {
        for (day in listOf(LocalDate.of(2026, 10, 23), LocalDate.of(2026, 10, 25))) {
            val other = entry(day)
            assertEquals(EntryLook.DIMMED, other.look(now, PspState.LIVE))
            assertEquals("Jen přímé přenosy", other.cellTitle(now, PspState.LIVE))
            assertEquals(
                Selection.Notice("Poslanecká sněmovna: jen přímé přenosy, bez záznamů."),
                other.select(now, psp = PspState.LIVE),
            )
        }
    }

    @Test fun zapKeysRoundTrip() {
        val zap = ZapChannel(stream.key, stream.name)
        assertEquals(PlayRequest.PspLive("stream1", "Schůze PS"), zap.liveRequest())
        assertEquals("psp:stream1", ZapChannel.keyOf(PlayRequest.PspLive("stream1", "x")))
        assertEquals(ChannelSource.PSP, ChannelSource.ofKey(stream.key))
        assertEquals(ChannelSource.DVB, ChannelSource.ofKey("dvb:a"))
        assertEquals(ChannelSource.CT, ChannelSource.ofKey("CH_1"))
    }

    @Test fun channelOrderAppendsStreamsAndHonoursTheSwitch() {
        val keys = ChannelOrder.visible(ChannelPrefs.DEFAULT, emptyList()).map { it.key }
        assertEquals(PspStreams.all.map { it.key }, keys.takeLast(5))
        assertEquals("ct:CH_1", keys.first())
        val off = ChannelPrefs.DEFAULT.toggleSource(ChannelPrefs.SOURCE_PSP)
        assertNull(ChannelOrder.visible(off, emptyList()).firstOrNull { it.psp != null })
    }
}
