import Foundation
import XCTest
@testable import CTCore

/// spec/apps.md "Archiv (T13)": card lines, the "Pokračovat ve sledování" row, the archive client and parser.
final class ArchiveRulesTests: XCTestCase {
    private func card(source: ArchiveCard.Source = .ct, kind: ArchiveCard.Kind? = .movie, year: Int? = 2021,
                      image: ArchiveImage? = nil, rating: ArchiveCard.Rating? = nil, episodes: Int? = nil,
                      minutes: Int? = nil, tmdb: Bool = false) -> ArchiveCard {
        ArchiveCard(id: "ct:1", source: source, title: "Pelíšky", kind: kind, year: year, image: image, rating: rating,
                    episodeCount: episodes, durationMinutes: minutes, tmdb: tmdb)
    }

    /// "★ 7,4" follows the rating line of programme_page_cases.json: the same number, the same 20-vote limit.
    func testRatingBadgeFollowsTheGoldenRatingLines() throws {
        let cases = try Fixture.json("programme_page_cases")["rating"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 6)
        for item in cases {
            let rating = ArchiveCard.Rating(value: (item["value"] as! NSNumber).doubleValue, votes: item["votes"] as! Int)
            let badge = ArchiveRules.ratingBadge(rating)
            guard let line = item["line"] as? String else {
                XCTAssertNil(badge, "\(rating)")
                continue
            }
            // "Hodnocení TMDB 6,3 (21 hlasů)" → "★ 6,3"
            let number = line.split(separator: " ")[2]
            XCTAssertEqual(badge, "★ \(number)", line)
        }
        XCTAssertEqual(ArchiveRules.ratingBadge(ArchiveCard.Rating(value: 7.44, votes: 20)), "★ 7,4")
        XCTAssertNil(ArchiveRules.ratingBadge(nil))
    }

    func testMetaLineAndKinds() {
        XCTAssertEqual(ArchiveRules.metaLine(card()), "2021 · film")
        XCTAssertEqual(ArchiveRules.metaLine(card(kind: .series, year: nil)), "seriál")
        // ČT's search knows neither the kind nor the year
        XCTAssertEqual(ArchiveRules.metaLine(card(kind: nil)), "2021")
        XCTAssertEqual(ArchiveRules.metaLine(card(kind: nil, year: nil)), "")
        XCTAssertNotEqual(ArchiveRules.metaLine(card(kind: .movie)), ArchiveRules.metaLine(card(kind: .series)))
    }

    func testSourceIcons() {
        XCTAssertEqual(ArchiveRules.icons(.ct), [.ct])
        XCTAssertEqual(ArchiveRules.icons(.rec), [.dvb])
        XCTAssertEqual(ArchiveRules.icons(.both), [.ct, .dvb])
    }

    /// The gateway decides (`"tmdb": true`).
    func testTMDbDataNeedsTheAttribution() {
        XCTAssertFalse(ArchiveRules.showsTMDb(card()))
        XCTAssertTrue(ArchiveRules.showsTMDb(card(tmdb: true)))
    }

    func testTexts() {
        XCTAssertEqual(ArchiveRules.sourceText(.ct), "iVysílání")
        XCTAssertEqual(ArchiveRules.sourceText(.rec), "Z antény")
        XCTAssertEqual(ArchiveRules.sourceText(.both), "iVysílání a z antény")
        XCTAssertEqual(ArchiveSort.allCases.map(\.title), ["Nejsledovanější", "Naposledy vysílané", "A–Z"])
        XCTAssertEqual(ArchiveRules.creatorsLine(["Režie: Jan Příklad."]), "Režie: Jan Příklad.", "as sent, no label")
        XCTAssertNil(ArchiveRules.creatorsLine([]))
        XCTAssertEqual(ArchiveRules.fromStart, "Přehrát od začátku")
    }

    func testEpisodeCountPlurals() {
        XCTAssertEqual(ArchiveRules.episodesText(1), "1 díl")
        XCTAssertEqual(ArchiveRules.episodesText(2), "2 díly")
        XCTAssertEqual(ArchiveRules.episodesText(4), "4 díly")
        XCTAssertEqual(ArchiveRules.episodesText(5), "5 dílů")
        XCTAssertEqual(ArchiveRules.episodesText(26), "26 dílů")
        XCTAssertEqual(ArchiveRules.episodesText(0), "0 dílů")
    }

