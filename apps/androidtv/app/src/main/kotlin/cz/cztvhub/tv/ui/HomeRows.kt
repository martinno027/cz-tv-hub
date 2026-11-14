package cz.cztvhub.tv.ui

import cz.cztvhub.core.PspState

/** The gateway line on Domů (spec/apps.md "Domů"). */
enum class GatewayNotice {
    /** Tailscale in the app is still connecting: its own "Připojuji se…" text. */
    CONNECTING,
    UNAVAILABLE,
    /** `/dvb/status` answered `401`: the key in Nastavení → Server is wrong. */
    KEY_REJECTED,
    RECORDING_PAUSED,
}

/** What Domů's rows show, from data Živě already has (spec/apps.md "Domů", T58). */
object HomeRows {
    /** "Teď běží" lists the first this many visible channels. */
    const val NOW_CHANNELS = 10

    /** A channel of a row with its number in "Kanály" (1-based position in `visible`). */
    data class Numbered(val number: Int, val channel: ListedChannel)

    /**
     * "Teď běží": the first [NOW_CHANNELS] visible ČT and Anténa channels, in the viewer's order; the Chamber streams
     * are only in "Sněmovna". Numbers stay those of "Kanály".
     */
    fun nowOn(visible: List<ListedChannel>): List<Numbered> =
        visible.mapIndexedNotNull { index, channel -> Numbered(index + 1, channel).takeIf { channel.psp == null } }
            .take(NOW_CHANNELS)

    /** "Sněmovna": the visible Chamber streams that are live now; empty = the block is left out. */
    fun pspLive(visible: List<ListedChannel>, states: Map<String, PspState>): List<Numbered> =
        visible.mapIndexedNotNull { index, channel ->
            val stream = channel.psp ?: return@mapIndexedNotNull null
            Numbered(index + 1, channel).takeIf { states[stream.id] == PspState.LIVE }
        }

    /**
     * Only with a gateway ([dvb] non-null): `/dvb/status` refused the key (`401`) or failed otherwise, else recording
     * paused; null = no line. While the
     * Tailscale node is still connecting ([connecting]) its state is shown instead of "unavailable".
     */
    fun notice(dvb: DvbState?, connecting: Boolean): GatewayNotice? = when {
        dvb == null -> null
        connecting -> GatewayNotice.CONNECTING
        dvb.statusFailed -> if (dvb.statusRejected) GatewayNotice.KEY_REJECTED else GatewayNotice.UNAVAILABLE
        dvb.recordingPaused -> GatewayNotice.RECORDING_PAUSED
        else -> null
    }

    /** Up/down between rows keeps the column as far as the target row allows. */
    fun column(index: Int, rowSize: Int): Int = index.coerceAtMost(rowSize - 1).coerceAtLeast(0)
}
