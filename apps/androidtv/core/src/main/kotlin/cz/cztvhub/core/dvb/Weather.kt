package cz.cztvhub.core.dvb

import cz.cztvhub.core.CtException
import cz.cztvhub.core.Days
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.math.BigDecimal
import java.math.RoundingMode
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.format.DateTimeFormatter

/** `GET /dvb/weather` (T59, spec/dvb-gateway.md "Weather"): the gateway's texts as they come; null = not sent. */
data class Weather(
    val now: WeatherNow? = null,
    val sun: WeatherSun? = null,
    /** Today first. */
    val days: List<WeatherDay> = emptyList(),
    val air: WeatherAir? = null,
    val warnings: List<WeatherWarning> = emptyList(),
    val attribution: String? = null,
    /** "Počasí teď není k dispozici": Domů then shows no weather at all. */
    val error: String? = null,
)

data class WeatherNow(val temperature: Double?, val text: String?, val icon: String?)

/** Local Prague times as the gateway sends them, `07:13`. */
data class WeatherSun(val rise: String, val set: String)

data class WeatherDay(
    val date: LocalDate,
    val text: String?,
    val icon: String?,
    val max: Double?,
    val min: Double?,
    val rainChance: Int?,
)

data class WeatherAir(val aqiText: String?, val pollen: List<Pollen>)

data class Pollen(val name: String, val level: String)

/** A ČHMÚ warning; [level] null for a level this app does not know (shown without colour). */
data class WeatherWarning(val level: WarningLevel?, val event: String, val from: Instant?, val to: Instant?)

enum class WarningLevel { YELLOW, ORANGE, RED }

/** Parsing of `/dvb/weather`; unknown fields are ignored, missing optional ones tolerated. */
object WeatherParser {
    /** `404` (weather off on the gateway, or an older gateway) and other errors throw like every `/dvb/…` call. */
    fun parse(status: Int, body: String): Weather {
        val root = try {
            Json.parseToJsonElement(body) as? JsonObject
        } catch (_: Exception) {
            null
        }
        if (status == 401) throw CtException("gateway HTTP 401", DvbMessages.BAD_KEY, "401")
        if (status !in 200..299) {
            throw CtException("gateway HTTP $status", root?.str("error") ?: DvbMessages.GENERIC, status.toString())
        }
        root ?: throw CtException("gateway: not JSON", DvbMessages.GENERIC)
        root.str("error")?.let { return Weather(error = it) }
        return Weather(
            now = root.obj("now")?.let { WeatherNow(it.num("temperature"), it.str("text"), it.str("icon")) },
            sun = root.obj("sun")?.let { s ->
                val rise = s.str("rise") ?: return@let null
                WeatherSun(rise, s.str("set") ?: return@let null)
            },
            days = root.array("days").mapNotNull { item ->
                val o = item as? JsonObject ?: return@mapNotNull null
                val date = o.str("date")?.let { runCatching { LocalDate.parse(it) }.getOrNull() } ?: return@mapNotNull null
                WeatherDay(date, o.str("text"), o.str("icon"), o.num("max"), o.num("min"), o.num("rainChance")?.toInt())
            },
            air = root.obj("air")?.let { a ->
                WeatherAir(a.str("aqiText"), a.array("pollen").mapNotNull { item ->
                    val o = item as? JsonObject ?: return@mapNotNull null
                    Pollen(o.str("name") ?: return@mapNotNull null, o.str("level") ?: return@mapNotNull null)
                })
            },
            warnings = root.array("warnings").mapNotNull { item ->
                val o = item as? JsonObject ?: return@mapNotNull null
                val level = when (o.str("level")) {
                    "yellow" -> WarningLevel.YELLOW
                    "orange" -> WarningLevel.ORANGE
                    "red" -> WarningLevel.RED
                    else -> null
                }
                WeatherWarning(level, o.str("event") ?: return@mapNotNull null, o.instant("from"), o.instant("to"))
            },
            attribution = root.str("attribution"),
        )
    }

    private fun JsonObject.str(key: String): String? {
        val v = this[key]
        if (v == null || v is JsonNull) return null
        return (v as? JsonPrimitive)?.content?.ifEmpty { null }
    }

    private fun JsonObject.num(key: String): Double? {
        val v = this[key] as? JsonPrimitive ?: return null
        if (v is JsonNull || v.isString) return null
        return v.content.toDoubleOrNull()?.takeIf { it.isFinite() }
    }

    private fun JsonObject.obj(key: String): JsonObject? = this[key] as? JsonObject

    private fun JsonObject.array(key: String): List<JsonElement> = (this[key] as? JsonArray) ?: emptyList()

    private fun JsonObject.instant(key: String): Instant? = str(key)?.let { runCatching { Instant.parse(it) }.getOrNull() }
}

