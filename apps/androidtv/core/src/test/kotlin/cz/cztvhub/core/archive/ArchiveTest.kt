package cz.cztvhub.core.archive

import cz.cztvhub.core.CtException
import cz.cztvhub.core.Fixtures
import cz.cztvhub.core.HttpResponse
import cz.cztvhub.core.PageSource
import cz.cztvhub.core.PlayRequest
import cz.cztvhub.core.dvb.DvbClient
import cz.cztvhub.core.dvb.DvbConfig
import cz.cztvhub.core.dvb.DvbMessages
import cz.cztvhub.core.dvb.TmdbRating
import kotlinx.serialization.json.double
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.time.Instant

/** The Archiv tab (T13): the gateway's `/dvb/archive…` answers and the card/line rules. */
class ArchiveTest {
    private val config = DvbConfig("http://10.0.2.2:18098", "dev-key-dev-key-dev-key-dev-key-0000")

    private fun client(status: Int, body: String, seen: MutableList<Pair<String, Map<String, String>>> = ArrayList()) =
        DvbClient(config) { url, headers ->
            seen += url to headers
            HttpResponse(status, body)
        }

    private fun failure(block: () -> Unit): CtException {
        try {
            block()
        } catch (e: CtException) {
            return e
        }
        fail("expected CtException")
        throw AssertionError()
    }

    private val home = """
        {"rows": [
          {"id": "recordings", "title": "Naše nahrávky", "more": true, "cards": [
            {"id": "rec:mistrizastavarny", "source": "rec", "title": "Mistři zastavárny", "kind": "series",
             "image": "/dvb/recording/b1-1700000000-1700001800/thumb.jpg", "rating": null, "genres": [],
             "episodeCount": 2}]},
          {"id": "3947-filmy", "title": "Filmy", "more": false, "cards": [
            {"id": "ct:10001", "source": "both", "title": "Pelíšky", "kind": "movie", "year": 1999,
             "image": "https://img.example.invalid/p.jpg", "rating": {"value": 8.14, "votes": 1234},
             "genres": ["Komedie"], "durationMinutes": 115},
            {"id": "ct:10002", "source": "tvguide", "title": "Neznámý zdroj", "kind": "show",
             "image": "http://img.example.invalid/x.jpg", "rating": {"value": 9.0, "votes": 3}},
            {"id": "ct:10003", "source": "ct", "title": "Obrázek odjinud", "kind": "movie",
             "image": "//evil.example/x.jpg"},
            {"id": "../admin", "source": "ct", "title": "Špatné id"},
            {"id": "ct:10004", "source": "ct", "title": "  "},
            {"id": "ct:10001", "source": "ct", "title": "Pelíšky znovu"}]},
          {"id": "4003-dokumenty", "title": "Dokumenty", "cards": []},
          "nonsense"
        ]}
    """.trimIndent()

    @Test fun parsesHomeRowsAndCardsDefensively() {
        val rows = ArchiveParser.parseHome(200, home).rows
        assertEquals("empty rows and non-objects are left out", listOf("recordings", "3947-filmy"), rows.map { it.id })
        assertTrue(rows[0].more)
        assertFalse(rows[1].more)
        val rec = rows[0].cards.single()
        assertEquals(CardSource.REC, rec.source)
        assertEquals(CardKind.SERIES, rec.kind)
        assertEquals("/dvb/recording/b1-1700000000-1700001800/thumb.jpg", rec.image)
        assertEquals(2, rec.episodeCount)
        assertNull(rec.rating)
        val films = rows[1].cards
        assertEquals("bad ids, blank titles and duplicates are dropped", listOf("ct:10001", "ct:10002", "ct:10003"), films.map { it.id })
        val pelisky = films[0]
        assertEquals(CardSource.BOTH, pelisky.source)
        assertEquals(1999, pelisky.year)
        assertEquals(TmdbRating(8.14, 1234), pelisky.rating)
        assertEquals(115, pelisky.durationMinutes)
        val unknown = films[1]
        assertNull("an undocumented source stays unknown", unknown.source)
        assertNull(unknown.kind)
        assertNull("only https:// or a gateway path", unknown.image)
        assertNull("fewer than 20 votes = no rating", unknown.rating)
        assertNull(films[2].image)
    }

