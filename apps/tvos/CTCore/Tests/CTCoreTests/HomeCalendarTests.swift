import Foundation
import XCTest
@testable import CTCore

/// Golden cases shared with the other apps: spec/fixtures/calendar_cases.json and namedays_cz.json (spec/apps.md
/// "Domů", T58).
final class HomeCalendarTests: XCTestCase {
    private func day(_ text: String) -> BroadcastDay {
        let parts = text.split(separator: "-").map { Int($0)! }
        return BroadcastDay(year: parts[0], month: parts[1], day: parts[2])
    }

    func testCases() throws {
        let cases = try XCTUnwrap(try Fixture.json("calendar_cases")["cases"] as? [[String: Any]])
        XCTAssertGreaterThan(cases.count, 20)
        for item in cases {
            let date = item["date"] as! String
            let header = HomeCalendar.header(for: day(date))
            XCTAssertEqual(header.fullDate, item["fullDate"] as? String, date)
            XCTAssertEqual(header.nameLine, item["nameLine"] as? String, date)
            XCTAssertEqual(header.lines, item["lines"] as? [String], date)
        }
    }

    func testNameDaysEqualTheFixture() throws {
        let days = try XCTUnwrap(try Fixture.json("namedays_cz")["days"] as? [String: Any])
        XCTAssertEqual(days.count, 366, "29 February included")
        for (key, value) in days {
            XCTAssertEqual(NameDays.table[key], value as? [String], key)
        }
        XCTAssertEqual(NameDays.table.count, days.values.filter { $0 is [String] }.count, "no extra days")
    }

    func testFixedHolidaysAndWordsEqualTheFixture() throws {
        let fixture = try Fixture.json("calendar_cases")
        XCTAssertEqual(HomeCalendar.fixedHolidays, fixture["stateHolidays"] as? [String: String])
        let easter = try XCTUnwrap(fixture["easterHolidays"] as? [String: String])
        XCTAssertEqual(Dictionary(uniqueKeysWithValues: HomeCalendar.easterHolidays.map { (String($0.key), $0.value) }),
                       easter)
        XCTAssertEqual(HomeCalendar.weekdays, fixture["weekdays"] as? [String])
        XCTAssertEqual(HomeCalendar.monthsGenitive, fixture["monthsGenitive"] as? [String])
    }

    func testSchoolHolidaysEqualTheFixture() throws {
        let table = try XCTUnwrap(try Fixture.json("calendar_cases")["schoolHolidays"] as? [[String: String]])
        XCTAssertEqual(HomeCalendar.schoolHolidays.map(\.name), table.map { $0["name"]! })
        XCTAssertEqual(HomeCalendar.schoolHolidays.map(\.from.description), table.map { $0["from"]! })
        XCTAssertEqual(HomeCalendar.schoolHolidays.map(\.to.description), table.map { $0["to"]! })
    }

    func testEaster() throws {
        let easter = try XCTUnwrap(try Fixture.json("calendar_cases")["easter"] as? [String: String])
        XCTAssertFalse(easter.isEmpty)
        for (year, date) in easter {
            XCTAssertEqual(HomeCalendar.easter(year: Int(year)!).description, date, year)
        }
    }

    func testClockChanges() throws {
        let changes = try XCTUnwrap(try Fixture.json("calendar_cases")["clockChange"] as? [String: [String]])
        XCTAssertFalse(changes.isEmpty)
        for (year, dates) in changes {
            let computed = HomeCalendar.clockChanges(year: Int(year)!)
            XCTAssertEqual([computed.forward.description, computed.back.description], dates, year)
        }
    }

    /// Every day of the year: a new or full moon exactly on the listed days (`moon2026`, `moon2027`), nothing on the
    /// others. 2027 has phases close to Prague midnight (21 February, 21 April).
    func testMoonYears() throws {
        for year in [2026, 2027] {
            let list = try XCTUnwrap(try Fixture.json("calendar_cases")["moon\(year)"] as? [[String: String]])
            XCTAssertGreaterThanOrEqual(list.count, 24, "\(year)")
            let expected = Dictionary(uniqueKeysWithValues: list.map { ($0["date"]!, $0["phase"]!) })
            var date = BroadcastDay(year: year, month: 1, day: 1)
            while date.year == year {
                let phase: String? = switch HomeCalendar.moonPhase(on: date) {
                case .new: "new"
                case .full: "full"
                case nil: nil
                }
                XCTAssertEqual(phase, expected[date.description], date.description)
                date = date.plus(1)
            }
        }
    }

    /// One computed instant to the second, so every app's formula is the same (E with T², TT taken as UT).
    func testMoonInstant() throws {
        let golden = try XCTUnwrap(try Fixture.json("calendar_cases")["moonInstant"] as? [String: Any])
        let k = try XCTUnwrap(golden["k"] as? Double)
        XCTAssertEqual(k, 320.5)
        let instant = HomeCalendar.moonInstant(k: k)
        XCTAssertEqual(instant.timeIntervalSince1970, utc(golden["utc"] as! String).timeIntervalSince1970, accuracy: 1)
    }

    func testDayArithmeticMatchesTheCalendar() {
        var date = BroadcastDay(year: 2023, month: 12, day: 25)
        for _ in 0..<1200 {
            XCTAssertEqual(date.plus(1), date.adding(days: 1))
            XCTAssertEqual(date.plus(-1), date.adding(days: -1))
            date = date.plus(1)
        }
        XCTAssertEqual(BroadcastDay(year: 2026, month: 11, day: 4).weekdayFromMonday, 2, "a Wednesday")
    }
}
