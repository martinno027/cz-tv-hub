package cz.cztvhub.core

import java.text.Normalizer
import java.time.Duration
import java.time.Instant
import java.util.Locale

/**
 * spec/apps.md "ČT images for Anténa ČT channels": which CT programme an Anténa event of a ČT channel is, so the
 * guide can show CT's official image (golden cases: spec/fixtures/ct_antenna_match.json).
 */
object CtAntennaMatch {
    private val DROP = setOf("hd", "sd", "uhd", "4k", "t2", "hevc", "pha", "praha", "jm", "sm", "brno", "ostrava")
    private val SLUGS = mapOf(
        "ct1" to listOf("ct1"),
        "ct2" to listOf("ct2"),
        "ct24" to listOf("ct24"),
        "ctsport" to listOf("sport"),
        "ct:d" to listOf("decko"),
        "ctd" to listOf("decko"),
        "ctart" to listOf("art"),
        "ct:d/art" to listOf("decko", "art"),
        "ctd/art" to listOf("decko", "art"),
    )

    private fun fold(text: String): String =
        Normalizer.normalize(text.lowercase(Locale.ROOT), Normalizer.Form.NFD).replace(Regex("\\p{M}+"), "")

    /** CT schedule slugs of an Anténa channel name; empty for every non-ČT channel. */
    fun slugs(channelName: String): List<String> =
        SLUGS[fold(channelName).split(Regex("\\s+")).filter { it.isNotEmpty() && it !in DROP }.joinToString("")]
            .orEmpty()

    private fun normTitle(title: String) = fold(title).replace(Regex("[^0-9a-z]"), "")

    private fun overlap(aStart: Instant, aEnd: Instant, b: Timed): Duration {
        val from = maxOf(aStart, b.start)
        val to = minOf(aEnd, b.end)
        return if (to.isAfter(from)) Duration.between(from, to) else Duration.ZERO
    }

    /** The CT programme the event is (same title, else ≥ half overlap), from [schedule] keyed by slug. */
    fun programme(
        channelName: String,
        title: String,
        start: Instant,
        end: Instant,
        schedule: Map<String, List<Programme>>,
    ): Programme? {
        val candidates = slugs(channelName).flatMap { schedule[it].orEmpty() }
            .filter { it.end.isAfter(start) && it.start.isBefore(end) }
        if (candidates.isEmpty()) return null
        val wanted = normTitle(title)
        candidates.firstOrNull { normTitle(it.title) == wanted }?.let { return it }
        val best = candidates.maxBy { overlap(start, end, it) }
        return best.takeIf { overlap(start, end, it).multipliedBy(2) >= Duration.between(start, end) }
    }
}