    @Test fun imagesAreHttpsOrGatewayPathsOnly() {
        assertTrue(ArchiveParser.isImage("https://img.example.invalid/a.jpg"))
        assertTrue(ArchiveParser.isImage("/dvb/details/image/w342/a.jpg"))
        assertTrue(ArchiveParser.isImage("/dvb/recording/b1-1-2/thumb.jpg"))
        for (bad in listOf("http://img/a.jpg", "//host/a.jpg", "/dvb/admin", "/dvb/details/image/../x",
            "/dvb/recording/../thumb.jpg", "/dvb/recording/b1/thumb.jpg?x=1", "javascript:x", "")) {
            assertFalse(bad, ArchiveParser.isImage(bad))
        }
        assertTrue(ArchiveParser.isGatewayPath("/dvb/details/image/w342/a.jpg"))
        assertFalse(ArchiveParser.isGatewayPath("https://img.example.invalid/a.jpg"))
        assertTrue(ArchiveParser.isThumbnail("/dvb/recording/b1-1-2/thumb.jpg"))
        assertFalse(ArchiveParser.isThumbnail("/dvb/details/image/w342/a.jpg"))
        val client = client(200, "{}")
        assertEquals("http://10.0.2.2:18098/dvb/details/image/w342/a.jpg", client.archiveImageUrl("/dvb/details/image/w342/a.jpg"))
        assertNull(client.archiveImageUrl("https://img.example.invalid/a.jpg"))
    }

    @Test fun parsesCategoryAndSearchPages() {
        val page = ArchiveParser.parsePage(200, """
            {"title": "Filmy", "genres": [{"id": "g1", "title": "Komedie"}, {"id": "g2"}, {"title": "x"}],
             "cards": [{"id": "ct:1", "source": "ct", "title": "A", "kind": "movie"}], "nextPage": 2}
        """.trimIndent())
        assertEquals("Filmy", page.title)
        assertEquals(listOf(ArchiveGenre("g1", "Komedie")), page.genres)
        assertEquals(2, page.nextPage)
        val search = ArchiveParser.parsePage(200, """{"cards": [], "nextPage": null}""")
        assertEquals("", search.title)
        assertNull(search.nextPage)
        assertNull(ArchiveParser.parsePage(200, """{"cards": [], "nextPage": "2"}""").nextPage)
    }

    private val show = """
        {"card": {"id": "ct:20001", "source": "both", "title": "Most!", "kind": "series", "year": 2019,
                  "image": "https://img.example.invalid/m.jpg", "episodeCount": 8},
         "description": "Seriál z Mostu.", "creators": ["Jan Prušinovský"], "countries": "CZ",
         "seasons": [{"id": "s1", "title": "1. řada"}, {"title": "bez id"}],
         "groups": [
           {"title": "1. řada", "episodes": [
             {"source": "ct", "idec": "219562279010001", "title": "1/8 Kovboj", "description": "D",
              "durationSeconds": 3180, "aired": "2019-01-09T19:00:00Z", "channel": "ČT1", "playable": true},
             {"source": "ct", "title": "bez idec", "playable": true},
             {"source": "ct", "idec": "219562279010002", "title": "2/8", "aired": "včera", "playable": false}]},
           {"title": "Nahráno z antény", "episodes": [
             {"source": "rec", "recordingId": "b1-1767985200-1767988800", "title": "Most! (3)",
              "durationSeconds": 3000, "aired": "2026-10-20T19:00:00Z", "channel": "ČT1 HD", "playable": true},
             {"source": "rec", "recordingId": "../x", "title": "špatné"},
             {"source": "web", "idec": "1", "title": "neznámý zdroj"}]}]}
    """.trimIndent()

