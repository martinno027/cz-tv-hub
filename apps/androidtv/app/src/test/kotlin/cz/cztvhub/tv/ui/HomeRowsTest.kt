package cz.cztvhub.tv.ui

import cz.cztvhub.core.ChannelPrefs
import cz.cztvhub.core.PspState
import cz.cztvhub.core.dvb.DvbChannel
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Domů's rows from what Živě already has (spec/apps.md "Domů", T58); no network. */
class HomeRowsTest {
    private val antenna = (1..8).map { DvbChannel("d$it", it, "Anténa $it", null) }

    @Test fun nowOnIsTheFirstTenVisibleChannelsNumberedAsEverywhere() {
        val prefs = ChannelPrefs(hidden = setOf("ct:CH_2"))
        val visible = ChannelOrder.visible(prefs, antenna)
        val row = HomeRows.nowOn(visible)
        assertTrue(visible.size > HomeRows.NOW_CHANNELS)
        assertEquals(10, row.size)
        assertEquals(visible.take(10).map { it.key }, row.map { it.channel.key })
        assertEquals((1..10).toList(), row.map { it.number })
        // The hidden channel is not there, and the next one closes up.
        assertTrue(row.none { it.channel.key == "ct:CH_2" })
        assertEquals("ct:CH_24", row[1].channel.key)
        // Fewer channels: all of them.
        assertEquals(6, HomeRows.nowOn(ChannelOrder.visible(ChannelPrefs(sourcesOff = setOf("dvb", "psp")), antenna)).size)
    }

    @Test fun nowOnLeavesOutTheChamberStreamsAndKeepsTheNumbers() {
        // A stream moved to the front: it is not in "Teď běží", the channels after it keep their numbers.
        val prefs = ChannelPrefs(order = listOf("psp:stream2", "ct:CH_1"))
        val visible = ChannelOrder.visible(prefs, antenna)
        assertEquals("psp:stream2", visible.first().key)
        val row = HomeRows.nowOn(visible)
        assertEquals(10, row.size)
        assertTrue(row.none { it.channel.psp != null })
        assertEquals("ct:CH_1", row.first().channel.key)
        assertEquals(2, row.first().number)
        // Anténa off: only the six ČT channels, the five streams go to "Sněmovna" only.
        val noAntenna = ChannelOrder.visible(ChannelPrefs(sourcesOff = setOf("dvb")), antenna)
        assertEquals(11, noAntenna.size)
        assertEquals((1..6).toList(), HomeRows.nowOn(noAntenna).map { it.number })
    }

    @Test fun pspLiveListsOnlyLiveVisibleStreamsWithTheirNumbers() {
        val prefs = ChannelPrefs(sourcesOff = setOf("dvb"), hidden = setOf("psp:stream3"))
        val visible = ChannelOrder.visible(prefs, antenna)
        val states = mapOf("stream1" to PspState.LIVE, "stream2" to PspState.OFF, "stream3" to PspState.LIVE,
            "stream4" to PspState.UNKNOWN, "stream5" to PspState.LIVE)
        val live = HomeRows.pspLive(visible, states)
        // stream3 is live but hidden; off and unknown are left out.
        assertEquals(listOf("psp:stream1", "psp:stream5"), live.map { it.channel.key })
        assertEquals(listOf(7, 10), live.map { it.number })
        assertTrue(HomeRows.pspLive(visible, emptyMap()).isEmpty())
    }

    @Test fun gatewayNotice() {
        assertNull(HomeRows.notice(null, connecting = false))
        assertNull(HomeRows.notice(DvbState(), connecting = false))
        assertEquals(GatewayNotice.UNAVAILABLE, HomeRows.notice(DvbState(statusFailed = true), connecting = false))
        assertEquals(GatewayNotice.RECORDING_PAUSED, HomeRows.notice(DvbState(recordingPaused = true), connecting = false))
        // A refused key (401) says where to fix it, also over an earlier pause; a 401 is a failed status too.
        assertEquals(GatewayNotice.KEY_REJECTED,
            HomeRows.notice(DvbState(statusFailed = true, statusRejected = true, recordingPaused = true), connecting = false))
        // Unreachable wins over a pause the gateway said earlier.
        assertEquals(GatewayNotice.UNAVAILABLE,
            HomeRows.notice(DvbState(statusFailed = true, recordingPaused = true), connecting = false))
        // Tailscale still connecting: its state instead of "unavailable"; nothing without a gateway.
        assertEquals(GatewayNotice.CONNECTING, HomeRows.notice(DvbState(statusFailed = true), connecting = true))
        assertEquals(GatewayNotice.CONNECTING, HomeRows.notice(DvbState(), connecting = true))
        assertNull(HomeRows.notice(null, connecting = true))
    }

    @Test fun columnIsKeptAsFarAsTheRowAllows() {
        assertEquals(3, HomeRows.column(3, 10))
        assertEquals(4, HomeRows.column(9, 5))
        assertEquals(0, HomeRows.column(0, 1))
    }
}
