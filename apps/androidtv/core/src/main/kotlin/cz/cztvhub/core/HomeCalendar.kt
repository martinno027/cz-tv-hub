package cz.cztvhub.core

import java.time.DayOfWeek
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.Month
import java.time.temporal.ChronoUnit
import java.time.temporal.TemporalAdjusters
import kotlin.math.PI
import kotlin.math.floor
import kotlin.math.roundToLong
import kotlin.math.sin

/** Domů's header (spec/apps.md "Domů", T58): the full date, the name-day line and at most two calendar lines. */
data class HomeHeader(val fullDate: String, val nameLine: String?, val lines: List<String>)

/** A nationwide school holiday of the MŠMT school year, both days included. */
data class SchoolHoliday(val name: String, val from: LocalDate, val to: LocalDate)

/**
 * Domů's calendar, all offline (golden cases `spec/fixtures/calendar_cases.json`): name days, state holidays (zákon
 * č. 245/2000 Sb.) with Easter, the EU clock change, the MŠMT school holidays and new/full moon. Days are Prague
 * calendar days.
 */
object HomeCalendar {
    const val MAX_LINES = 2

    /** How many days ahead a clock change or the start of school holidays is announced. */
    const val AHEAD_DAYS = 6L

    val WEEKDAYS = listOf("pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle")
    val MONTHS_GENITIVE = listOf(
        "ledna", "února", "března", "dubna", "května", "června",
        "července", "srpna", "září", "října", "listopadu", "prosince",
    )

    /** Fixed state holidays, `MM-DD` → name. */
    val STATE_HOLIDAYS: Map<String, String> = mapOf(
        "01-01" to "Den obnovy samostatného českého státu",
        "05-01" to "Svátek práce",
        "05-08" to "Den vítězství",
        "07-05" to "Den slovanských věrozvěstů Cyrila a Metoděje",
        "07-06" to "Den upálení mistra Jana Husa",
        "09-28" to "Den české státnosti",
        "10-28" to "Den vzniku samostatného československého státu",
        "11-17" to "Den boje za svobodu a demokracii a Mezinárodní den studentstva",
        "12-24" to "Štědrý den",
        "12-25" to "1. svátek vánoční",
        "12-26" to "2. svátek vánoční",
    )

    /** State holidays relative to Easter Sunday, days → name. */
    val EASTER_HOLIDAYS: Map<Int, String> = mapOf(-2 to "Velký pátek", 1 to "Velikonoční pondělí")

    /**
     * MŠMT's nationwide holidays (spring holidays depend on the district: not shown). Updated once a year; outside
     * the table nothing is shown.
     */
    val SCHOOL_HOLIDAYS: List<SchoolHoliday> = listOf(
        SchoolHoliday("Podzimní prázdniny", LocalDate.of(2026, 10, 29), LocalDate.of(2026, 10, 30)),
        SchoolHoliday("Vánoční prázdniny", LocalDate.of(2026, 12, 23), LocalDate.of(2027, 1, 3)),
        SchoolHoliday("Pololetní prázdniny", LocalDate.of(2027, 1, 29), LocalDate.of(2027, 1, 29)),
        SchoolHoliday("Velikonoční prázdniny", LocalDate.of(2027, 3, 25), LocalDate.of(2027, 3, 25)),
        SchoolHoliday("Hlavní prázdniny", LocalDate.of(2027, 7, 1), LocalDate.of(2027, 8, 31)),
    )

    fun header(now: Instant): HomeHeader = header(Days.today(now))

    fun header(day: LocalDate): HomeHeader = HomeHeader(fullDate(day), nameLine(day), lines(day))

    /** `středa 4. listopadu`: lower-case weekday, genitive month, no year. */
    fun fullDate(day: LocalDate): String =
        "${WEEKDAYS[day.dayOfWeek.value - 1]} ${dayMonth(day)}"

    /** `Svátek má Karel` / `Svátek mají A, B a C`; on a state holiday the holiday line; null when neither. */
    fun nameLine(day: LocalDate): String? {
        stateHoliday(day)?.let { return "Dnes je státní svátek: $it" }
        val names = NameDays.DAYS[key(day)].orEmpty()
        return when (names.size) {
            0 -> null
            1 -> "Svátek má ${names[0]}"
            else -> "Svátek mají ${names.dropLast(1).joinToString(", ")} a ${names.last()}"
        }
    }

    /** At most [MAX_LINES], in this order: holiday tomorrow, clock change, school holidays, new/full moon. */
    fun lines(day: LocalDate): List<String> = listOfNotNull(
        stateHoliday(day.plusDays(1))?.let { "Zítra je státní svátek: $it" },
        clockChangeLine(day),
        schoolHolidayLine(day),
        moonLine(day),
    ).take(MAX_LINES)

    fun stateHoliday(day: LocalDate): String? {
        STATE_HOLIDAYS[key(day)]?.let { return it }
        return EASTER_HOLIDAYS[ChronoUnit.DAYS.between(easter(day.year), day).toInt()]
    }

