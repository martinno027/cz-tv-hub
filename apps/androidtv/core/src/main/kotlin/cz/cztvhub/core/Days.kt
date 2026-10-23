package cz.cztvhub.core

import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** Broadcast days and Czech labels. CT plans its days in Prague time, whatever the device zone. */
object Days {
    val ZONE: ZoneId = ZoneId.of("Europe/Prague")
    const val DAYS_BACK = 7
    const val DAYS_AHEAD = 2

    /** A broadcast day runs from 05:00 to 05:00 Prague. */
    val DAY_START: LocalTime = LocalTime.of(5, 0)

    private val WEEKDAYS = listOf("po", "út", "st", "čt", "pá", "so", "ne")
    private val TIME: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm").withZone(ZONE)

    /** The calendar day (Prague) of [now]: "Dnes" for labels and the day strip. */
    fun today(now: Instant): LocalDate = now.atZone(ZONE).toLocalDate()

    /** The broadcast day (05:00–05:00 Prague) containing [now]: the grid every open of the guide shows. */
    fun openDay(now: Instant): LocalDate {
        val local = now.atZone(ZONE).toLocalDateTime()
        return if (local.toLocalTime() < DAY_START) local.toLocalDate().minusDays(1) else local.toLocalDate()
    }

    /** The day strip's chip of a programme starting at [time]: its calendar day (0:10 is the next day's). */
    fun chipDay(time: Instant): LocalDate = today(time)

    /**
     * The chip highlighted for a focused programme starting at [start] on the grid of [gridDay]: its [chipDay], or
     * the grid's day when that has no chip (after midnight on the strip's last day).
     */
    fun highlightedChip(start: Instant, gridDay: LocalDate, today: LocalDate): LocalDate =
        chipDay(start).takeIf { it in guideDays(today) } ?: gridDay

    /** Grid day and focus of a picked chip: [anchor] null = now (the grid containing it), else that moment. */
    data class Target(val day: LocalDate, val anchor: Instant?)

    /** Picking [chip] on the day strip: "Dnes" shows now as on open, any other day that broadcast day at 20:00. */
    fun select(chip: LocalDate, now: Instant): Target =
        if (chip == today(now)) {
            Target(openDay(now), null)
        } else {
            Target(chip, chip.atTime(EVENING).atZone(ZONE).toInstant())
        }

    private val EVENING: LocalTime = LocalTime.of(20, 0)

    /** Days offered in the guide's day picker, oldest first. */
    fun guideDays(today: LocalDate): List<LocalDate> =
        (-DAYS_BACK..DAYS_AHEAD).map { today.plusDays(it.toLong()) }

    /**
     * The guide day next to [day] ([forward] = later) when it is in the picker's range, else null
     * (spec/apps.md "Across days": scrolling past a day's edge opens the neighbouring day).
     */
    fun adjacentGuideDay(day: LocalDate, today: LocalDate, forward: Boolean): LocalDate? =
        day.plusDays(if (forward) 1 else -1).takeIf { it in guideDays(today) }

    fun label(day: LocalDate, today: LocalDate): String = when (day) {
        today -> "Dnes"
        today.minusDays(1) -> "Včera"
        today.plusDays(1) -> "Zítra"
        else -> "${WEEKDAYS[day.dayOfWeek.value - 1]} ${day.dayOfMonth}. ${day.monthValue}."
    }

    fun time(moment: Instant): String = TIME.format(moment)

    fun timeRange(start: Instant, end: Instant): String = "${time(start)}–${time(end)}"
}
