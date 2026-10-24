import Foundation
import XCTest
@testable import CTCore

final class ProgrammePageTests: XCTestCase {
    // MARK: golden cases (programme_page_cases.json)

    func testGoldenButtons() throws {
        let cases = try Fixture.json("programme_page_cases")["buttons"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 11)
        for item in cases {
            let name = item["name"] as! String
            let buttons = ProgrammePage.buttons(
                source: try XCTUnwrap(ProgrammePage.Source(rawValue: item["source"] as! String), name),
                phase: try XCTUnwrap(ProgrammePage.Phase(rawValue: item["state"] as! String), name),
                startable: item["startable"] as? Bool ?? false,
                playable: item["playable"] as? Bool ?? false,
                resume: item["resume"] as! Bool
            )
            XCTAssertEqual(buttons.map(\.rawValue), item["buttons"] as! [String], name)
        }
    }

    func testGoldenRatingLines() throws {
        let cases = try Fixture.json("programme_page_cases")["rating"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 6)
        for item in cases {
            let value = (item["value"] as! NSNumber).doubleValue
            let votes = (item["votes"] as! NSNumber).intValue
            XCTAssertEqual(ProgrammePage.ratingLine(value: value, votes: votes), item["line"] as? String, "\(value)/\(votes)")
        }
    }

    func testGoldenPlural() throws {
        let cases = try Fixture.json("programme_page_cases")["plural"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 6)
        for item in cases {
            let n = (item["n"] as! NSNumber).intValue
            XCTAssertEqual(ProgrammePage.votesWord(n), item["word"] as? String, "\(n)")
        }
    }

    // MARK: rules beyond the golden file

    func testRatingRoundsHalfUpOnTheDecimalValue() {
        XCTAssertEqual(ProgrammePage.oneDecimal(6.25), "6,3")
        XCTAssertEqual(ProgrammePage.oneDecimal(6.35), "6,4", "6.35 is 6.3499… as a double")
        XCTAssertEqual(ProgrammePage.oneDecimal(6.24), "6,2")
        XCTAssertEqual(ProgrammePage.oneDecimal(10), "10,0")
        XCTAssertEqual(ProgrammePage.grouped(999), "999")
        XCTAssertEqual(ProgrammePage.grouped(1000), "1 000")
        XCTAssertEqual(ProgrammePage.ratingLine(value: 7, votes: 20), "Hodnocení TMDB 7,0 (20 hlasů)")
    }

    func testOriginalTitleOnlyWhenItDiffers() {
        XCTAssertEqual(ProgrammePage.originalTitleLine(original: "The Shawshank Redemption", title: "Vykoupení z věznice Shawshank",
                                                       guideTitle: "Vykoupení z věznice Shawshank"),
                       "Původní název: The Shawshank Redemption")
        XCTAssertNil(ProgrammePage.originalTitleLine(original: "Pelíšky", title: "Pelíšky", guideTitle: "Pelíšky (12)"))
        XCTAssertNil(ProgrammePage.originalTitleLine(original: "Pelisky", title: "Cosy Dens", guideTitle: "Pelíšky"),
                     "diacritics and case do not count")
        XCTAssertNil(ProgrammePage.originalTitleLine(original: "  ", title: "x", guideTitle: "y"))
    }

    func testMinutes() {
        let start = utc("2026-10-24T18:15:00Z")
        XCTAssertEqual(ProgrammePage.minutesText(start: start, end: start.addingTimeInterval(110 * 60)), "110 min")
        XCTAssertNil(ProgrammePage.minutesText(start: start, end: start))
    }

    // MARK: details

