package cz.cztvhub.tv.ui

import androidx.core.graphics.PathParser
import cz.cztvhub.tv.R
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** Domů's weather (T59): the icons for every gateway key, and the texts of spec/apps.md "Location for weather". */
class WeatherUiTest {
    private fun String.oneLine() = replace(Regex("\\s+"), " ")

    private val specDir = generateSequence(File("").absoluteFile) { it.parentFile }
        .map { it.resolve("spec") }.first { it.resolve("apps.md").exists() }
    private val spec = specDir.resolve("apps.md").readText().oneLine()
    private val strings = File("src/main/res/values/strings.xml").readText()

    private fun string(name: String) = Regex("<string name=\"$name\">([^<]*)</string>").find(strings)!!.groupValues[1]

    @Test fun everyIconKeyOfTheGatewayHasItsOwnDrawable() {
        val cases = specDir.resolve("fixtures/weather_cases.json").readText()
        val keys = Regex("\"icon\": \"([a-z]+)\"").findAll(cases).map { it.groupValues[1] }.toSet()
        assertEquals(setOf("sun", "partly", "cloud", "fog", "drizzle", "rain", "snow", "storm"), keys)
        assertEquals(keys, WeatherIcons.KEYS.keys)
        assertEquals(keys.size, WeatherIcons.KEYS.values.toSet().size)
        assertEquals(R.drawable.weather_rain, WeatherIcons.of("rain"))
        assertEquals(R.drawable.weather_cloud, WeatherIcons.of("tornado"))
        assertEquals(R.drawable.weather_cloud, WeatherIcons.of(null))
    }

    /** A vector's path data is parsed only when drawn: a typo would crash Domů on the TV, not the build. */
    @Test fun everyIconParses() {
        for (key in WeatherIcons.KEYS.keys) {
            val xml = File("src/main/res/drawable/weather_$key.xml").readText()
            val paths = Regex("android:pathData=\"([^\"]+)\"").findAll(xml).map { it.groupValues[1] }.toList()
            assertTrue(key, paths.isNotEmpty())
            paths.forEach { assertTrue("$key: $it", PathParser.createNodesFromPathData(it).isNotEmpty()) }
        }
    }

    @Test fun theQuestionIsTheSpecText() {
        val text = string("weather_consent")
        assertTrue(text, text.startsWith("Počasí na Domů podle polohy této televize") && text.endsWith("Nikde se neukládá."))
        assertTrue("\"$text\"" in spec)
        assertTrue("`${string("weather_allow")}` / `${string("weather_decline")}`" in spec)
        assertTrue("\"${string("settings_weather_location")}\"" in spec)
        assertTrue("**${string("home_weather_week")}**" in spec)
    }
}