    @Test fun parsesAShow() {
        val s = ArchiveParser.parseShow(200, show)
        assertEquals("ct:20001", s.card.id)
        assertEquals("Seriál z Mostu.", s.description)
        assertEquals("Jan Prušinovský", s.creators)
        assertEquals("a single string counts as a list", listOf("CZ"), s.countries)
        assertEquals(listOf(ArchiveSeason("s1", "1. řada")), s.seasons)
        assertEquals(listOf("1. řada", "Nahráno z antény"), s.groups.map { it.title })
        val ct = s.groups[0].episodes
        assertEquals(listOf("219562279010001", "219562279010002"), ct.map { it.idec })
        assertEquals(Instant.parse("2019-01-09T19:00:00Z"), ct[0].aired)
        assertEquals(3180.0, ct[0].durationSeconds, 0.0)
        assertTrue(ct[0].playable)
        assertNull("a bad date stays unknown", ct[1].aired)
        assertFalse(ct[1].playable)
        val rec = s.groups[1].episodes.single()
        assertEquals(EpisodeSource.REC, rec.source)
        assertEquals("b1-1767985200-1767988800", rec.recordingId)
        assertNull(rec.idec)
    }

    @Test fun showWithoutCardIsAnError() {
        assertEquals(ArchiveMessages.GENERIC, failure { ArchiveParser.parseShow(200, """{"groups": []}""") }.userMessage)
    }

    @Test fun errorsCarryTheGatewayText() {
        assertEquals(DvbMessages.BAD_KEY, failure { ArchiveParser.parseHome(401, "{}") }.userMessage)
        assertEquals("ČT neodpovídá", failure { ArchiveParser.parseHome(502, """{"error": "ČT neodpovídá"}""") }.userMessage)
        assertEquals(ArchiveMessages.GENERIC, failure { ArchiveParser.parseHome(500, "<html>") }.userMessage)
        assertEquals(ArchiveMessages.GENERIC, failure { ArchiveParser.parseHome(200, "[]") }.userMessage)
    }

    @Test fun clientPathsCarryTheKeyAndEncodeText() {
        val seen = ArrayList<Pair<String, Map<String, String>>>()
        val c = client(200, """{"rows": [], "cards": [], "card": {"id": "ct:1", "title": "A"}}""", seen)
        c.archiveHome()
        c.archiveCategory("3947-filmy", ArchiveSort.NEWEST, "g 1&x", 2)
        c.archiveCategory("recordings", ArchiveSort.POPULAR, null, 1)
        c.archiveSearch("Most a ž&q=1", 1)
        c.archiveShow("ct:20001")
        c.archiveShow("rec:mistrizastavarny", season = "s1", page = 3)
        assertEquals(
            listOf(
                "http://10.0.2.2:18098/dvb/archive",
                "http://10.0.2.2:18098/dvb/archive/category/3947-filmy?sort=newest&genre=g%201%26x&page=2",
                "http://10.0.2.2:18098/dvb/archive/category/recordings?sort=popular&page=1",
                "http://10.0.2.2:18098/dvb/archive/search?q=Most%20a%20%C5%BE%26q%3D1&page=1",
                "http://10.0.2.2:18098/dvb/archive/show/ct:20001",
                "http://10.0.2.2:18098/dvb/archive/show/rec:mistrizastavarny?season=s1&page=3",
            ),
            seen.map { it.first },
        )
        assertTrue(seen.all { it.second["Authorization"] == "Bearer ${config.key}" })
        c.archiveSearch("x".repeat(150), 1)
        assertTrue("at most 100 characters go out", seen.last().first.contains("q=${"x".repeat(100)}&"))
        assertEquals(ArchiveMessages.GENERIC, failure { c.archiveCategory("../admin", ArchiveSort.AZ, null, 1) }.userMessage)
        assertEquals(ArchiveMessages.GENERIC, failure { c.archiveShow("ct:1/../x") }.userMessage)
        assertEquals("rejected ids and slugs send nothing", 7, seen.size)
    }

    // -- rules ------------------------------------------------------------------------------------------

