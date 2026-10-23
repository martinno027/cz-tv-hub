import Foundation
import XCTest
@testable import CTCore

/// Golden cases shared with the other apps: spec/fixtures/guide_days_cases.json.
final class GuideDaysTests: XCTestCase {
    private func cases(_ group: String) throws -> [[String: String]] {
        let cases = try XCTUnwrap(try Fixture.json("guide_days_cases")[group] as? [[String: String]])
        XCTAssertFalse(cases.isEmpty)
        return cases
    }

    private func day(_ text: String) -> BroadcastDay {
        let parts = text.split(separator: "-").compactMap { Int($0) }
        return BroadcastDay(year: parts[0], month: parts[1], day: parts[2])
    }

    func testOpenDay() throws {
        for item in try cases("openDay") {
            XCTAssertEqual(GuideDays.openDay(now: utc(item["now"]!)), day(item["day"]!), item["name"]!)
        }
    }

    func testChipDay() throws {
        for item in try cases("chipDay") {
            XCTAssertEqual(GuideDays.chipDay(utc(item["time"]!)), day(item["day"]!), item["name"]!)
        }
    }

    func testSelect() throws {
        for item in try cases("select") {
            let result = GuideDays.select(chip: day(item["chip"]!), now: utc(item["now"]!))
            XCTAssertEqual(result.day, day(item["day"]!), item["name"]!)
            let anchor = item["anchor"]!
            XCTAssertEqual(result.anchor, anchor == "now" ? .now : .time(utc(anchor)), item["name"]!)
        }
    }

    func testBoundsAcrossDSTEnd() {
        // 25 Oct 2026 has 25 hours; the grid still runs from local 05:00 to local 05:00.
        let bounds = GuideDays.bounds(of: BroadcastDay(year: 2026, month: 10, day: 24))
        XCTAssertEqual(bounds.start, utc("2026-10-24T03:00:00Z"))
        XCTAssertEqual(bounds.end, utc("2026-10-25T04:00:00Z"))
    }
}
