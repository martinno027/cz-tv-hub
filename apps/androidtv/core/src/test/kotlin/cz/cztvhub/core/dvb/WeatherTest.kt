package cz.cztvhub.core.dvb

import cz.cztvhub.core.CtException
import cz.cztvhub.core.Fixtures
import cz.cztvhub.core.HttpResponse
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.double
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.time.Instant
import java.time.LocalDate

/** `GET /dvb/weather` and Domů's weather texts (T59): spec/fixtures/dvb/dvb_weather_*.json, weather_cases.json. */
class WeatherTest {
    private val config = DvbConfig("http://10.0.2.2:18098", "dev-key-dev-key-dev-key-dev-key-0000")

    private fun fixture(name: String): Weather {
        val response = Fixtures.response("dvb/$name")
        return WeatherParser.parse(response.status, response.body)
    }

    @Test fun parsesTheFullAnswer() {
        val weather = fixture("dvb_weather_ok.json")
        assertNull(weather.error)
        assertEquals(WeatherNow(8.4, "Polojasno", "partly"), weather.now)
        assertEquals(WeatherSun("07:13", "16:19"), weather.sun)
        assertEquals(7, weather.days.size)
        assertEquals(WeatherDay(LocalDate.of(2026, 11, 15), "Slabý déšť", "rain", 8.0, 3.5, 60), weather.days[1])
        assertEquals(LocalDate.of(2026, 11, 14), weather.days.first().date)
        assertEquals(WeatherAir("zhoršené", listOf(Pollen("olše", "nízká"))), weather.air)
        assertEquals(
            listOf(
                WeatherWarning(WarningLevel.YELLOW, "Silný vítr", Instant.parse("2026-11-14T12:00:00Z"), Instant.parse("2026-11-14T17:00:00Z")),
                WeatherWarning(WarningLevel.ORANGE, "Riziko požárů", Instant.parse("2026-11-13T22:00:00Z"), null),
            ),
            weather.warnings,
        )
        assertEquals("Počasí: Open-Meteo.com (CC BY 4.0), CAMS · Výstrahy: ČHMÚ (CC BY 4.0)", weather.attribution)
    }

    @Test fun anErrorAnswerShowsNoWeather() {
        val weather = fixture("dvb_weather_error.json")
        assertEquals("Počasí teď není k dispozici", weather.error)
        assertNull(weather.now)
        assertTrue(weather.days.isEmpty())
    }

    @Test fun weatherOffThrowsWithTheGatewaysText() {
        try {
            fixture("dvb_weather_off.json")
            fail("404 must throw")
        } catch (e: CtException) {
            assertEquals("404", e.status)
            assertEquals("Počasí není zapnuté", e.userMessage)
        }
    }

    @Test fun unknownFieldsAreIgnoredAndMissingOnesTolerated() {
        val weather = WeatherParser.parse(200, """
            {"future": {"x": 1}, "now": {"temperature": "warm", "icon": "sun"},
             "sun": {"rise": "07:13"}, "air": null,
             "days": [{"date": "2026-11-14"}, {"date": "bad"}, 5, {"max": 1}],
             "warnings": [{"level": "purple", "event": "Mráz"}, {"level": "red"}]}
        """.trimIndent())
        assertEquals(WeatherNow(null, null, "sun"), weather.now)
        assertNull(weather.sun)
        assertNull(weather.air)
        assertEquals(listOf(WeatherDay(LocalDate.of(2026, 11, 14), null, null, null, null, null)), weather.days)
        assertEquals(listOf(WeatherWarning(null, "Mráz", null, null)), weather.warnings)
        assertNull(weather.attribution)
        assertEquals(Weather(), WeatherParser.parse(200, "{}"))
    }

    @Test fun aBodyThatIsNoJsonIsAnError() {
        try {
            WeatherParser.parse(200, "<html>")
            fail("must throw")
        } catch (e: CtException) {
            assertEquals(DvbMessages.GENERIC, e.userMessage)
        }
    }