    /** "★ 7,4" follows the page's rating line rules (spec/fixtures/programme_page_cases.json "rating"). */
    @Test fun starFollowsTheGoldenRatingCases() {
        val cases = Fixtures.json("programme_page_cases.json").getValue("rating").jsonArray
        assertTrue(cases.size >= 5)
        for (case in cases) {
            val c = case.jsonObject
            val rating = TmdbRating(c.getValue("value").jsonPrimitive.double, c.getValue("votes").jsonPrimitive.int)
            val line = c["line"]?.jsonPrimitive?.takeIf { it.isString }?.content
            val expected = line?.let { "★ " + it.removePrefix("Hodnocení TMDB ").substringBefore(" (") }
            assertEquals("$c", expected, ArchiveRules.star(rating))
        }
        assertNull(ArchiveRules.star(null))
        assertEquals("★ 7,4", ArchiveRules.star(TmdbRating(7.35, 20)))
    }

    private fun card(source: CardSource?, kind: CardKind?, year: Int? = null, rating: TmdbRating? = null, image: String? = null) =
        ArchiveCard("ct:1", source, "A", kind, year = year, rating = rating, image = image)

    @Test fun cardLines() {
        assertEquals("1999 · film", ArchiveRules.metaLine(card(CardSource.CT, CardKind.MOVIE, 1999)))
        assertEquals("seriál", ArchiveRules.metaLine(card(CardSource.CT, CardKind.SERIES)))
        assertEquals("2001", ArchiveRules.metaLine(card(CardSource.CT, null, 2001)))
        assertEquals("", ArchiveRules.metaLine(card(CardSource.CT, null)))
        assertEquals(listOf(PageSource.CT), ArchiveRules.icons(CardSource.CT))
        assertEquals(listOf(PageSource.DVB), ArchiveRules.icons(CardSource.REC))
        assertEquals(listOf(PageSource.CT, PageSource.DVB), ArchiveRules.icons(CardSource.BOTH))
        assertEquals(emptyList<PageSource>(), ArchiveRules.icons(null))
    }

    @Test fun tmdbAttributionWhenACardSaysSo() {
        val home = ArchiveParser.parseHome(200, fixture("dvb_archive_home.json"))
        assertTrue(ArchiveRules.showsTmdb(home.rows.flatMap { it.cards }))
        val search = ArchiveParser.parsePage(200, fixture("dvb_archive_search.json"))
        assertFalse(ArchiveRules.showsTmdb(search.cards))
        assertFalse("a rating alone does not decide", ArchiveRules.showsTmdb(listOf(card(CardSource.REC, null, rating = TmdbRating(7.0, 200)))))
        assertEquals(ArchiveRules.TMDB_ATTRIBUTION, ArchiveRules.attribution(null))
        assertEquals(ArchiveRules.TMDB_ATTRIBUTION, ArchiveRules.attribution(" "))
        assertEquals("Věta z brány.", ArchiveRules.attribution("Věta z brány."))
    }

    @Test fun episodeCountPlurals() {
        assertEquals(listOf("díl", "díly", "díly", "díly", "dílů", "dílů", "dílů"), listOf(1, 2, 3, 4, 5, 21, 0).map(ArchiveRules::episodesWord))
        val s = ArchiveParser.parseShow(200, show)
        assertEquals("iVysílání a z antény · 2019 · seriál · CZ · 8 dílů", ArchiveRules.factsLine(s))
        assertEquals("iVysílání · 1 díl", ArchiveRules.factsLine(s.copy(card = s.card.copy(source = CardSource.CT, year = null, kind = null, episodeCount = 1), countries = emptyList())))
        assertEquals("Z antény · 3 díly", ArchiveRules.factsLine(s.copy(card = s.card.copy(source = CardSource.REC, year = null, kind = null, episodeCount = 3), countries = emptyList())))
    }