/** The TV's location as sent to the gateway (`?lat=&lon=`), already rounded. */
data class WeatherPlace(val lat: BigDecimal, val lon: BigDecimal) {
    val query: String get() = "lat=${lat.toPlainString()}&lon=${lon.toPlainString()}"
}

/** Domů's weather texts (spec/apps.md "Domů", T59); everything else is the gateway's text as it comes. */
object WeatherRules {
    /** The answer is asked again when Domů is shown and it is older than this. */
    val REFRESH: Duration = Duration.ofMinutes(30)

    /** "srážky 60 %" from this chance on. */
    const val RAIN_FROM = 20

    /** The gateway's bounds of a place (outside: `400`); the TV then asks without its location. */
    private val LAT = BigDecimal("48.0")..BigDecimal("51.5")
    private val LON = BigDecimal("11.5")..BigDecimal("19.5")

    val WEEKDAYS_SHORT = listOf("po", "út", "st", "čt", "pá", "so", "ne")

    private val CLOCK: DateTimeFormatter = DateTimeFormatter.ofPattern("H:mm").withZone(Days.ZONE)

    fun isDue(answeredAt: Instant?, now: Instant): Boolean =
        answeredAt == null || Duration.between(answeredAt, now) >= REFRESH

    /**
     * The device location rounded on the device to 0.1° (half away from zero; golden `weather_cases.json` "round"),
     * null outside the gateway's bounds (checked before rounding, like the gateway) or for a non-finite number.
     */
    fun place(lat: Double, lon: Double): WeatherPlace? {
        if (!lat.isFinite() || !lon.isFinite()) return null
        // From the shortest decimal text of the double, so 49.95 rounds up as written rather than as stored.
        val la = BigDecimal(lat.toString())
        val lo = BigDecimal(lon.toString())
        if (la !in LAT || lo !in LON) return null
        return WeatherPlace(la.setScale(1, RoundingMode.HALF_UP), lo.setScale(1, RoundingMode.HALF_UP))
    }

    /** Whole degrees, half away from zero; never `-0`; ASCII hyphen as minus (golden "temperature"). */
    fun degrees(value: Double): String {
        val whole = BigDecimal(value.toString()).setScale(0, RoundingMode.HALF_UP).toInt()
        return whole.toString()
    }

    /** `8 °C, polojasno` (the gateway's text with a lower-case first letter); `8 °C` without a text. */
    fun nowLine(now: WeatherNow): String? {
        val text = now.text?.replaceFirstChar { it.lowercaseChar() }
        val temperature = now.temperature?.let { "${degrees(it)} °C" }
        return listOfNotNull(temperature, text).joinToString(", ").ifEmpty { null }
    }

    /** `Slunce 7:13–16:19`. */
    fun sunLine(sun: WeatherSun): String = "Slunce ${sun.rise.removePrefix("0")}–${sun.set.removePrefix("0")}"

    fun airLine(air: WeatherAir?): String? = air?.aqiText?.let { "Ovzduší: $it" }

    /** `Pyl: bříza střední, tráva nízká`. */
    fun pollenLine(air: WeatherAir?): String? =
        air?.pollen?.takeIf { it.isNotEmpty() }?.joinToString(", ", prefix = "Pyl: ") { "${it.name} ${it.level}" }

    /**
     * `Výstraha ČHMÚ: Silný vítr do 18:00` (`to` in Prague time), on another day than today `do po 16. 11. 18:00`;
     * without `to` (fire risk, ozone) `Výstraha ČHMÚ: Riziko požárů (do odvolání)`.
     */
    fun warningLine(warning: WeatherWarning, now: Instant): String {
        val to = warning.to ?: return "Výstraha ČHMÚ: ${warning.event} (do odvolání)"
        val day = Days.today(to)
        val date = if (day == Days.today(now)) "" else "${dayLabel(day, today = null)} "
        return "Výstraha ČHMÚ: ${warning.event} do $date${CLOCK.format(to)}"
    }

    /** The warnings still in force at [now] (a cached answer can hold one that has just ended). */
    fun current(warnings: List<WeatherWarning>, now: Instant): List<WeatherWarning> =
        warnings.filter { it.to == null || it.to.isAfter(now) }

    /** `Dnes` for [today] (Prague), else `út 4. 11.` (golden "weekday"). */
    fun dayLabel(day: LocalDate, today: LocalDate?): String =
        if (day == today) "Dnes" else "${WEEKDAYS_SHORT[day.dayOfWeek.value - 1]} ${day.dayOfMonth}. ${day.monthValue}."

    /** `9° / 2°`; one of them alone when the other is missing. */
    fun temperatures(day: WeatherDay): String? =
        listOfNotNull(day.max, day.min).joinToString(" / ") { "${degrees(it)}°" }.ifEmpty { null }

    /** `srážky 60 %` only from [RAIN_FROM]. */
    fun rainLine(day: WeatherDay): String? = day.rainChance?.takeIf { it >= RAIN_FROM }?.let { "srážky $it %" }
}
