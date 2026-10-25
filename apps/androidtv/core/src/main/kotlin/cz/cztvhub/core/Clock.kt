package cz.cztvhub.core

import java.time.Instant
import java.time.format.DateTimeFormatter

/**
 * The clock top right on Živě, the guide and Nastavení (spec/apps.md "Clock"): Prague `H:MM`, 24 h, whatever the
 * device zone. Golden cases in spec/fixtures/clock_cases.json.
 */
object Clock {
    private const val MINUTE_MS = 60_000L
    private val FORMAT: DateTimeFormatter = DateTimeFormatter.ofPattern("H:mm").withZone(Days.ZONE)

    fun text(now: Instant): String = FORMAT.format(now)

    /**
     * Milliseconds from [now] to the next minute boundary (1..60 000), so the clock changes on the minute. Prague's
     * offsets are whole hours: a UTC minute boundary is a Prague one.
     */
    fun untilNextMinute(now: Instant): Long = MINUTE_MS - Math.floorMod(now.toEpochMilli(), MINUTE_MS)
}