    @Test fun roundingFollowsTheGoldenCases() {
        val cases = Fixtures.json("weather_cases.json").getValue("round").jsonArray.map { it.jsonObject }
        assertTrue(cases.size >= 10)
        for (case in cases) {
            fun number(key: String): Double {
                val p = case.getValue(key).jsonPrimitive
                return if (p.isString) p.content.toDoubleOrNull() ?: Double.NaN else p.double
            }
            val place = WeatherRules.place(number("lat"), number("lon"))
            val out = case.getValue("out")
            if (out == JsonNull) {
                assertNull("$case", place)
            } else {
                val (lat, lon) = out.jsonArray.map { it.jsonPrimitive.double }
                assertEquals("$case", lat, place!!.lat.toDouble(), 0.0)
                assertEquals("$case", lon, place.lon.toDouble(), 0.0)
            }
        }
    }

    @Test fun thePlaceGoesOutRoundedAndOnlyWhenThere() {
        val seen = ArrayList<String>()
        val client = DvbClient(config) { url, _ ->
            seen += url
            HttpResponse(200, Fixtures.json("dvb/dvb_weather_ok.json").getValue("body").toString())
        }
        client.weather(WeatherRules.place(50.0755, 14.4378))
        client.weather(WeatherRules.place(49.95, 15.25))
        client.weather(null)
        assertEquals(
            listOf(
                "http://10.0.2.2:18098/dvb/weather?lat=50.1&lon=14.4",
                "http://10.0.2.2:18098/dvb/weather?lat=50.0&lon=15.3",
                "http://10.0.2.2:18098/dvb/weather",
            ),
            seen,
        )
    }

    @Test fun headerLines() {
        val weather = fixture("dvb_weather_ok.json")
        assertEquals("8 °C, polojasno", WeatherRules.nowLine(weather.now!!))
        assertEquals("8 °C", WeatherRules.nowLine(WeatherNow(8.4, null, "cloud")))
        assertEquals("Slunce 7:13–16:19", WeatherRules.sunLine(weather.sun!!))
        assertEquals("Ovzduší: zhoršené", WeatherRules.airLine(weather.air))
        assertNull(WeatherRules.airLine(WeatherAir(null, emptyList())))
        assertEquals("Pyl: olše nízká", WeatherRules.pollenLine(weather.air))
        assertEquals(
            "Pyl: bříza střední, tráva nízká",
            WeatherRules.pollenLine(WeatherAir(null, listOf(Pollen("bříza", "střední"), Pollen("tráva", "nízká")))),
        )
        assertNull(WeatherRules.pollenLine(WeatherAir("zhoršené", emptyList())))
        assertNull(WeatherRules.pollenLine(null))
    }

    @Test fun theWarningLineIsInPragueTime() {
        val (wind, fire) = fixture("dvb_weather_ok.json").warnings
        // 17:00Z is 18:00 in Prague in November (the tests run in America/New_York, where it is still the 14th).
        val now = Instant.parse("2026-11-14T10:00:00Z")
        assertEquals("Výstraha ČHMÚ: Silný vítr do 18:00", WeatherRules.warningLine(wind, now))
        assertEquals("Výstraha ČHMÚ: Riziko požárů (do odvolání)", WeatherRules.warningLine(fire, now))
        // Another Prague day: with the day; 23:30Z on the 14th is already the 15th in Prague.
        val monday = wind.copy(to = Instant.parse("2026-11-16T17:00:00Z"))
        assertEquals("Výstraha ČHMÚ: Silný vítr do po 16. 11. 18:00", WeatherRules.warningLine(monday, now))
        val afterMidnight = wind.copy(to = Instant.parse("2026-11-14T23:30:00Z"))
        assertEquals("Výstraha ČHMÚ: Silný vítr do ne 15. 11. 0:30", WeatherRules.warningLine(afterMidnight, now))
        val summer = wind.copy(to = Instant.parse("2026-07-01T04:00:00Z"))
        assertEquals("Výstraha ČHMÚ: Silný vítr do 6:00", WeatherRules.warningLine(summer, Instant.parse("2026-07-01T01:00:00Z")))
    }