    @Test fun episodeLine() {
        val e = ArchiveParser.parseShow(200, show).groups[0].episodes[0]
        val now = Instant.parse("2019-10-25T10:00:00Z")
        assertEquals("1/8 Kovboj · 53 min · ČT1 9. 1.", ArchiveRules.episodeLine(e, now))
        // The aired day is Prague's: 23:30 UTC on 8 January is 0:30 on the 9th.
        assertEquals("1/8 Kovboj · 53 min · ČT1 9. 1.", ArchiveRules.episodeLine(e.copy(aired = Instant.parse("2019-01-08T23:30:00Z")), now))
        assertEquals("another year shows it", "1/8 Kovboj · 53 min · ČT1 9. 1. 2019", ArchiveRules.episodeLine(e, Instant.parse("2026-10-25T10:00:00Z")))
        // Prague's year: 31 Dec 23:30 UTC is already 1 January.
        assertEquals("1/8 Kovboj · 53 min · ČT1 1. 1.", ArchiveRules.episodeLine(e.copy(aired = Instant.parse("2019-12-31T23:30:00Z")), Instant.parse("2020-06-01T10:00:00Z")))
        assertEquals("1/8 Kovboj", ArchiveRules.episodeLine(e.copy(aired = null, channel = "", durationSeconds = 0.0), now))
        assertEquals("1/8 Kovboj · 9. 1.", ArchiveRules.episodeLine(e.copy(channel = "", durationSeconds = 10.0), now))
    }

    @Test fun searchNeedsTwoCharacters() {
        assertNull(ArchiveRules.searchQuery(""))
        assertNull(ArchiveRules.searchQuery(" a "))
        assertEquals("ab", ArchiveRules.searchQuery(" ab "))
        assertEquals(100, ArchiveRules.searchQuery("y".repeat(140))!!.length)
        assertEquals(500L, ArchiveRules.SEARCH_DEBOUNCE_MS)
    }

    @Test fun episodesPlayThroughTheExistingPaths() {
        val s = ArchiveParser.parseShow(200, show)
        val ct = s.groups[0].episodes[0]
        val request = ArchiveRules.request(ct) as PlayRequest.Programme
        assertEquals("CH_1", request.channelId)
        assertEquals("219562279010001", request.idec)
        assertEquals(Instant.parse("2019-01-09T19:00:00Z"), request.start)
        assertEquals(Instant.parse("2019-01-09T19:53:00Z"), request.end)
        assertTrue(request.isPlayableNow)
        assertNull(request.resumeSeconds)
        assertEquals(600.0, (ArchiveRules.request(ct, 600.0) as PlayRequest.Programme).resumeSeconds!!, 0.0)
        assertEquals("ct:219562279010001", ArchiveRules.resumeKey(ct))
        val rec = s.groups[1].episodes[0]
        val recorded = ArchiveRules.request(rec, 120.0) as PlayRequest.DvbRecorded
        assertEquals("b1-1767985200-1767988800", recorded.recordingId)
        assertFalse(recorded.fromStart)
        assertEquals(3000.0, recorded.lengthSeconds!!, 0.0)
        assertEquals(120.0, recorded.resumeSeconds!!, 0.0)
        assertEquals("dvb:b1-1767985200-1767988800", ArchiveRules.resumeKey(rec))
    }

    @Test fun channelNamesMapToCtIds() {
        assertEquals("CH_1", ArchiveRules.ctChannelId("ČT1"))
        assertEquals("CH_1", ArchiveRules.ctChannelId("ct1"))
        assertEquals("CH_4", ArchiveRules.ctChannelId("ČT sport"))
        assertEquals("CH_24", ArchiveRules.ctChannelId("CH_24"))
        assertNull(ArchiveRules.ctChannelId("ČT1 HD"))
        assertNull(ArchiveRules.ctChannelId(""))
    }