    func testShowFacts() {
        let series = ArchiveShow(card: card(source: .both, kind: .series, year: 2005, episodes: 26), description: "",
                                 creators: [], countries: ["Česko"], seasons: [], groups: [], attribution: "", error: nil)
        XCTAssertEqual(ArchiveRules.showFacts(series), "iVysílání a z antény · 2005 · seriál · Česko · 26 dílů")
        let film = ArchiveShow(card: card(source: .rec, year: nil, minutes: 110), description: "", creators: [],
                               countries: [], seasons: [], groups: [], attribution: "", error: nil)
        XCTAssertEqual(ArchiveRules.showFacts(film), "Z antény · film · 110 min")
    }

    /// "1/26 Od začátku · 53 min · ČT1 9. 1." (spec/apps.md), the year only when it is not this year.
    func testEpisodeLine() {
        let episode = ArchiveEpisode(source: .ct(idec: "1"), title: "1/26 Od začátku", durationSeconds: 3180,
                                     aired: utc("2026-01-09T19:00:00Z"), channel: "ČT1", playable: true)
        XCTAssertEqual(ArchiveRules.episodeLine(episode, now: utc("2026-10-25T10:00:00Z")),
                       "1/26 Od začátku · 53 min · ČT1 9. 1.")
        XCTAssertEqual(ArchiveRules.episodeLine(episode, now: utc("2027-02-01T10:00:00Z")),
                       "1/26 Od začátku · 53 min · ČT1 9. 1. 2026")
        // the day is Prague's: 23:30 UTC on 8 Jan is already 9 Jan
        let late = ArchiveEpisode(source: .rec(recordingID: "b1-1-2"), title: "", durationSeconds: 0,
                                  aired: utc("2026-01-08T23:30:00Z"), channel: "", playable: true)
        XCTAssertEqual(ArchiveRules.episodeLine(late, now: utc("2026-10-25T10:00:00Z")), "9. 1.")
    }

    func testButtons() {
        XCTAssertEqual(ArchiveRules.episodeButton(resumeAt: nil), "Přehrát")
        XCTAssertEqual(ArchiveRules.episodeButton(resumeAt: 1390), "Pokračovat od 23:10")
        XCTAssertEqual(ArchiveRules.filmButtons(resume: false), [.startOver])
        XCTAssertEqual(ArchiveRules.filmButtons(resume: true), [.resume, .startOver])
    }

    func testSearchQuery() {
        XCTAssertNil(ArchiveRules.searchQuery(""))
        XCTAssertNil(ArchiveRules.searchQuery(" a "))
        XCTAssertEqual(ArchiveRules.searchQuery(" ab "), "ab")
        XCTAssertEqual(ArchiveRules.searchQuery("čš"), "čš")
        XCTAssertEqual(ArchiveRules.searchQuery(String(repeating: "x", count: 150))?.count, 100)
        XCTAssertEqual(ArchiveRules.searchDebounce, .milliseconds(500))
    }

    func testImageSourcesAreOnlyCTHttpsOrGatewayRoutes() {
        XCTAssertEqual(ArchiveImage("https://img.example.invalid/porad.jpg"),
                       .ct(URL(string: "https://img.example.invalid/porad.jpg")!))
        XCTAssertEqual(ArchiveImage("/dvb/details/image/w342/abc.jpg"), .poster(path: "/dvb/details/image/w342/abc.jpg"))
        XCTAssertEqual(ArchiveImage("/dvb/recording/b1-1-2/thumb.jpg"), .thumbnail(recordingID: "b1-1-2"))
        for bad in ["http://img.example.invalid/a.jpg", "//evil.example/a.jpg", "/dvb/live/1.m3u8", "/etc/passwd",
                    "/dvb/details/image/../../admin", "/dvb/recording/../thumb.jpg", "/dvb/recording/a/b/thumb.jpg",
                    "/dvb/recording/%2E%2E/thumb.jpg", "file:///etc/passwd", "https://user:pw@example.invalid/a.jpg", ""] {
            XCTAssertNil(ArchiveImage(bad), bad)
        }
        XCTAssertEqual(ArchiveImage.thumbnail(recordingID: "b1-1-2").stored, "/dvb/recording/b1-1-2/thumb.jpg")
    }

