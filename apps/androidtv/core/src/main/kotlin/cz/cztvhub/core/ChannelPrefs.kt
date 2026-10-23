package cz.cztvhub.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/** A group's channels as Živě, the guide ([visible]) and the settings list ([all]) show them. */
data class Arranged(val all: List<String>, val visible: List<String>)

/**
 * "Kanály": hidden channels and their order, on this device only (spec/apps.md). Keys are
 * `ct:<API id>` and `dvb:<gateway channel id>`; golden cases in spec/fixtures/channel_prefs_cases.json.
 * Unknown keys are kept, so an Anténa channel switched off on the gateway keeps its place.
 */
data class ChannelPrefs(val order: List<String> = emptyList(), val hidden: Set<String> = emptySet()) {
    /** [channels] = one group's keys in default order. */
    fun arrange(channels: List<String>): Arranged {
        val inGroup = channels.toSet()
        val all = LinkedHashSet<String>()
        order.filterTo(all) { it in inGroup }
        all += channels
        val list = all.toList()
        return Arranged(list, list.filter { it !in hidden })
    }

    /** Swaps [key] with its neighbour ([by] = −1 up, +1 down) within the group; no-op at the edge. */
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
        ),
    ).toString()

    companion object {
        const val MAX_KEYS = 500
        val DEFAULT = ChannelPrefs()

        fun ctKey(channelId: String): String = "ct:$channelId"

        fun dvbKey(channelId: String): String = "dvb:$channelId"

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
            return ChannelPrefs(keys("order"), keys("hidden").toSet())
        }
    }
}
