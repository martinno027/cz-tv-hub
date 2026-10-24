package cz.cztvhub.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/** The channels as Živě, the guide ([visible]) and the settings list ([all]) show them. */
data class Arranged(val all: List<String>, val visible: List<String>)

/**
 * "Kanály": hidden channels, their order (one list mixing CT internet, Anténa and Poslanecká sněmovna channels) and
 * the sources switched off, on this device only (spec/apps.md). Keys are `ct:<API id>`, `dvb:<gateway channel id>`
 * and `psp:<stream id>`; golden cases in spec/fixtures/channel_prefs_cases.json and psp_cases.json. Unknown keys are kept, so an Anténa channel switched off
 * on the gateway (or the channels of a source that is off) keep their place.
 */
data class ChannelPrefs(
    val order: List<String> = emptyList(),
    val hidden: Set<String> = emptySet(),
    val sourcesOff: Set<String> = emptySet(),
) {
    fun isSourceOn(source: String): Boolean = source !in sourcesOff

    fun toggleSource(source: String): ChannelPrefs =
        copy(sourcesOff = if (source in sourcesOff) sourcesOff - source else sourcesOff + source)

    /** The input list for [arrange]: the channels of the sources that are on, CT first, then Anténa, then psp. */
    fun sourceKeys(ct: List<String>, dvb: List<String>, psp: List<String> = emptyList()): List<String> =
        (if (isSourceOn(SOURCE_CT)) ct else emptyList()) + (if (isSourceOn(SOURCE_DVB)) dvb else emptyList()) +
            (if (isSourceOn(SOURCE_PSP)) psp else emptyList())

    /** The sources that are on, in default order. */
    val sourcesOn: List<String> get() = SOURCES.filter(::isSourceOn)

    /** [channels] = the listed keys in default order (already filtered by [sourceKeys]). */
    fun arrange(channels: List<String>): Arranged {
        val inGroup = channels.toSet()
        val all = LinkedHashSet<String>()
        order.filterTo(all) { it in inGroup }
        all += channels
        val list = all.toList()
        return Arranged(list, list.filter { it !in hidden })
    }

    /** Swaps [key] with its neighbour ([by] = −1 up, +1 down) in the list; no-op at the edge. */
    fun move(key: String, by: Int, channels: List<String>): ChannelPrefs {
        val all = arrange(channels).all.toMutableList()
        val i = all.indexOf(key)
        val j = i + by
        if (i < 0 || j !in all.indices) return this
        all[i] = all[j].also { all[j] = all[i] }
        val inGroup = all.toSet()
        return copy(order = all + order.filter { it !in inGroup })
    }

    fun toggle(key: String): ChannelPrefs = copy(hidden = if (key in hidden) hidden - key else hidden + key)

    fun isHidden(key: String): Boolean = key in hidden

    fun encode(): String = JsonObject(
        mapOf(
            "order" to JsonArray(order.distinct().take(MAX_KEYS).map(::JsonPrimitive)),
            "hidden" to JsonArray(hidden.take(MAX_KEYS).map(::JsonPrimitive)),
            "sourcesOff" to JsonArray(sourcesOff.filter { it in SOURCES }.map(::JsonPrimitive)),
        ),
    ).toString()

    companion object {
        const val MAX_KEYS = 500
        const val SOURCE_CT = "ct"
        const val SOURCE_DVB = "dvb"
        const val SOURCE_PSP = "psp"
        private val SOURCES = listOf(SOURCE_CT, SOURCE_DVB, SOURCE_PSP)

        /** Reset ("Obnovit výchozí pořadí") clears order and hidden and switches every source on. */
        val DEFAULT = ChannelPrefs()

        fun ctKey(channelId: String): String = "ct:$channelId"

        fun dvbKey(channelId: String): String = "dvb:$channelId"

        fun pspKey(streamId: String): String = "psp:$streamId"

        /** Anything unreadable gives the defaults. */
        fun decode(text: String?): ChannelPrefs {
            if (text.isNullOrBlank()) return DEFAULT
            val root = try {
                Json.parseToJsonElement(text).jsonObject
            } catch (_: Exception) {
                return DEFAULT
            }
            fun keys(name: String): List<String> =
                (root[name] as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.contentOrNull }.take(MAX_KEYS)
            return ChannelPrefs(keys("order"), keys("hidden").toSet(), keys("sourcesOff").filter { it in SOURCES }.toSet())
        }
    }
}