    @Test fun resumeCardOfAnEpisode() {
        val s = ArchiveParser.parseShow(200, show)
        val ct = ArchiveRules.resumeCard(s.groups[0].episodes[0], s.card)
        assertEquals("1/8 Kovboj", ct.title)
        assertEquals("Most!", ct.showTitle)
        assertEquals("https://img.example.invalid/m.jpg", ct.image)
        assertEquals("ct", ct.source)
        val rec = ArchiveRules.resumeCard(s.groups[1].episodes[0], s.card)
        assertEquals("dvb", rec.source)
        assertNotEquals(ct.source, rec.source)
        val film = s.card.copy(title = "1/8 Kovboj")
        assertNull("a film's own title is not repeated", ArchiveRules.resumeCard(s.groups[0].episodes[0], film).showTitle)
    }

    @Test fun continueCardsPlayFromTheSavedPosition() {
        val start = Instant.parse("2026-10-25T08:00:00Z")
        val ct = cz.cztvhub.core.ContinueItem(
            "ct:219562279010001",
            cz.cztvhub.core.ResumeEntry(600.0, 3180.0, start, cz.cztvhub.core.ResumeCard("1/8", "Most!", null, "ct", start, "CH_1")),
        )
        val request = ArchiveRules.continueRequest(ct) as PlayRequest.Programme
        assertEquals("CH_1", request.channelId)
        assertEquals("219562279010001", request.idec)
        assertEquals(start, request.start)
        assertEquals(start.plusSeconds(3180), request.end)
        assertEquals(600.0, request.resumeSeconds!!, 0.0)
        val dvb = cz.cztvhub.core.ContinueItem(
            "dvb:b1-1-2", cz.cztvhub.core.ResumeEntry(900.0, 1800.0, start, cz.cztvhub.core.ResumeCard("X", source = "dvb")),
        )
        val recorded = ArchiveRules.continueRequest(dvb) as PlayRequest.DvbRecorded
        assertEquals("b1-1-2", recorded.recordingId)
        assertEquals(900.0, recorded.resumeSeconds!!, 0.0)
        assertEquals(1800.0, recorded.lengthSeconds!!, 0.0)
        assertNull(ArchiveRules.continueRequest(dvb.copy(entry = dvb.entry.copy(card = null))))
        assertNull(ArchiveRules.continueRequest(dvb.copy(key = "psp:stream1")))
        val episode = ArchiveParser.parseShow(200, show).groups[0].episodes[0]
        val card = ArchiveRules.resumeCard(episode, ArchiveParser.parseShow(200, show).card)
        assertEquals(Instant.parse("2019-01-09T19:00:00Z"), card.start)
        assertEquals("CH_1", card.channelId)
    }

    // -- the shared samples (spec/fixtures/dvb/dvb_archive_*.json) -------------------------------------------

    private fun fixture(name: String): String = Fixtures.response("dvb/$name").body

    @Test fun sharedHome() {
        val home = ArchiveParser.parseHome(200, fixture("dvb_archive_home.json"))
        assertEquals(listOf("recordings", "3947-filmy", "3976-serialy", "4003-dokumenty", "4079-historie", "4106-priroda", "4055-rady-a-recepty"), home.rows.map { it.id })
        val recordings = home.rows[0]
        assertEquals("Naše nahrávky", recordings.title)
        assertEquals(3, recordings.cards.size)
        val zahrada = recordings.cards[0]
        assertEquals(CardSource.REC, zahrada.source)
        assertNull("no year in the sample", zahrada.year)
        assertTrue(ArchiveParser.isThumbnail(zahrada.image!!))
        assertFalse(zahrada.tmdb)
        val hora = recordings.cards[1]
        assertTrue(hora.tmdb)
        assertEquals("★ 7,1", ArchiveRules.star(hora.rating))
        assertEquals(CardSource.BOTH, home.rows[2].cards[0].source)
        assertNull(home.error)
        assertEquals("cards with tmdb=true come with the sentence", ArchiveRules.TMDB_ATTRIBUTION, home.attribution)
    }

