import Foundation
import XCTest
@testable import CTCore

/// T32: the year/month chips of a long ČT episode list (archive_cases.json "periods", spec/dvb-gateway.md
/// "Archive" → "Year and month").
final class ArchivePeriodsTests: XCTestCase {
    private func golden() throws -> [String: Any] {
        try XCTUnwrap(Fixture.json("archive_cases")["periods"] as? [String: Any])
    }

    func testGoldenMonthNames() throws {
        let cases = try XCTUnwrap(golden()["monthName"] as? [[String: Any]])
        XCTAssertEqual(cases.count, 12)
        for item in cases {
            let month = item["month"] as! Int
            XCTAssertEqual(ArchivePeriodRules.monthName(month), item["name"] as? String, "month \(month)")
        }
        XCTAssertNil(ArchivePeriodRules.monthName(0))
        XCTAssertNil(ArchivePeriodRules.monthName(13))
    }

    func testGoldenChips() throws {
        let cases = try XCTUnwrap(golden()["chips"] as? [[String: Any]])
        XCTAssertEqual(cases.count, 9)
        for item in cases {
            let name = item["name"] as! String
            // the golden periods go through the parser, as the gateway's would
            let periods = ArchiveParser.periods(item["periods"])
            // no chips expected = the parser keeps no periods (also for periods that are there but make no sense)
            XCTAssertEqual(periods == nil, (item["years"] as! [Any]).isEmpty, name)
            let chips = ArchivePeriodRules.chips(periods)
            func expected(_ key: String) -> [[String: AnyHashable]] {
                (item[key] as! [[String: Any]]).map { ["label": $0["label"] as! String, "selected": $0["selected"] as! Bool] }
            }
            XCTAssertEqual(chips.years.map { ["label": $0.label, "selected": $0.selected] as [String: AnyHashable] },
                           expected("years"), name)
            XCTAssertEqual(chips.months.map { ["label": $0.label, "selected": $0.selected] as [String: AnyHashable] },
                           expected("months"), name)
        }
    }

    func testGoldenSelectYear() throws {
        let cases = try XCTUnwrap(golden()["selectYear"] as? [[String: Any]])
        XCTAssertEqual(cases.count, 3)
        for item in cases {
            XCTAssertEqual(ArchivePeriodRules.selectYear(months: item["months"] as! [Int]), item["month"] as? Int,
                           item["name"] as? String ?? "")
        }
        XCTAssertNil(ArchivePeriodRules.selectYear(months: []))
    }

    /// The chips carry the values to ask for, not only the labels.
    func testChipValues() {
        let chips = ArchivePeriodRules.chips(ArchivePeriods(years: [2026, 2025], year: 2025, months: [3, 1], month: 1))
        XCTAssertEqual(chips.years.map(\.value), [2026, 2025])
        XCTAssertEqual(chips.months.map(\.value), [3, 1])
        XCTAssertEqual(chips.months.first { $0.selected }?.label, "leden")
    }

    // MARK: parser

    private let good: [String: Any] = ["years": [2026, 2025], "year": 2026, "months": [11, 10], "month": 11]

    func testParsesTheGatewaysShape() {
        XCTAssertEqual(ArchiveParser.periods(good),
                       ArchivePeriods(years: [2026, 2025], year: 2026, months: [11, 10], month: 11))
    }

    /// Defensive: an undocumented value is no chips, never a guessed selection.
    func testBadPeriodsAreNoChips() {
        func with(_ change: [String: Any]) -> ArchivePeriods? {
            ArchiveParser.periods(good.merging(change) { $1 })
        }
        XCTAssertNotNil(with([:]))
        XCTAssertNil(ArchiveParser.periods(nil))
        XCTAssertNil(ArchiveParser.periods(NSNull()))
        XCTAssertNil(ArchiveParser.periods([2026]))
        XCTAssertNil(with(["year": 2024]), "the chosen year is not listed")
        // a month not named or not listed: the year's newest (selectYear), not "no chips"
        XCTAssertEqual(with(["month": 9])?.month, 11, "the chosen month is not listed")
        XCTAssertEqual(ArchiveParser.periods(["years": [2025], "year": 2025, "months": [3, 12, 6]])?.month, 12,
                       "no month named")
        XCTAssertEqual(with(["month": NSNull()])?.month, 11)
        XCTAssertNil(with(["years": []]))
        XCTAssertNil(with(["months": [13, "3"]]), "months none of which is a month")
        XCTAssertNil(with(["months": "10"]), "months not a list")
        XCTAssertNil(with(["year": "2026"]), "a string is no number")
        XCTAssertEqual(with(["month": true])?.month, 11, "a bool is no number")
        // out-of-range, repeated and fractional values are dropped, the rest kept in the gateway's order
        XCTAssertEqual(with(["years": [2026, 2026, 1850, 2025.5, "2024", 2025], "months": [13, 11, 0, 10, 11]]),
                       ArchivePeriods(years: [2026, 2025], year: 2026, months: [11, 10], month: 11))
    }

