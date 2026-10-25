import Foundation
import XCTest
@testable import CTCore

/// Golden cases shared with the other apps: spec/fixtures/clock_cases.json.
final class ClockTests: XCTestCase {
    func testFormat() throws {
        let cases = try XCTUnwrap(try Fixture.json("clock_cases")["format"] as? [[String: String]])
        XCTAssertFalse(cases.isEmpty)
        for item in cases {
            XCTAssertEqual(Clock.text(utc(item["utc"]!)), item["text"]!, item["name"]!)
        }
    }

    func testIgnoresTheDeviceTimeZone() {
        var tokyo = Calendar.prague
        tokyo.timeZone = TimeZone(identifier: "Asia/Tokyo")!
        XCTAssertEqual(Clock.text(utc("2026-10-25T07:05:00Z")), "8:05")
        XCTAssertEqual(Clock.text(utc("2026-10-25T07:05:00Z"), calendar: tokyo), "16:05")
    }

    func testNextMinuteIsTheBoundary() {
        XCTAssertEqual(Clock.nextMinute(after: utc("2026-10-25T13:30:59Z")), utc("2026-10-25T13:31:00Z"))
        XCTAssertEqual(Clock.nextMinute(after: utc("2026-10-25T13:30:00Z")), utc("2026-10-25T13:31:00Z"))
        XCTAssertEqual(Clock.nextMinute(after: utc("2026-10-25T13:30:00Z").addingTimeInterval(0.4)),
                       utc("2026-10-25T13:31:00Z"))
    }
}