    private let answer = #"""
    {"match": {"kind": "movie", "title": "Pelíšky", "originalTitle": "Pelíšky", "year": 1999,
               "overview": "Komedie.", "genres": ["Komedie", "Drama"], "runtimeMinutes": 115,
               "rating": {"value": 8.1, "votes": 1234}, "poster": "/dvb/details/image/w342/abc_D-1.jpg", "tmdbId": 12},
     "attribution": "This application uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB."}
    """#

    func testParsesAMatch() throws {
        let details = try XCTUnwrap(try ProgrammeDetailsParser.parse(status: 200, body: Data(answer.utf8)))
        let match = try XCTUnwrap(details.match)
        XCTAssertEqual(match.title, "Pelíšky")
        XCTAssertEqual(match.year, 1999)
        XCTAssertEqual(match.genres, ["Komedie", "Drama"])
        XCTAssertEqual(match.runtimeMinutes, 115)
        XCTAssertEqual(match.rating, ProgrammeDetails.Rating(value: 8.1, votes: 1234))
        XCTAssertEqual(match.posterPath, "/dvb/details/image/w342/abc_D-1.jpg")
        XCTAssertEqual(details.attribution, ProgrammeDetails.defaultAttribution)
    }

    func testSharedFixtures() throws {
        let matchFixture = try Fixture.response("dvb/dvb_details_match")
        let details = try XCTUnwrap(try ProgrammeDetailsParser.parse(status: matchFixture.status, body: matchFixture.body))
        let match = try XCTUnwrap(details.match)
        XCTAssertEqual(match.title, "Hněv Titánů")
        XCTAssertEqual(ProgrammePage.originalTitleLine(original: match.originalTitle, title: match.title, guideTitle: "Hněv Titánů"),
                       "Původní název: Wrath of the Titans")
        XCTAssertEqual(match.year, 2012)
        XCTAssertEqual(match.genres, ["Akční"])
        XCTAssertEqual(match.rating.flatMap { ProgrammePage.ratingLine(value: $0.value, votes: $0.votes) },
                       "Hodnocení TMDB 5,8 (7 311 hlasů)")
        XCTAssertEqual(match.posterPath, "/dvb/details/image/w342/titanyPoster01.jpg")
        XCTAssertEqual(details.attribution, ProgrammeDetails.defaultAttribution)

        let none = try Fixture.response("dvb/dvb_details_nomatch")
        let empty = try XCTUnwrap(try ProgrammeDetailsParser.parse(status: none.status, body: none.body))
        XCTAssertNil(empty.match)
    }

    func testNoMatchNotFoundAndErrors() throws {
        let none = try ProgrammeDetailsParser.parse(status: 200, body: Data(#"{"match": null, "error": "TMDb neodpovídá"}"#.utf8))
        XCTAssertEqual(none?.match, nil)
        XCTAssertNotNil(none, "200 without a match is still an answer")
        XCTAssertNil(try ProgrammeDetailsParser.parse(status: 404, body: Data(#"{"error": "Podrobnosti nejsou zapnuté"}"#.utf8)))
        XCTAssertThrowsError(try ProgrammeDetailsParser.parse(status: 401, body: Data(#"{"error": "Chybí klíč"}"#.utf8))) {
            XCTAssertEqual(($0 as? CTError)?.userMessage, "Chybí klíč")
        }
        XCTAssertThrowsError(try ProgrammeDetailsParser.parse(status: 200, body: Data("[]".utf8)))
    }

    func testDefensiveFields() throws {
        let body = #"""
        {"match": {"title": "X", "year": "1999", "rating": {"value": true, "votes": 30}, "genres": [1, "Drama", ""],
                   "poster": "https://image.tmdb.org/t/p/w342/a.jpg"}}
        """#
        let match = try XCTUnwrap(try ProgrammeDetailsParser.parse(status: 200, body: Data(body.utf8))?.match)
        XCTAssertNil(match.year)
        XCTAssertNil(match.rating)
        XCTAssertEqual(match.genres, ["Drama"])
        XCTAssertNil(match.posterPath, "only a gateway path, never a URL")
        let noTitle = try ProgrammeDetailsParser.parse(status: 200, body: Data(#"{"match": {"year": 2000}}"#.utf8))
        XCTAssertNil(noTitle?.match)
    }

    func testPosterPathsAreGatewayImagesOnly() {
        XCTAssertTrue(ProgrammeDetailsParser.isGatewayImagePath("/dvb/details/image/w342/abc.png"))
        for bad in ["/dvb/details/image/w342/../../admin", "/dvb/details/image//x.jpg", "/dvb/epg?x=1",
                    "http://evil/dvb/details/image/a.jpg", "/dvb/details/image/w342/a b.jpg"] {
            XCTAssertFalse(ProgrammeDetailsParser.isGatewayImagePath(bad), bad)
        }
    }

    func testClientQueriesByIDWithTheKey() async throws {
        let transport = FakeTransport()
        transport.on("/dvb/details?source=dvb", HTTPResponse(status: 200, body: Data(answer.utf8)))
        transport.on("/dvb/details?source=ct", HTTPResponse(status: 404, body: Data("{}".utf8)))
        transport.on("/dvb/details/image/", HTTPResponse(status: 200, body: Data([0xFF, 0xD8, 0xFF])))
        let client = DVBClient(config: GatewayConfig(address: "pi", key: "k")!, transport: transport)

        let dvb = try await client.details(.dvb(eventID: "dvr:a+b c"))
        XCTAssertEqual(dvb?.match?.title, "Pelíšky")
        let ct = try await client.details(.ct(channel: "ct1", day: BroadcastDay(year: 2026, month: 10, day: 4), idec: "226411010101016"))
        XCTAssertNil(ct)
        let poster = try await client.detailsImage(path: "/dvb/details/image/w342/abc.jpg")
        XCTAssertEqual(poster, Data([0xFF, 0xD8, 0xFF]))
        let refused = try await client.detailsImage(path: "/dvb/recording/x/thumb.jpg")
        XCTAssertNil(refused)

        let urls = transport.requests.map { $0.url!.absoluteString }
        XCTAssertEqual(urls, [
            "http://pi:8098/dvb/details?source=dvb&id=dvr:a%2Bb%20c",
            "http://pi:8098/dvb/details?source=ct&channel=ct1&date=2026-10-04&idec=226411010101016",
            "http://pi:8098/dvb/details/image/w342/abc.jpg",
        ], "a refused path is never requested")
        XCTAssertTrue(transport.requests.allSatisfy { $0.value(forHTTPHeaderField: "Authorization") == "Bearer k" })
    }
}
