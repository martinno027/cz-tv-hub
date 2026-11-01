package cz.cztvhub.core

import java.time.Duration
import java.time.Instant
import java.time.LocalDate

/**
 * "A failed ČT day is asked again by itself (T42)" (spec/apps.md): every load of a ČT schedule goes through here, so
 * each failure is remembered with its time; while Živě or the guide is visible, [retryDue] asks a day that shows
 * "Program se nepodařilo načíst" again until it loads: [fast] after its latest failure during the first [fastFor]
 * since the first failure of the outage, then every [slow]. These retries are outside the ~10 requests of one screen
 * open; loads the viewer asks for (a chip, reopening) are never held back.
 */
class ScheduleRetry(
    private val repo: ScheduleRepository,
    private val clock: () -> Instant = Instant::now,
    private val fast: Duration = Duration.ofSeconds(30),
    private val fastFor: Duration = Duration.ofMinutes(5),
    private val slow: Duration = Duration.ofMinutes(2),
) {
    /** First and latest failure of a day not loaded since. */
    private data class Failure(val first: Instant, val latest: Instant)

    private val failures = HashMap<LocalDate, Failure>()

    /** [day] failed and was not loaded since. */
    @Synchronized fun hasFailed(day: LocalDate): Boolean = day in failures

    /** [ScheduleRepository.day], remembering the outcome. Throws [CtException]. */
    fun day(day: LocalDate): Map<String, List<Programme>> = record(day) { repo.day(day) }

    /**
     * [ScheduleRepository.onAir]; it fails only when the broadcast day on air failed, so that day is remembered.
     * Throws [CtException].
     */
    fun onAir(now: Instant = clock()): Map<String, OnAir> = record(Days.openDay(now)) { repo.onAir(now) }

    /**
     * Asks again those of [shown] (the days whose failure text is on screen now) that are due; returns the days that
     * loaded. Blocking (network); never throws.
     */
    fun retryDue(shown: Collection<LocalDate>): Map<LocalDate, Map<String, List<Programme>>> {
        val loaded = LinkedHashMap<LocalDate, Map<String, List<Programme>>>()
        for (day in shown.distinct()) {
            val due = synchronized(this) { failures[day]?.let { !clock().isBefore(dueAt(it)) } ?: false }
            if (!due) continue
            try {
                loaded[day] = day(day)
            } catch (_: CtException) {
                // Remembered with the new time: asked again 30 s or 2 min later.
            }
        }
        return loaded
    }

    /** How long until the next of [shown] is due; null = nothing to ask (none of them failed). */
    @Synchronized fun untilNext(shown: Collection<LocalDate>): Duration? {
        val now = clock()
        return shown.mapNotNull { failures[it]?.let(::dueAt) }
            .minOrNull()
            ?.let { Duration.between(now, it) }
            ?.let { if (it.isNegative) Duration.ZERO else it }
    }

    private fun <T> record(day: LocalDate, load: () -> T): T {
        val result = try {
            load()
        } catch (e: CtException) {
            synchronized(this) {
                val at = clock()
                failures[day] = Failure(failures[day]?.first ?: at, at)
            }
            throw e
        }
        synchronized(this) { failures.remove(day) }
        return result
    }

    /** 30 s after the latest failure while the outage is younger than 5 min, then 2 min. */
    private fun dueAt(f: Failure): Instant =
        f.latest.plus(if (Duration.between(f.first, f.latest) < fastFor) fast else slow)
}