    /// T51: a year of at most 100 episodes is shown whole: year chips, no month (null or left out).
    func testYearsOnly() {
        let whole = ArchivePeriods(years: [2026, 2025], year: 2025, months: [], month: nil)
        XCTAssertEqual(ArchiveParser.periods(["years": [2026, 2025], "year": 2025, "months": [], "month": NSNull()]), whole)
        XCTAssertEqual(ArchiveParser.periods(["years": [2026, 2025], "year": 2025, "months": []]), whole)
        XCTAssertNil(ArchiveParser.periods(["years": [2026, 2025], "year": 2024, "months": [], "month": NSNull()]),
                     "the chosen year is still checked")
        // lead's decision: a month named for no months, or no months list at all, is still a year shown whole
        XCTAssertEqual(ArchiveParser.periods(["years": [2026, 2025], "year": 2025, "months": [], "month": 3]), whole,
                       "a month named for a year without months is dropped")
        XCTAssertEqual(ArchiveParser.periods(["years": [2026, 2025], "year": 2025, "month": 3]), whole, "no months key")
        XCTAssertEqual(ArchiveParser.periods(["years": [2026, 2025], "year": 2025, "months": NSNull(), "month": 3]), whole,
                       "months null")
        // the gateway's form (T51 follow-up): wholeYear true with one month for pre-T51 builds = the year shown whole
        let gateway: [String: Any] = ["years": [2026, 2025], "year": 2025, "months": [10], "month": 10, "wholeYear": true]
        XCTAssertEqual(ArchiveParser.periods(gateway), whole, "wholeYear drops the compatibility month")
        XCTAssertEqual(ArchiveParser.periods(gateway.merging(["months": "10"]) { $1 }), whole,
                       "wholeYear decides, whatever the months")
        XCTAssertEqual(ArchiveParser.periods(gateway.merging(["wholeYear": false]) { $1 }),
                       ArchivePeriods(years: [2026, 2025], year: 2025, months: [10], month: 10), "wholeYear false = months")
        XCTAssertEqual(ArchiveParser.periods(gateway.merging(["wholeYear": "true"]) { $1 })?.month, 10,
                       "only a JSON true is a year shown whole")
        XCTAssertNil(ArchiveParser.periods(gateway.merging(["year": 2024]) { $1 }), "the chosen year is still checked")
        let chips = ArchivePeriodRules.chips(whole)
        XCTAssertEqual(chips.years.filter(\.selected).map(\.value), [2025])
        XCTAssertTrue(chips.months.isEmpty)
        // the years-only and the month variant differ
        XCTAssertNotEqual(ArchiveParser.periods(good), ArchiveParser.periods(good.merging(["months": [], "month": NSNull()]) { $1 }))
    }

    func testShowAndEpisodePageCarryThePeriods() throws {
        let episode: [String: Any] = ["source": "ct", "idec": "31", "title": "Události", "durationSeconds": 60]
        let card: [String: Any] = ["id": "ct:1", "source": "ct", "title": "Události", "kind": "series"]
        let body = try JSONSerialization.data(withJSONObject: ["card": card, "seasons": [], "groups": [
            ["title": "Díly", "season": NSNull(), "episodes": [episode], "nextPage": 2, "periods": good],
            ["title": "Nahráno z antény", "episodes": [["source": "rec", "recordingId": "r1", "title": "x"]]],
        ]])
        let show = try ArchiveParser.show(status: 200, body: body)
        XCTAssertEqual(show.groups[0].periods?.month, 11)
        XCTAssertNil(show.groups[1].periods)
        let page = try ArchiveParser.episodePage(status: 200, body: body)
        XCTAssertEqual(page.periods?.years, [2026, 2025])
        XCTAssertEqual(page.nextPage, 2)
        let flat = try ArchiveParser.episodePage(status: 200, body: JSONSerialization.data(withJSONObject: [
            "episodes": [episode], "periods": good,
        ]))
        XCTAssertEqual(flat.periods?.year, 2026)
        let none = try ArchiveParser.episodePage(status: 200, body: JSONSerialization.data(withJSONObject: [
            "episodes": [episode],
        ]))
        XCTAssertNil(none.periods)
    }