    func testEpisodeAsProgrammeForTheDecisionRules() {
        let (programme, channel) = ArchiveRules.programme(idec: "217", title: "Pelíšky", episode: "", aired: nil,
                                                          duration: 6000, playable: true, channel: ArchiveRules.ctChannel("ČT1"))
        XCTAssertEqual(channel?.id, "CH_1")
        XCTAssertEqual(programme.channel, "ct1")
        XCTAssertEqual(DecisionRules.action(for: programme, now: Date()), .vod, "no broadcast time = long past")
        XCTAssertEqual(ArchiveRules.ctChannel("ČT :D")?.id, "CH_5")
        XCTAssertEqual(ArchiveRules.ctChannel("CT sport")?.id, "CH_4")
        XCTAssertEqual(ArchiveRules.ctChannel("ct24")?.id, "CH_24")
        XCTAssertNil(ArchiveRules.ctChannel("ČT3"))
        XCTAssertNil(ArchiveRules.ctChannel("Nova"))
    }
}

@MainActor
final class ArchiveContinueRowTests: XCTestCase {
    func testGoldenContinueRow() throws {
        let cases = try Fixture.json("archive_cases")["continueRow"] as! [[String: Any]]
        XCTAssertFalse(cases.isEmpty)
        for item in cases {
            var entries: [String: ResumeEntry] = [:]
            for entry in item["entries"] as! [[String: Any]] {
                let card = (entry["title"] as? String).map { ResumeCard(title: $0, source: .ct) }
                entries[entry["key"] as! String] = ResumeEntry(progressSeconds: 600, lengthSeconds: 3600,
                                                               savedAt: utc(entry["savedAt"] as! String), card: card)
            }
            XCTAssertEqual(ArchiveRules.continueRow(entries).map(\.key), item["row"] as! [String], item["name"] as! String)
        }
    }

    func testAtMostTwentyNewestFirst() {
        var entries: [String: ResumeEntry] = [:]
        for index in 0..<25 {
            entries["ct:\(index)"] = ResumeEntry(progressSeconds: 600, lengthSeconds: 3600,
                                                 savedAt: utc("2026-10-25T08:00:00Z").addingTimeInterval(Double(index) * 60),
                                                 card: ResumeCard(title: "Díl \(index)", source: .ct))
        }
        let row = ArchiveRules.continueRow(entries)
        XCTAssertEqual(row.count, 20)
        XCTAssertEqual(row.first?.key, "ct:24")
        XCTAssertEqual(row.last?.key, "ct:5")
    }

