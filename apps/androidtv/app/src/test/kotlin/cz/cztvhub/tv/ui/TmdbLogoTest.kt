package cz.cztvhub.tv.ui

import androidx.compose.ui.graphics.vector.PathNode
import androidx.compose.ui.graphics.vector.PathParser
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** The TMDB logo drawable is spec/assets/tmdb-logo-short.svg unchanged (path and gradient), and its path parses. */
class TmdbLogoTest {
    private val drawable = File("src/main/res/drawable/tmdb_logo_short.xml").readText()
    private val svg = generateSequence(File("").absoluteFile) { it.parentFile }
        .map { it.resolve("spec/assets/tmdb-logo-short.svg") }.first { it.exists() }.readText()

    private fun attr(text: String, regex: String) = Regex(regex).find(text)!!.groupValues[1]

    @Test fun sameShapeAndColoursAsTheOfficialFile() {
        assertEquals(attr(svg, " d=\"([^\"]+)\""), attr(drawable, "android:pathData=\"([^\"]+)\""))
        val svgStops = Regex("stop offset=\"([^\"]+)\" stop-color=\"#([0-9a-f]{6})\"").findAll(svg)
            .map { it.groupValues[1].toFloat() to it.groupValues[2].uppercase() }.toList()
        val stops = Regex("android:offset=\"([^\"]+)\" android:color=\"#FF([0-9A-F]{6})\"").findAll(drawable)
            .map { it.groupValues[1].toFloat() to it.groupValues[2] }.toList()
        assertEquals(3, svgStops.size)
        assertEquals(svgStops, stops)
        assertEquals("0 0 273.42 35.52", attr(svg, "viewBox=\"([^\"]+)\""))
        assertEquals("273.42", attr(drawable, "android:viewportWidth=\"([^\"]+)\""))
        assertEquals("35.52", attr(drawable, "android:viewportHeight=\"([^\"]+)\""))
    }

    @Test fun pathParses() {
        val nodes = PathParser().parsePathString(attr(drawable, "android:pathData=\"([^\"]+)\"")).toNodes()
        assertTrue(nodes.size > 50)
        assertTrue(nodes.any { it is PathNode.ArcTo || it is PathNode.RelativeArcTo })
    }
}