    /// The gateway's shared sample (spec/fixtures/dvb/dvb_archive_show_periods.json, written with the gateway).
    func testGatewaySample() throws {
        let url = Fixture.directory.appendingPathComponent("dvb/dvb_archive_show_periods.json")
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw XCTSkip("dvb_archive_show_periods.json is not there yet")
        }
        let response = try Fixture.response("dvb/dvb_archive_show_periods")
        let show = try ArchiveParser.show(status: response.status, body: response.body)
        let group = try XCTUnwrap(show.groups.first { !$0.isRecordings })
        let periods = try XCTUnwrap(group.periods, "the ČT group has periods")
        XCTAssertEqual(periods.years, periods.years.sorted(by: >), "years newest first")
        XCTAssertEqual(periods.months, periods.months.sorted(by: >), "months newest first")
        XCTAssertFalse(group.episodes.isEmpty)
        let calendar = Calendar(identifier: .gregorian)
        var prague = calendar
        prague.timeZone = TimeZone(identifier: "Europe/Prague")!
        for episode in group.episodes {
            let aired = try XCTUnwrap(episode.aired)
            XCTAssertEqual(prague.component(.year, from: aired), periods.year)
            XCTAssertEqual(prague.component(.month, from: aired), periods.month)
        }
        let aired = group.episodes.compactMap(\.aired)
        XCTAssertEqual(aired, aired.sorted(by: >), "the month's episodes newest first")
        let chips = ArchivePeriodRules.chips(periods)
        XCTAssertEqual(chips.years.filter(\.selected).count, 1)
        XCTAssertEqual(chips.months.filter(\.selected).count, 1)
    }

    // MARK: query

    func testQueryFollowsTheSelection() {
        let start = ArchiveEpisodeQuery(season: "s1")
        let periods = ArchivePeriods(years: [2026, 2025], year: 2026, months: [11, 10], month: 11)
        let year = start.selectingYear(2025)
        XCTAssertEqual(year, ArchiveEpisodeQuery(season: "s1", year: 2025), "a year alone: the gateway picks its month")
        XCTAssertEqual(start.selectingMonth(10, of: periods), ArchiveEpisodeQuery(season: "s1", year: 2026, month: 10))
        XCTAssertEqual(ArchiveEpisodeQuery(season: "s1", year: 2025, month: 3).selectingSeason("s2"),
                       ArchiveEpisodeQuery(season: "s2"), "another season starts at its newest month")
        XCTAssertEqual(year.paging(ArchivePeriods(years: [2026, 2025], year: 2025, months: [12], month: 12)),
                       ArchiveEpisodeQuery(season: "s1", year: 2025, month: 12), "later pages name the month shown")
        XCTAssertEqual(start.paging(nil), start)
        // T51: the next page of a year shown whole names the year only
        let whole = ArchivePeriods(years: [2026, 2025], year: 2025, months: [], month: nil)
        XCTAssertEqual(ArchiveEpisodeQuery(season: "s1", year: 2026, month: 10).selectingYear(2025).paging(whole),
                       ArchiveEpisodeQuery(season: "s1", year: 2025))
        XCTAssertEqual(ArchiveEpisodeQuery(season: "s1", year: 2025).paging(whole).queryItems(page: 2).map(\.name),
                       ["season", "year", "page"])
    }

    func testRequests() async throws {
        let transport = FakeTransport()
        transport.on("/dvb/archive/show/", HTTPResponse(status: 200, body: Data(#"{"episodes": []}"#.utf8)))
        let client = DVBClient(config: GatewayConfig(baseURL: URL(string: "http://gw.local:8098")!, key: "k"),
                               transport: transport)
        _ = try await client.archiveEpisodes(show: "ct:1", query: ArchiveEpisodeQuery(season: "s 2", year: 2025, month: 3), page: 2)
        _ = try await client.archiveEpisodes(show: "ct:1", query: ArchiveEpisodeQuery(year: 2024), page: 1)
        _ = try await client.archiveEpisodes(show: "ct:1", query: ArchiveEpisodeQuery(month: 3), page: 1)
        _ = try await client.archiveEpisodes(show: "ct:1", query: ArchiveEpisodeQuery(), page: 4)
        XCTAssertEqual(transport.requests.map { $0.url!.absoluteString }, [
            "http://gw.local:8098/dvb/archive/show/ct:1?season=s%202&year=2025&month=3&page=2",
            "http://gw.local:8098/dvb/archive/show/ct:1?year=2024&page=1",
            "http://gw.local:8098/dvb/archive/show/ct:1?page=1",
            "http://gw.local:8098/dvb/archive/show/ct:1?page=4",
        ])
    }
}
