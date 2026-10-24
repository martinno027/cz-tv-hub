package cz.cztvhub.core.dvb

import cz.cztvhub.core.CtException
import cz.cztvhub.core.Fixtures
import cz.cztvhub.core.ProgrammePage
import cz.cztvhub.core.HttpResponse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Test
import java.time.LocalDate

/**
 * `GET /dvb/details` (spec/dvb-gateway.md "Programme details (T15 …)"): the shared fixtures in spec/fixtures/dvb/, plus
 * inline answers for the edge cases they do not cover.
 */
class DvbDetailsTest {
    private val config = DvbConfig("http://10.0.2.2:18098", "dev-key-dev-key-dev-key-dev-key-0000")
    private val attribution =
        "This application uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB."

    private val matched = """
        {"match": {"kind": "movie", "title": "Pelíšky", "originalTitle": "Pelíšky", "year": 1999,
          "overview": "Vánoce 1967.", "genres": ["Komedie", "Drama"], "runtimeMinutes": 115,
          "rating": {"value": 8.1, "votes": 1234}, "poster": "/dvb/details/image/w342/abc.jpg", "tmdbId": 12345},
         "attribution": "$attribution"}
    """.trimIndent()

    private fun client(status: Int, body: String, seen: MutableList<Pair<String, Map<String, String>>> = ArrayList()) =
        DvbClient(config) { url, headers ->
            seen += url to headers
            HttpResponse(status, body)
        }

    @Test fun parsesAMatch() {
        val details = DvbParser.parseDetails(200, matched)
        val m = details.match!!
        assertEquals("movie", m.kind)
        assertEquals("Pelíšky", m.title)
        assertEquals("Pelíšky", m.originalTitle)
        assertEquals(1999, m.year)
        assertEquals("Vánoce 1967.", m.overview)
        assertEquals(listOf("Komedie", "Drama"), m.genres)
        assertEquals(115, m.runtimeMinutes)
        assertEquals(TmdbRating(8.1, 1234), m.rating)
        assertEquals("/dvb/details/image/w342/abc.jpg", m.poster)
        assertEquals("12345", m.tmdbId)
        assertEquals(attribution, details.attribution)
    }

    @Test fun sharedFixtures() {
        val match = Fixtures.response("dvb/dvb_details_match.json")
        val details = DvbParser.parseDetails(match.status, match.body)
        val m = details.match!!
        assertEquals("movie", m.kind)
        assertEquals("Hněv Titánů", m.title)
        assertEquals("Wrath of the Titans", m.originalTitle)
        assertEquals(2012, m.year)
        assertEquals("Smyšlený popis filmu.", m.overview)
        assertEquals(listOf("Akční"), m.genres)
        assertEquals(99, m.runtimeMinutes)
        assertEquals(TmdbRating(5.8, 7311), m.rating)
        assertEquals("Hodnocení TMDB 5,8 (7 311 hlasů)", ProgrammePage.ratingLine(5.8, 7311))
        assertEquals("/dvb/details/image/w342/titanyPoster01.jpg", m.poster)
        assertEquals("4401", m.tmdbId)
        assertEquals(attribution, details.attribution)

        val none = Fixtures.response("dvb/dvb_details_nomatch.json")
        val empty = DvbParser.parseDetails(none.status, none.body)
        assertNull(empty.match)
        assertEquals(attribution, empty.attribution)
    }

    @Test fun noMatchAndTmdbErrors() {
        assertNull(DvbParser.parseDetails(200, """{"match": null, "attribution": "$attribution"}""").match)
        assertNull(DvbParser.parseDetails(200, """{"match": null, "error": "TMDb neodpovídá"}""").match)
        assertNull(DvbParser.parseDetails(200, """{"match": {"kind": "tv"}}""").match) // no title
        assertNull(DvbParser.parseDetails(200, """{"match": "Pelíšky"}""").match)
    }

    @Test fun dropsWhatTheGatewayShouldNotSend() {
        val body = """
            {"match": {"title": "X", "rating": {"value": 9.1, "votes": 19}, "poster": "https://image.tmdb.org/t/p/w342/a.jpg",
              "year": "nineteen", "runtimeMinutes": 0, "genres": ["", 5, "Drama"]}}
        """.trimIndent()
        val m = DvbParser.parseDetails(200, body).match!!
        assertNull(m.rating)
        assertNull(m.poster)
        assertNull(m.year)
        assertNull(m.runtimeMinutes)
        assertEquals(listOf("Drama"), m.genres)
        assertNull(m.originalTitle)
        assertNull(m.overview)
    }

    @Test fun posterPathsAreGatewayImagesOnly() {
        assertEquals(true, DvbParser.isPosterPath("/dvb/details/image/w342/abc.jpg"))
        for (bad in listOf("//evil/dvb/details/image/a.jpg", "/dvb/details/image/../../admin", "/dvb/epg",
                "https://image.tmdb.org/t/p/w342/a.jpg", "/dvb/details/image/w342/a.jpg?u=http://x")) {
            assertEquals(bad, false, DvbParser.isPosterPath(bad))
            assertNull(bad, client(200, "{}").posterUrl(bad))
        }
        assertEquals("http://10.0.2.2:18098/dvb/details/image/w342/abc.jpg",
            client(200, "{}").posterUrl("/dvb/details/image/w342/abc.jpg"))
    }

    @Test fun asksByProgrammeIdWithTheKey() {
        val seen = ArrayList<Pair<String, Map<String, String>>>()
        client(200, matched, seen).details(DetailsQuery.Dvb("dvr:0a1b 2c"))
        client(200, matched, seen).details(DetailsQuery.Ct("ct1", LocalDate.of(2026, 10, 24), "426 123"))
        assertEquals("http://10.0.2.2:18098/dvb/details?source=dvb&id=dvr%3A0a1b+2c", seen[0].first)
        assertEquals("http://10.0.2.2:18098/dvb/details?source=ct&channel=ct1&date=2026-10-24&idec=426+123", seen[1].first)
        seen.forEach { (_, headers) -> assertEquals("Bearer ${config.key}", headers["Authorization"]) }
    }

    @Test fun notFoundAndOffAreErrors() {
        for ((status, body) in listOf(404 to """{"error": "Podrobnosti nejsou zapnuté"}""", 404 to "", 500 to "x")) {
            try {
                client(status, body).details(DetailsQuery.Dvb("e1"))
                fail("expected CtException for $status")
            } catch (e: CtException) {
                assertEquals(status.toString(), e.status)
            }
        }
    }
}