    /** Easter Sunday by the Gregorian computus (Meeus/Jones/Butcher). */
    fun easter(year: Int): LocalDate {
        val a = year % 19
        val b = year / 100
        val c = year % 100
        val d = b / 4
        val e = b % 4
        val f = (b + 8) / 25
        val g = (b - f + 1) / 3
        val h = (19 * a + b - d - g + 15) % 30
        val i = c / 4
        val k = c % 4
        val l = (32 + 2 * e + 2 * i - h - k) % 7
        val m = (a + 11 * h + 22 * l) / 451
        val month = (h + l - 7 * m + 114) / 31
        val dayOfMonth = (h + l - 7 * m + 114) % 31 + 1
        return LocalDate.of(year, month, dayOfMonth)
    }

    /** The EU clock changes of [year]: the last Sunday of March (forward) and of October (back). */
    fun clockChanges(year: Int): List<LocalDate> = listOf(Month.MARCH, Month.OCTOBER).map {
        LocalDate.of(year, it, 1).with(TemporalAdjusters.lastInMonth(DayOfWeek.SUNDAY))
    }

    private fun clockChangeLine(day: LocalDate): String? {
        val change = clockChanges(day.year).firstOrNull { !it.isBefore(day) && !it.isAfter(day.plusDays(AHEAD_DAYS)) }
            ?: return null
        val direction = if (change.month == Month.MARCH) "o hodinu dopředu" else "o hodinu zpět"
        val whenText = when (ChronoUnit.DAYS.between(day, change)) {
            0L -> "Dnes"
            1L -> "Zítra"
            else -> "V neděli ${dayMonth(change)}"
        }
        return "$whenText se mění čas: $direction"
    }

    private fun schoolHolidayLine(day: LocalDate): String? {
        for (holiday in SCHOOL_HOLIDAYS) {
            if (!day.isBefore(holiday.from) && !day.isAfter(holiday.to)) {
                return "Dnes jsou ${holiday.name.replaceFirstChar { it.lowercaseChar() }}"
            }
            if (day.isBefore(holiday.from) && !holiday.from.isAfter(day.plusDays(AHEAD_DAYS))) {
                return "${holiday.name} ${range(holiday.from, holiday.to)}"
            }
        }
        return null
    }

    /** `29. ledna`, `29.–30. října`, `23. prosince – 3. ledna`. */
    private fun range(from: LocalDate, to: LocalDate): String = when {
        from == to -> dayMonth(from)
        from.month == to.month && from.year == to.year -> "${from.dayOfMonth}.–${dayMonth(to)}"
        else -> "${dayMonth(from)} – ${dayMonth(to)}"
    }

    private fun moonLine(day: LocalDate): String? = when (moonPhase(day)) {
        MoonPhase.FULL -> "Dnes je úplněk"
        MoonPhase.NEW -> "Dnes je nov"
        null -> null
    }

    enum class MoonPhase { NEW, FULL }

    /** A new or full moon whose instant falls on the Prague calendar [day], else null. */
    fun moonPhase(day: LocalDate): MoonPhase? =
        MoonPhase.entries.firstOrNull { Days.today(moonNear(day, it)) == day }

    /** The instant of the [phase] nearest to [day] (golden `moonInstant`, compared to the second). */
    fun moonNear(day: LocalDate, phase: MoonPhase): Instant {
        val yearFraction = day.year + (day.dayOfYear - 1) / 365.25
        val base = floor((yearFraction - 2000) * 12.3685).toInt()
        val noon = day.atTime(12, 0).atZone(Days.ZONE).toInstant()
        return ((base - 1)..(base + 1))
            .map { moonInstant(it + if (phase == MoonPhase.FULL) 0.5 else 0.0) }
            .minBy { Duration.between(it, noon).abs() }
    }

    /**
     * Meeus, Astronomical Algorithms ch. 49: the mean phase plus the three largest periodic terms ([k] integer = new
     * moon, `+ 0.5` = full moon). TT taken as UT (ΔT ≈ 1 min is well inside the method's error).
     */
    fun moonInstant(k: Double): Instant {
        val t = k / 1236.85
        val jde = 2451550.09766 + 29.530588861 * k + 0.00015437 * t * t -
            0.000000150 * t * t * t + 0.00000000073 * t * t * t * t
        val e = 1 - 0.002516 * t - 0.0000074 * t * t
        val m = rad(2.5534 + 29.10535670 * k - 0.0000014 * t * t - 0.00000011 * t * t * t)
        val mPrime = rad(201.5643 + 385.81693528 * k + 0.0107582 * t * t + 0.00001238 * t * t * t)
        val full = k - floor(k) > 0.25
        val correction = if (full) {
            -0.40614 * sin(mPrime) + 0.17302 * e * sin(m) + 0.01614 * sin(2 * mPrime)
        } else {
            -0.40720 * sin(mPrime) + 0.17241 * e * sin(m) + 0.01608 * sin(2 * mPrime)
        }
        val unixSeconds = (jde + correction - 2440587.5) * 86_400
        return Instant.ofEpochSecond(unixSeconds.roundToLong())
    }

    private fun rad(degrees: Double): Double = (degrees % 360) * PI / 180

    private fun dayMonth(day: LocalDate): String = "${day.dayOfMonth}. ${MONTHS_GENITIVE[day.monthValue - 1]}"

    private fun key(day: LocalDate): String = "%02d-%02d".format(day.monthValue, day.dayOfMonth)
}