    /// Entries saved before T13 (no card in the JSON) still decode and offer "Pokračovat", but stay out of the row.
    func testOldEntriesKeepWorking() throws {
        let backend = MemoryBackend()
        backend.values[ResumeStore.storageKey] = Data(#"""
            {"ct:old": {"progressSeconds": 600, "lengthSeconds": 3600, "savedAt": 783000000}}
            """#.utf8)
        let store = ResumeStore(backend: backend, clock: { Date(timeIntervalSinceReferenceDate: 783_000_100) })
        XCTAssertEqual(store.offer(for: "ct:old"), 600)
        XCTAssertNil(store.entry(for: "ct:old")?.card)
        XCTAssertTrue(store.continueRow().isEmpty)
    }

    func testSaveKeepsTheCardAndALaterSaveWithoutOneKeepsIt() {
        let backend = MemoryBackend()
        var now = utc("2026-10-25T08:00:00Z")
        let store = ResumeStore(backend: backend, clock: { now })
        let card = ResumeCard(title: "1/26 Od začátku", showTitle: "Vyprávěj", image: "https://img.example.invalid/a.jpg",
                              source: .ct, start: utc("2026-01-09T19:00:00Z"), channel: "CH_1")
        store.save(ResumeTarget(key: "ct:1", programmeStart: utc("2026-01-09T19:00:00Z"), length: 3600, card: card),
                   progress: 600)
        now = now.addingTimeInterval(60)
        store.save(ResumeTarget(key: "ct:1", programmeStart: utc("2026-01-09T19:00:00Z"), length: 3600), progress: 700)
        // a fresh store reads it back from the backend
        let reread = ResumeStore(backend: backend, clock: { now })
        XCTAssertEqual(reread.entry(for: "ct:1")?.card, card)
        XCTAssertEqual(reread.entry(for: "ct:1")?.progressSeconds, 700)
        XCTAssertEqual(reread.continueRow().map(\.key), ["ct:1"])
        reread.remove("ct:1")
        XCTAssertTrue(reread.continueRow().isEmpty, "Odebrat z řady deletes the entry")
    }

    func testGuideTargetsCarryACard() {
        let programme = Programme(channel: "ct1", idec: "9", title: "Hercule Poirot", start: utc("2026-10-24T20:15:00Z"),
                                  end: utc("2026-10-24T22:00:00Z"), isPlayableNow: true, episodeTitle: "Vražda v Orient expresu",
                                  imageURL: URL(string: "https://img.example.invalid/p.jpg"))
        let ct = ResumeTarget.ct(programme).card
        XCTAssertEqual(ct, ResumeCard(title: "Vražda v Orient expresu", showTitle: "Hercule Poirot",
                                      image: "https://img.example.invalid/p.jpg", source: .ct,
                                      start: utc("2026-10-24T20:15:00Z"), channel: "CH_1"))
        let event = DVBEvent(id: "e", channel: "c", title: "Zprávy", start: utc("2026-10-24T17:00:00Z"),
                             end: utc("2026-10-24T17:30:00Z"))
        let dvb = ResumeTarget.antenna(recordingID: "b1-1-2", event: event, channelName: "Nova").card
        XCTAssertEqual(dvb, ResumeCard(title: "Zprávy", image: "/dvb/recording/b1-1-2/thumb.jpg", source: .dvb,
                                       start: utc("2026-10-24T17:00:00Z"), channel: "Nova"))
    }
}

final class ArchiveParserTests: XCTestCase {
    private func json(_ object: Any) -> Data { try! JSONSerialization.data(withJSONObject: object) }

    private let goodCard: [String: Any] = [
        "id": "ct:1", "source": "both", "title": "Ordinace v růžové zahradě 2", "kind": "series", "year": 2005,
        "image": "https://img.example.invalid/o.jpg", "rating": ["value": 6.2, "votes": 31], "genres": ["Drama"],
        "episodeCount": 412, "unknownField": ["ignored": true],
    ]

    func testHomeDropsBadCardsAndEmptyRows() throws {
        let body = json(["rows": [
            ["id": "recordings", "title": "Naše nahrávky", "more": true, "cards": [
                goodCard,
                ["id": "rec:x", "source": "rec", "title": "Bez obrázku", "kind": "movie", "image": "http://evil.example/x.jpg"],
                ["id": "", "source": "ct", "title": "bez id"],
                ["id": "ct:2", "source": "satellite", "title": "neznámý zdroj"],
                goodCard,  // duplicate
            ]],
            ["id": "3947-filmy", "title": "Filmy", "cards": []],
            "not a row",
        ]])
        let home = try ArchiveParser.home(status: 200, body: body)
        XCTAssertEqual(home.rows.map(\.id), ["recordings"])
        XCTAssertEqual(home.rows[0].cards.map(\.id), ["ct:1", "rec:x"])
        XCTAssertTrue(home.rows[0].more)
        let card = home.rows[0].cards[0]
        XCTAssertEqual(card.source, .both)
        XCTAssertEqual(card.kind, .series)
        XCTAssertEqual(card.year, 2005)
        XCTAssertEqual(card.rating, ArchiveCard.Rating(value: 6.2, votes: 31))
        XCTAssertEqual(card.episodeCount, 412)
        XCTAssertEqual(card.image, .ct(URL(string: "https://img.example.invalid/o.jpg")!))
        XCTAssertNil(home.rows[0].cards[1].image, "plain http dropped")
        XCTAssertEqual(home.attribution, ProgrammeDetails.defaultAttribution)
    }

    func testCategoryPage() throws {
        let body = json(["title": "Filmy", "genres": [["id": "4012", "title": "Komedie"], ["id": 4013, "title": "Drama"], ["title": "bez id"]],
                         "cards": [goodCard], "nextPage": 2])
        let page = try ArchiveParser.page(status: 200, body: body)
        XCTAssertEqual(page.title, "Filmy")
        XCTAssertEqual(page.genres, [ArchiveGenre(id: "4012", title: "Komedie"), ArchiveGenre(id: "4013", title: "Drama")])
        XCTAssertEqual(page.nextPage, 2)
        XCTAssertNil(try ArchiveParser.page(status: 200, body: json(["cards": [], "nextPage": NSNull()])).nextPage)
    }

    func testShowWithGroups() throws {
        let body = json([
            "card": goodCard, "description": "Seriál z nemocnice.", "creators": ["Jan Novák"], "countries": "Česko",
            "seasons": [["id": "s1", "title": "1. řada"], ["id": "s2", "title": "2. řada"]],
            "groups": [
                ["title": "1. řada", "nextPage": 2, "episodes": [
                    ["source": "ct", "idec": "21", "title": "1/26 Od začátku", "durationSeconds": 3180,
                     "aired": "2026-01-09T19:00:00Z", "channel": "ČT1", "playable": true],
                    ["source": "ct", "title": "bez idec"],
                    ["source": "ct", "idec": "22", "title": "2/26", "playable": false],
                ]],
                ["title": "Nahráno z antény", "episodes": [
                    ["source": "rec", "recordingId": "b1-100-200", "title": "Ordinace v růžové zahradě 2 (412)",
                     "durationSeconds": 3000, "aired": "2026-10-20T18:00:00Z", "channel": "Nova", "playable": true],
                ]],
            ],
        ])
        let show = try ArchiveParser.show(status: 200, body: body)
        XCTAssertEqual(show.description, "Seriál z nemocnice.")
        XCTAssertEqual(show.creators, ["Jan Novák"])
        XCTAssertEqual(show.countries, ["Česko"])
        XCTAssertEqual(show.seasons.map(\.id), ["s1", "s2"])
        XCTAssertEqual(show.groups.count, 2)
        XCTAssertEqual(show.groups[0].episodes.map(\.resumeKey), ["ct:21", "ct:22"])
        XCTAssertEqual(show.groups[0].nextPage, 2)
        XCTAssertFalse(show.groups[0].episodes[1].playable)
        XCTAssertEqual(show.groups[0].episodes[0].aired, utc("2026-01-09T19:00:00Z"))
        XCTAssertFalse(show.groups[0].isRecordings)
        XCTAssertTrue(show.groups[1].isRecordings)
        XCTAssertEqual(show.groups[1].episodes[0].resumeKey, "dvb:b1-100-200")
        XCTAssertThrowsError(try ArchiveParser.show(status: 200, body: json(["groups": []])))
    }

    func testSeasonPageInBothShapes() throws {
        let episode: [String: Any] = ["source": "ct", "idec": "31", "title": "1/13", "durationSeconds": 60]
        let flat = try ArchiveParser.episodePage(status: 200, body: json(["episodes": [episode], "nextPage": 3]))
        XCTAssertEqual(flat.episodes.map(\.resumeKey), ["ct:31"])
        XCTAssertEqual(flat.nextPage, 3)
        let grouped = try ArchiveParser.episodePage(status: 200, body: json(["card": goodCard, "groups": [
            ["title": "2. řada", "episodes": [episode], "nextPage": 2],
        ]]))
        XCTAssertEqual(grouped.episodes.map(\.resumeKey), ["ct:31"])
        XCTAssertEqual(grouped.nextPage, 2)
    }

    /// iVysílání down: 200 with the gateway's text, our recordings still listed.
    func testErrorInsideAnAnswer() throws {
        let body = json(["error": "iVysílání teď neodpovídá", "rows": [["id": "recordings", "title": "Naše nahrávky",
                                                                         "cards": [goodCard]]], "cards": [goodCard]])
        let home = try ArchiveParser.home(status: 200, body: body)
        XCTAssertEqual(home.error, "iVysílání teď neodpovídá")
        XCTAssertEqual(home.rows.count, 1)
        XCTAssertEqual(try ArchiveParser.page(status: 200, body: body).error, "iVysílání teď neodpovídá")
        XCTAssertNil(try ArchiveParser.page(status: 200, body: json(["cards": []])).error)
        XCTAssertNil(try ArchiveParser.home(status: 200, body: json(["rows": []])).error)
    }

    /// `channelId` is the playback channel (null = VOD only); `channel` only the display name.
    func testEpisodeChannelID() throws {
        func episode(_ extra: [String: Any]) -> ArchiveEpisode? {
            ArchiveParser.episode(["source": "ct", "idec": "1", "channel": "ČT1"].merging(extra) { $1 })
        }
        XCTAssertEqual(episode(["channelId": "CH_24"])?.ctChannel?.id, "CH_24", "channelId first")
        XCTAssertEqual(episode(["channelId": NSNull()])?.ctChannel?.id, "CH_1", "null: the display name")
        XCTAssertEqual(episode(["channelId": "CH_99"])?.ctChannel?.id, "CH_1", "unknown id: the display name")
        XCTAssertEqual(episode([:])?.ctChannel?.id, "CH_1", "missing: the display name")
        XCTAssertNil(episode(["channelId": NSNull(), "channel": "Nova HD"])?.ctChannel, "unknown: VOD only")
        XCTAssertEqual(episode([:])?.channel, "ČT1")
    }

    func testKindMayBeNull() throws {
        let item = goodCard.merging(["kind": NSNull()]) { $1 }
        XCTAssertNil(ArchiveParser.card(item)?.kind)
        XCTAssertEqual(ArchiveParser.card(goodCard)?.tmdb, false)
        XCTAssertEqual(ArchiveParser.card(goodCard.merging(["tmdb": true]) { $1 })?.tmdb, true)
    }

    func testGatewayErrorsCarryItsText() {
        let body = json(["error": "Archiv je vypnutý"])
        XCTAssertThrowsError(try ArchiveParser.home(status: 503, body: body)) { error in
            XCTAssertEqual((error as? CTError)?.userMessage, "Archiv je vypnutý")
        }
        XCTAssertThrowsError(try ArchiveParser.home(status: 200, body: Data("[]".utf8)))
    }
}

final class ArchiveClientTests: XCTestCase {
    let config = GatewayConfig(baseURL: URL(string: "http://gw.local:8098")!, key: "k3y")

    func testPathsQueriesAndKey() async throws {
        let transport = FakeTransport()
        let empty = HTTPResponse(status: 200, body: Data(#"{"rows": [], "cards": [], "episodes": []}"#.utf8))
        transport.on("/dvb/archive", empty)
        let card = #"{"card": {"id": "rec:mistrizastavarny", "source": "rec", "title": "Mistři", "kind": "series"}}"#
        transport.on("/dvb/archive/show/", HTTPResponse(status: 200, body: Data(card.utf8)))
        let client = DVBClient(config: config, transport: transport)
        _ = try await client.archiveHome()
        _ = try await client.archiveCategory("3947-filmy", sort: .newest, genre: "4012", page: 2)
        _ = try await client.archiveCategory("recordings", sort: .az, genre: nil, page: 1)
        _ = try await client.archiveSearch("a&b=c+d", page: 1)
        _ = try await client.archiveShow("rec:mistrizastavarny")
        _ = try await client.archiveSeason(show: "ct:1", season: "s 2", page: 3)
        let urls = transport.requests.map { $0.url!.absoluteString }
        XCTAssertEqual(urls, [
            "http://gw.local:8098/dvb/archive",
            "http://gw.local:8098/dvb/archive/category/3947-filmy?sort=newest&genre=4012&page=2",
            "http://gw.local:8098/dvb/archive/category/recordings?sort=az&page=1",
            "http://gw.local:8098/dvb/archive/search?q=a%26b%3Dc%2Bd&page=1",
            "http://gw.local:8098/dvb/archive/show/rec:mistrizastavarny",
            "http://gw.local:8098/dvb/archive/show/ct:1?season=s%202&page=3",
        ])
        XCTAssertTrue(transport.requests.allSatisfy { $0.value(forHTTPHeaderField: "Authorization") == "Bearer k3y" })
    }

    func testSearchTextIsCappedAt100() async throws {
        let transport = FakeTransport()
        transport.on("/dvb/archive/search", HTTPResponse(status: 200, body: Data(#"{"cards": []}"#.utf8)))
        _ = try await DVBClient(config: config, transport: transport).archiveSearch(String(repeating: "x", count: 150), page: 1)
        let query = URLComponents(url: transport.requests[0].url!, resolvingAgainstBaseURL: false)!
            .queryItems!.first { $0.name == "q" }!.value!
        XCTAssertEqual(query.count, 100)
    }
}

final class ArchivePlaybackTests: XCTestCase {
    let now = utc("2026-10-17T18:00:00Z")

    private func planner(_ transport: FakeTransport) -> PlaybackPlanner {
        let now = now
        return PlaybackPlanner(client: CTClient(transport: transport), clock: { now })
    }

    func testOldEpisodePlaysVODAtTheResumePoint() async throws {
        let transport = FakeTransport()
        try transport.on("/media/external/", fixture: "vod_ok")
        let (programme, channel) = ArchiveRules.programme(idec: "21", title: "Vyprávěj", episode: "1/26", aired:
                                                          utc("2026-01-09T19:00:00Z"), duration: 3180, playable: true,
                                                          channel: ArchiveRules.ctChannel("ČT1"))
        let source = try await planner(transport).resolve(.episode(programme, channel: channel), resumeAt: 600)
        XCTAssertEqual(source.mode, .vod)
        XCTAssertEqual(source.startPosition, 600)
    }

    /// Began < 3 h ago: the buffer, as the decision rules say; without a known channel only the archive is left.
    func testRecentEpisodeUsesTheBufferWhenTheChannelIsKnown() async throws {
        let transport = FakeTransport()
        try transport.on("/channel/", fixture: "live_CH_1")
        try transport.on("/media/external/", fixture: "vod_ok")
        let aired = utc("2026-10-17T16:00:00Z")
        let (known, ct1) = ArchiveRules.programme(idec: "21", title: "Zprávy", episode: "", aired: aired, duration: 1800,
                                                  playable: true, channel: ArchiveRules.ctChannel("ČT1"))
        let buffered = try await planner(transport).resolve(.episode(known, channel: ct1))
        XCTAssertEqual(buffered.mode, .startOver(seekDate: utc("2026-10-17T15:59:30Z"), offset: 3570))
        let (unknown, none) = ArchiveRules.programme(idec: "22", title: "Web", episode: "", aired: aired, duration: 1800,
                                                     playable: true, channel: ArchiveRules.ctChannel("ČT3"))
        XCTAssertNil(none)
        let vod = try await planner(transport).resolve(.episode(unknown, channel: none))
        XCTAssertEqual(vod.mode, .vod)
    }

    func testUnplayableEpisodeShowsCTsText() async throws {
        let transport = FakeTransport()
        try transport.on("/media/external/", fixture: "vod_no_licence")
        let (programme, channel) = ArchiveRules.programme(idec: "23", title: "Film", episode: "", aired: nil, duration: 6000,
                                                          playable: false, channel: ArchiveRules.ctChannel("ČT2"))
        do {
            _ = try await planner(transport).resolve(.episode(programme, channel: channel))
            XCTFail("expected CT's reason")
        } catch let error as CTError {
            XCTAssertFalse(error.userMessage.isEmpty)
        }
    }
}

/// The gateway's shared sample answers (spec/fixtures/dvb/dvb_archive_*.json).
final class ArchiveFixtureTests: XCTestCase {
    private func parse<T>(_ name: String, _ parser: (Int, Data) throws -> T) throws -> T {
        let response = try Fixture.response("dvb/dvb_archive_\(name)")
        return try parser(response.status, response.body)
    }

    func testHome() throws {
        let home = try parse("home", ArchiveParser.home)
        XCTAssertEqual(home.rows.first?.id, "recordings", "Naše nahrávky first")
        XCTAssertEqual(home.rows.map(\.id), ["recordings", "3947-filmy", "3976-serialy", "4003-dokumenty", "4079-historie",
                                             "4106-priroda", "4055-rady-a-recepty"])
        let card = home.rows[0].cards[0]
        XCTAssertEqual(card.source, .rec)
        XCTAssertEqual(card.image, .thumbnail(recordingID: "b0b3e1d5a7c9f4e2a8b6d4c2e0f1a3b5c-1792294200-1792296900"))
        XCTAssertFalse(ArchiveRules.showsTMDb(card))
        XCTAssertTrue(home.rows.allSatisfy { !$0.cards.isEmpty && $0.cards.allSatisfy { $0.image != nil } })
    }

    func testCategoryWithTMDbCard() throws {
        let page = try parse("category", ArchiveParser.page)
        XCTAssertEqual(page.title, "Filmy")
        XCTAssertEqual(page.genres, [ArchiveGenre(id: "3951", title: "Drama")])
        XCTAssertNil(page.nextPage)
        let card = try XCTUnwrap(page.cards.first)
        XCTAssertTrue(card.tmdb)
        XCTAssertTrue(ArchiveRules.showsTMDb(card))
        XCTAssertEqual(ArchiveRules.ratingBadge(card.rating), "★ 6,4")
        XCTAssertEqual(ArchiveRules.metaLine(card), "2019 · film")
        XCTAssertEqual(card.image, .ct(URL(string: "https://img.example.invalid/archive/card/10000000002.jpg")!))
    }

    func testSearchMergedCard() throws {
        let page = try parse("search", ArchiveParser.page)
        XCTAssertEqual(page.cards.first?.source, .both)
        XCTAssertEqual(page.cards.first.map { ArchiveRules.icons($0.source) }, [.ct, .dvb])
    }

    func testSeriesShow() throws {
        let show = try parse("show_series", ArchiveParser.show)
        XCTAssertEqual(show.card.kind, .series)
        XCTAssertEqual(show.seasons.count, 2)
        XCTAssertEqual(show.creators, ["Režie: Jan Příklad. Hrají: Eva Vzorová, Petr Ukázka a další."])
        XCTAssertEqual(show.groups.count, 2)
        let season = show.groups[0]
        XCTAssertFalse(season.isRecordings)
        XCTAssertEqual(season.season, show.seasons[0].id)
        XCTAssertEqual(season.nextPage, 2)
        XCTAssertEqual(season.episodes.count, 20)
        XCTAssertEqual(ArchiveRules.episodeLine(season.episodes[0], now: utc("2026-10-25T10:00:00Z")),
                       "1/26 Smyšlený díl 1 · 52 min · ČT1 1. 4.")
        let recordings = show.groups[1]
        XCTAssertTrue(recordings.isRecordings)
        XCTAssertNil(recordings.season)
        XCTAssertEqual(recordings.title, ArchiveRules.recordingsGroupTitle)
        XCTAssertEqual(recordings.episodes[0].resumeKey,
                       "dvb:b0b3e1d5a7c9f4e2a8b6d4c2e0f1a3b5c-1792294200-1792296900")
        XCTAssertTrue(TimelineIDs.isContinuous("b0b3e1d5a7c9f4e2a8b6d4c2e0f1a3b5c-1792294200-1792296900"),
                      "our recordings play in the timeline player")
    }

    /// The answer's attribution exactly when a card carries TMDb data; else the standard sentence.
    func testAttributionFollowsTMDbCards() throws {
        for name in ["home", "category", "search"] {
            let response = try Fixture.response("dvb/dvb_archive_\(name)")
            let sent = (try JSONSerialization.jsonObject(with: response.body) as? [String: Any])?["attribution"] as? String
            let cards: [ArchiveCard] = name == "home"
                ? try ArchiveParser.home(status: response.status, body: response.body).rows.flatMap(\.cards)
                : try ArchiveParser.page(status: response.status, body: response.body).cards
            XCTAssertEqual(sent != nil, cards.contains(where: ArchiveRules.showsTMDb), name)
        }
        XCTAssertEqual(try parse("search", ArchiveParser.page).attribution, ProgrammeDetails.defaultAttribution)
    }

    /// A `both` show while ČT is down: 200, no seasons, only our group, the gateway's text, no attribution.
    func testShowWhileCTIsDown() throws {
        let show = try parse("show_ctdown", ArchiveParser.show)
        XCTAssertEqual(show.card.source, .both)
        XCTAssertEqual(show.error, "iVysílání teď neodpovídá")
        XCTAssertTrue(show.seasons.isEmpty)
        XCTAssertEqual(show.groups.map(\.isRecordings), [true])
        XCTAssertEqual(show.groups.first?.title, ArchiveRules.recordingsGroupTitle)
        XCTAssertEqual(show.attribution, ProgrammeDetails.defaultAttribution)
    }

    func testSeasonPage() throws {
        let page = try parse("show_season_page", ArchiveParser.episodePage)
        XCTAssertEqual(page.episodes.count, 6)
        XCTAssertEqual(page.episodes.first?.resumeKey, "ct:208522161500021")
        XCTAssertEqual(page.episodes.first?.ctChannel?.id, "CH_1")
    }

    func testChannelIDAndAttribution() throws {
        let show = try parse("show_series", ArchiveParser.show)
        XCTAssertTrue(show.groups[0].episodes.allSatisfy { $0.ctChannel?.id == "CH_1" })
        // our recording: channelId null and "Nova HD" is no ČT channel → VOD rules never apply, it is a recording
        XCTAssertNil(show.groups[1].episodes[0].ctChannel)
        let home = try parse("home", ArchiveParser.home)
        XCTAssertTrue(home.attribution.hasPrefix("This application uses TMDB"))
    }

    func testMovieShow() throws {
        let show = try parse("show_movie", ArchiveParser.show)
        XCTAssertEqual(show.card.kind, .movie)
        XCTAssertEqual(show.countries, ["Česko", "Slovensko"])
        XCTAssertEqual(ArchiveRules.showFacts(show), "iVysílání · 2019 · film · Česko, Slovensko · 98 min")
        let episode = try XCTUnwrap(show.groups.first?.episodes.first)
        XCTAssertEqual(episode.resumeKey, "ct:22638149399")
        let (programme, channel) = ArchiveRules.programme(idec: "22638149399", title: show.card.title, episode: "",
                                                          aired: episode.aired, duration: episode.durationSeconds,
                                                          playable: episode.playable, channel: episode.ctChannel)
        XCTAssertEqual(channel?.id, "CH_2")
        XCTAssertEqual(DecisionRules.action(for: programme, now: utc("2026-10-25T10:00:00Z")), .vod)
    }
}
