package cz.cztvhub.core

import java.time.Duration
import java.time.Instant
import java.time.LocalDate

/**
 * In-memory schedule cache, one entry per broadcast day (all channels in one request).
 * The broadcast day on air (`Days.openDay`, also after midnight) and later days are refetched after [freshFor];
 * earlier days never change.
 * A failed day is not cached, so it can be retried without breaking the other days.
 */
class ScheduleRepository(
    private val api: CtApi,
    private val channels: List<Channel> = Channels.all,
    private val clock: () -> Instant = Instant::now,
    private val freshFor: Duration = Duration.ofHours(1),
) {
    private data class Entry(val fetchedAt: Instant, val plan: Map<String, List<Programme>>)

    private val cache = HashMap<LocalDate, Entry>()

    /** Programmes of [day] keyed by channel id, sorted by start. Throws [CtException]. */
    fun day(day: LocalDate): Map<String, List<Programme>> {
        val now = clock()
        synchronized(cache) {
            cache[day]?.let { entry ->
                // The broadcast day on air is current too: after midnight it is the previous date.
                val past = day.isBefore(Days.openDay(now))
                if (past || Duration.between(entry.fetchedAt, now) < freshFor) return entry.plan
            }
        }
        val bySlug = api.schedules(channels.map { it.slug }, day)
        val plan = channels.associate { ch -> ch.id to bySlug[ch.slug].orEmpty().sortedBy { it.start } }
        synchronized(cache) { cache[day] = Entry(now, plan) }
        return plan
    }

    /**
     * Programme airing now (and the next one) on each channel. Programmes after midnight belong
     * to the previous broadcast day, so yesterday's plan is consulted when today's has nothing on air.
     */
    fun onAir(now: Instant = clock()): Map<String, OnAir> {
        val today = Days.today(now)
        val result = HashMap<String, OnAir>()
        var lastError: CtException? = null
        for (day in listOf(today, today.minusDays(1))) {
            if (channels.all { result[it.id]?.current != null }) break
            val plan = try {
                day(day)
            } catch (e: CtException) {
                lastError = e
                continue
            }
            for (ch in channels) {
                if (result[ch.id]?.current != null) continue
                val list = plan[ch.id].orEmpty()
                val index = list.indexOfFirst { it.isAiring(now) }
                result[ch.id] = when {
                    index >= 0 -> OnAir(list[index], list.getOrNull(index + 1))
                    // A gap between programmes: still tell what comes next.
                    else -> result[ch.id] ?: OnAir(null, list.firstOrNull { it.start.isAfter(now) })
                }
            }
        }
        if (result.isEmpty() && lastError != null) throw lastError
        return result
    }
}

/** What a channel is showing now and what follows; either may be unknown. */
data class OnAirLike<T>(val current: T?, val next: T?)

typealias OnAir = OnAirLike<Programme>

/** Live stream URLs reused in memory for at most [ttl] (signed URLs live ~1 h). */
class LiveResolver(
    private val api: CtApi,
    private val clock: () -> Instant = Instant::now,
    private val ttl: Duration = Duration.ofSeconds(60),
) {
    private val cache = HashMap<String, Pair<Instant, LiveStreams>>()

    fun resolve(channelId: String, forceRefresh: Boolean = false): LiveStreams {
        val now = clock()
        if (!forceRefresh) {
            synchronized(cache) {
                cache[channelId]?.let { (at, streams) -> if (Duration.between(at, now) < ttl) return streams }
            }
        }
        val streams = api.liveStreams(channelId)
        synchronized(cache) { cache[channelId] = now to streams }
        return streams
    }
}
