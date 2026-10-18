package cz.cztvhub.core

import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** Broadcast days and Czech labels. CT plans its days in Prague time, whatever the device zone. */
object Days {
    val ZONE: ZoneId = ZoneId.of("Europe/Prague")
    const val DAYS_BACK = 7
    const val DAYS_AHEAD = 2

    private val WEEKDAYS = listOf("po", "út", "st", "čt", "pá", "so", "ne")
    private val TIME: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm").withZone(ZONE)

    fun today(now: Instant): LocalDate = now.atZone(ZONE).toLocalDate()

    /** Days offered in the guide's day picker, oldest first. */
    fun guideDays(today: LocalDate): List<LocalDate> =
        (-DAYS_BACK..DAYS_AHEAD).map { today.plusDays(it.toLong()) }

    fun label(day: LocalDate, today: LocalDate): String = when (day) {
        today -> "Dnes"
        today.minusDays(1) -> "Včera"
        else -> "${WEEKDAYS[day.dayOfWeek.value - 1]} ${day.dayOfMonth}. ${day.monthValue}."
    }

    fun time(moment: Instant): String = TIME.format(moment)

    fun timeRange(start: Instant, end: Instant): String = "${time(start)}–${time(end)}"
}