    @Test fun sharedCategoryAndSearch() {
        val category = ArchiveParser.parsePage(200, fixture("dvb_archive_category.json"))
        assertEquals("Filmy", category.title)
        assertEquals(listOf(ArchiveGenre("3951", "Drama")), category.genres)
        assertEquals(listOf("ct:10000000002", "rec:tichahora"), category.cards.map { it.id })
        assertEquals("2019 · film", ArchiveRules.metaLine(category.cards[0]))
        assertNull(category.nextPage)
        assertEquals(ArchiveRules.TMDB_ATTRIBUTION, category.attribution)
        val search = ArchiveParser.parsePage(200, fixture("dvb_archive_search.json"))
        assertEquals(listOf("ct:10000000001"), search.cards.map { it.id })
        assertNull(search.nextPage)
        assertNull("no tmdb card, no sentence", search.attribution)
        assertTrue(ArchiveParser.isImage(search.cards.single().image!!))
    }

    @Test fun searchCardsWithoutKindShowNoKind() {
        val page = ArchiveParser.parsePage(200, """{"cards": [{"id": "ct:5", "source": "ct", "title": "Most!", "kind": null, "image": null, "rating": null, "tmdb": false}], "nextPage": null}""")
        val card = page.cards.single()
        assertNull(card.kind)
        assertNull(card.image)
        assertEquals("", ArchiveRules.metaLine(card))
    }

    @Test fun ivysilaniDownStillListsOurRecordings() {
        val body = """{"rows": [{"id": "recordings", "title": "Naše nahrávky", "cards": [{"id": "rec:a", "source": "rec", "title": "A", "kind": "series", "tmdb": false}], "more": false}], "error": "iVysílání teď neodpovídá"}"""
        val home = ArchiveParser.parseHome(200, body)
        assertEquals("iVysílání teď neodpovídá", home.error)
        assertEquals(listOf("recordings"), home.rows.map { it.id })
        val page = ArchiveParser.parsePage(200, """{"cards": [], "nextPage": null, "error": "iVysílání teď neodpovídá"}""")
        assertEquals("iVysílání teď neodpovídá", page.error)
        assertEquals("ČT neodpovídá", failure { ArchiveParser.parseShow(502, """{"error": "ČT neodpovídá"}""") }.userMessage)
    }

    @Test fun bothShowWhileCtIsDown() {
        val both = ArchiveParser.parseShow(200, fixture("dvb_archive_show_ctdown.json"))
        assertEquals(CardSource.BOTH, both.card.source)
        assertEquals("iVysílání teď neodpovídá", both.error)
        assertTrue(both.seasons.isEmpty())
        assertEquals(listOf("Nahráno z antény"), both.groups.map { it.title })
        val episode = both.groups.single().episodes.single()
        assertEquals(EpisodeSource.REC, episode.source)
        assertTrue(ArchiveRules.request(episode) is PlayRequest.DvbRecorded)
        assertFalse("no iVysílání group to page", both.groups.any(ArchiveRules::isCtGroup))
    }

    @Test fun sharedSeries() {
        val s = ArchiveParser.parseShow(200, fixture("dvb_archive_show_series.json"))
        assertEquals(CardKind.SERIES, s.card.kind)
        assertEquals(listOf("season:00000000000000000000aa01", "season:00000000000000000000aa02"), s.seasons.map { it.id })
        assertEquals("shown as it comes", "Režie: Jan Příklad. Hrají: Eva Vzorová, Petr Ukázka a další.", s.creators)
        assertNull(s.error)
        val (ct, rec) = s.groups
        assertEquals("season:00000000000000000000aa01", ct.season)
        assertEquals(2, ct.nextPage)
        assertEquals(20, ct.episodes.size)
        assertEquals("1/26 Smyšlený díl 1 · 52 min · ČT1 1. 4.", ArchiveRules.episodeLine(ct.episodes[0], Instant.parse("2026-10-25T10:00:00Z")))
        assertEquals("CH_1", ct.episodes[0].channelId)
        assertEquals("CH_1", (ArchiveRules.request(ct.episodes[0]) as PlayRequest.Programme).channelId)
        assertNull(s.attribution)
        assertEquals("Nahráno z antény", rec.title)
        assertNull(rec.season)
        assertNull(rec.nextPage)
        assertEquals(EpisodeSource.REC, rec.episodes.single().source)
        assertNull("our recordings have no CT channel", rec.episodes.single().channelId)
        assertEquals("Nova HD is no CT channel", "Nova HD", (ArchiveRules.request(rec.episodes.single()) as PlayRequest.DvbRecorded).channelId)
        assertEquals("iVysílání a z antény · seriál · 52 dílů", ArchiveRules.factsLine(s))
    }

