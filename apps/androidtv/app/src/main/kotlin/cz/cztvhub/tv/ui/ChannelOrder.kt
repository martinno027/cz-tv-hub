package cz.cztvhub.tv.ui

import cz.cztvhub.core.Channel
import cz.cztvhub.core.ChannelPrefs
import cz.cztvhub.core.Channels
import cz.cztvhub.core.dvb.DvbChannel

/** "Kanály" applied to the two groups: what Živě and the guide show, in the viewer's order. */
object ChannelOrder {
    val ctKeys: List<String> get() = Channels.all.map { ChannelPrefs.ctKey(it.id) }

    fun dvbKeys(channels: List<DvbChannel>): List<String> = channels.map { ChannelPrefs.dvbKey(it.id) }

    fun visibleCt(prefs: ChannelPrefs): List<Channel> {
        val byKey = Channels.all.associateBy { ChannelPrefs.ctKey(it.id) }
        return prefs.arrange(ctKeys).visible.mapNotNull(byKey::get)
    }

    fun visibleDvb(prefs: ChannelPrefs, channels: List<DvbChannel>): List<DvbChannel> {
        val byKey = channels.associateBy { ChannelPrefs.dvbKey(it.id) }
        return prefs.arrange(dvbKeys(channels)).visible.mapNotNull(byKey::get)
    }
}