    @Test fun anEndedWarningIsNotShown() {
        val warning = fixture("dvb_weather_ok.json").warnings.first()
        assertEquals(listOf(warning), WeatherRules.current(listOf(warning), Instant.parse("2026-11-14T16:59:59Z")))
        assertTrue(WeatherRules.current(listOf(warning), Instant.parse("2026-11-14T17:00:00Z")).isEmpty())
        assertEquals(1, WeatherRules.current(listOf(warning.copy(to = null)), Instant.parse("2030-01-01T00:00:00Z")).size)
    }

    @Test fun weekCards() {
        val days = fixture("dvb_weather_ok.json").days
        val today = LocalDate.of(2026, 11, 14)
        assertEquals(
            listOf("Dnes", "ne 15. 11.", "po 16. 11.", "út 17. 11.", "st 18. 11.", "čt 19. 11.", "pá 20. 11."),
            days.map { WeatherRules.dayLabel(it.date, today) },
        )
        assertEquals("so 14. 11.", WeatherRules.dayLabel(today, today.minusDays(1)))
        assertEquals(
            listOf("9° / 2°", "8° / 4°", "7° / 4°", "7° / -1°", "5° / -2°", "2° / -3°", "12° / 6°"),
            days.map(WeatherRules::temperatures),
        )
        // rainChance 10, 60, 30, 0, 5, 70, 80: shown from 20 on.
        assertEquals(
            listOf(null, "srážky 60 %", "srážky 30 %", null, null, "srážky 70 %", "srážky 80 %"),
            days.map(WeatherRules::rainLine),
        )
        val day = days.first()
        assertEquals("srážky 20 %", WeatherRules.rainLine(day.copy(rainChance = 20)))
        assertNull(WeatherRules.rainLine(day.copy(rainChance = 19)))
        assertNull(WeatherRules.rainLine(day.copy(rainChance = null)))
        assertEquals("9°", WeatherRules.temperatures(day.copy(min = null)))
        assertNull(WeatherRules.temperatures(day.copy(max = null, min = null)))
    }

    @Test fun temperaturesFollowTheGoldenCases() {
        val cases = Fixtures.json("weather_cases.json").getValue("temperature").jsonArray.map { it.jsonObject }
        assertTrue(cases.size >= 8)
        for (case in cases) {
            val value = case.getValue("value").jsonPrimitive.double
            assertEquals("$case", case.getValue("now").jsonPrimitive.content, WeatherRules.nowLine(WeatherNow(value, null, null)))
            val day = WeatherDay(LocalDate.of(2026, 11, 14), null, null, value, null, null)
            assertEquals("$case", case.getValue("card").jsonPrimitive.content, WeatherRules.temperatures(day))
        }
    }

    @Test fun weekdaysFollowTheGoldenCases() {
        val weekday = Fixtures.json("weather_cases.json").getValue("weekday").jsonObject
        assertEquals(weekday.getValue("names").jsonArray.map { it.jsonPrimitive.content }, WeatherRules.WEEKDAYS_SHORT)
        val cases = weekday.getValue("cases").jsonArray.map { it.jsonObject }
        assertTrue(cases.size >= 4)
        for (case in cases) {
            val label = WeatherRules.dayLabel(
                LocalDate.parse(case.getValue("date").jsonPrimitive.content),
                LocalDate.parse(case.getValue("today").jsonPrimitive.content),
            )
            assertEquals("$case", case.getValue("label").jsonPrimitive.content, label)
        }
    }

    @Test fun refreshWhenTheAnswerIsOlderThanHalfAnHour() {
        val at = Instant.parse("2026-11-14T10:00:00Z")
        assertTrue(WeatherRules.isDue(null, at))
        assertFalse(WeatherRules.isDue(at, at.plusSeconds(29 * 60 + 59)))
        assertTrue(WeatherRules.isDue(at, at.plusSeconds(30 * 60)))
    }
}