    @Test fun sharedMovie() {
        val s = ArchiveParser.parseShow(200, fixture("dvb_archive_show_movie.json"))
        assertEquals(CardKind.MOVIE, s.card.kind)
        assertTrue(s.card.tmdb)
        val group = s.groups.single()
        assertEquals("Díly", group.title)
        assertNull(group.season)
        assertNull(group.nextPage)
        assertEquals("CH_2", group.episodes.single().channelId)
        assertEquals("CH_2", ArchiveRules.channelOf(group.episodes.single()))
        assertEquals(ArchiveRules.TMDB_ATTRIBUTION, s.attribution)
        assertEquals(ArchiveRules.TMDB_ATTRIBUTION, ArchiveRules.attribution(s.attribution))
        assertEquals("iVysílání · 2019 · film · Česko, Slovensko", ArchiveRules.factsLine(s))
    }

    @Test fun channelIdWinsOverTheDisplayName() {
        val e = ArchiveParser.parseShow(200, fixture("dvb_archive_show_movie.json")).groups.single().episodes.single()
        assertEquals("CH_2", e.channelId)
        assertEquals("CH_6", ArchiveRules.channelOf(e.copy(channelId = "CH_6")))
        assertEquals("without channelId the display name maps", "CH_2", ArchiveRules.channelOf(e.copy(channelId = null)))
        assertEquals("an unknown id falls back to the name", "CH_2", ArchiveRules.channelOf(e.copy(channelId = "CH_99")))
        assertNull("unknown either way = VOD only", ArchiveRules.channelOf(e.copy(channelId = null, channel = "Nova HD")))
        val parsed = ArchiveParser.parseShow(200, """{"card": {"id": "ct:1", "title": "A"}, "groups": [{"title": "Díly", "episodes": [
            {"source": "ct", "idec": "1", "title": "A", "channelId": "CH_24", "channel": "ČT24"},
            {"source": "ct", "idec": "2", "title": "B", "channelId": "../x"}]}]}""")
        assertEquals(listOf("CH_24", null), parsed.groups.single().episodes.map { it.channelId })
    }

    @Test fun seasonPageReplacesThenAppends() {
        val series = ArchiveParser.parseShow(200, fixture("dvb_archive_show_series.json"))
        val page = ArchiveParser.parseShow(200, fixture("dvb_archive_show_season_page.json"))
        val season2 = "season:00000000000000000000aa02"
        assertEquals(season2, page.groups[0].season)
        assertNull(page.groups[0].nextPage)
        val replaced = ArchiveRules.withSeasonPage(series, page, season2, append = false)
        assertEquals(listOf("II. řada", "Nahráno z antény"), replaced.groups.map { it.title })
        assertEquals(page.groups[0].episodes, replaced.groups[0].episodes)
        assertNotEquals(series.groups[0].episodes, replaced.groups[0].episodes)
        assertEquals(season2, replaced.groups[0].season)
        assertNull(replaced.groups[0].nextPage)
        assertEquals("our recordings stay", series.groups[1], replaced.groups[1])
        val season1 = "season:00000000000000000000aa01"
        val appended = ArchiveRules.withSeasonPage(series, page.copy(groups = listOf(page.groups[0].copy(season = season1))), season1, append = true)
        assertEquals(20 + page.groups[0].episodes.size, appended.groups[0].episodes.size)
        assertEquals(series.groups[0].episodes, appended.groups[0].episodes.take(20))
        assertTrue(ArchiveRules.isCtGroup(series.groups[0]))
        assertFalse(ArchiveRules.isCtGroup(series.groups[1]))
    }
}
