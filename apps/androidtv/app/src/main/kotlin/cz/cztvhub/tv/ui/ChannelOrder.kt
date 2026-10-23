package cz.cztvhub.tv.ui

import cz.cztvhub.core.Channel
import cz.cztvhub.core.ChannelPrefs
import cz.cztvhub.core.Channels
import cz.cztvhub.core.dvb.DvbChannel

/** A channel of the mixed list: a CT internet channel ([ct]) or an Anténa channel ([dvb]). */
data class ListedChannel(val key: String, val name: String, val ct: Channel? = null, val dvb: DvbChannel? = null) {
    val isAntenna: Boolean get() = dvb != null
}

/**
 * "Kanály" applied (spec/apps.md): one list mixing CT internet and Anténa channels in the viewer's order, without
 * the channels of a source that is switched off. Živě and the guide show [visible], the settings list [all].
 */
object ChannelOrder {
    /** The input list for [ChannelPrefs.arrange] and [ChannelPrefs.move]: sources that are on, CT first. */
    fun keys(prefs: ChannelPrefs, dvbChannels: List<DvbChannel>): List<String> =
        prefs.sourceKeys(Channels.all.map { ChannelPrefs.ctKey(it.id) }, dvbChannels.map { ChannelPrefs.dvbKey(it.id) })

    fun all(prefs: ChannelPrefs, dvbChannels: List<DvbChannel>): List<ListedChannel> =
        listed(prefs.arrange(keys(prefs, dvbChannels)).all, dvbChannels)

    fun visible(prefs: ChannelPrefs, dvbChannels: List<DvbChannel>): List<ListedChannel> =
        listed(prefs.arrange(keys(prefs, dvbChannels)).visible, dvbChannels)

    private fun listed(keys: List<String>, dvbChannels: List<DvbChannel>): List<ListedChannel> {
        val byKey = Channels.all.associate { ChannelPrefs.ctKey(it.id) to ListedChannel(ChannelPrefs.ctKey(it.id), it.name, ct = it) } +
            dvbChannels.associate { ChannelPrefs.dvbKey(it.id) to ListedChannel(ChannelPrefs.dvbKey(it.id), it.name, dvb = it) }
        return keys.mapNotNull(byKey::get)
    }
}
