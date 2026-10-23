package cz.cztvhub.core.dvb

import cz.cztvhub.core.CtException
import cz.cztvhub.core.Days
import cz.cztvhub.core.OnAirLike
import java.time.Duration
import java.time.Instant
import java.time.LocalDate

/** In-memory cache of the gateway's channels and EPG days (recording states change, so short-lived). */
class DvbRepository(
    val client: DvbClient,
    private val clock: () -> Instant = Instant::now,
    private val freshFor: Duration = Duration.ofMinutes(5),
) {
    private var channels: Pair<Instant, List<DvbChannel>>? = null
    private var status: Pair<Instant, DvbStatus>? = null
    private val days = HashMap<LocalDate, Pair<Instant, Map<String, List<DvbEvent>>>>()

    @Synchronized
    fun channels(): List<DvbChannel> {
        val now = clock()
        channels?.let { (at, list) -> if (Duration.between(at, now) < Duration.ofHours(1)) return list }
        return client.channels().also { channels = now to it }
    }

    /** Gateway status, refreshed every 5 minutes (the household switches change rarely). */
    @Synchronized
    fun status(): DvbStatus {
        val now = clock()
        status?.let { (at, value) -> if (Duration.between(at, now) < freshFor) return value }
        return client.status().also { status = now to it }
    }

    /** Events of [day] keyed by channel id, sorted by start, de-duplicated by id. */
    @Synchronized
    fun day(day: LocalDate): Map<String, List<DvbEvent>> {
        val now = clock()
        days[day]?.let { (at, plan) -> if (Duration.between(at, now) < freshFor) return plan }
        val plan = client.epg(day).distinctBy { it.id }.groupBy { it.channelId }
            .mapValues { (_, list) -> list.sortedBy { it.start } }
        days[day] = now to plan
        return plan
    }

    /** Current and next event per channel; the broadcast day starts at 05:00, so yesterday is consulted too. */
    fun onAir(now: Instant = clock()): Map<String, OnAirLike<DvbEvent>> {
        val today = Days.today(now)
        val merged = HashMap<String, MutableList<DvbEvent>>()
        var loaded = false
        var error: CtException? = null
        for (d in listOf(today.minusDays(1), today)) {
            try {
                day(d).forEach { (ch, list) -> merged.getOrPut(ch) { ArrayList() } += list }
                loaded = true
            } catch (e: CtException) {
                error = e
            }
        }
        if (!loaded && error != null) throw error
        return merged.mapValues { (_, list) ->
            val sorted = list.distinctBy { it.id }.sortedBy { it.start }
            val i = sorted.indexOfFirst { it.isAiring(now) }
            if (i >= 0) OnAirLike(sorted[i], sorted.getOrNull(i + 1))
            else OnAirLike(null, sorted.firstOrNull { it.start.isAfter(now) })
        }
    }
}
