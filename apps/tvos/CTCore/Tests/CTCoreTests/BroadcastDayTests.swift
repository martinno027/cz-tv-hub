import Foundation
import XCTest
@testable import CTCore

final class BroadcastDayTests: XCTestCase {
    let today = BroadcastDay(year: 2026, month: 10, day: 17)  // Saturday

    func testLabels() {
        XCTAssertEqual(today.label(today: today), "Dnes")
        XCTAssertEqual(today.adding(days: -1).label(today: today), "Včera")
        XCTAssertEqual(today.adding(days: 1).label(today: today), "Zítra")
        XCTAssertEqual(today.adding(days: -2).label(today: today), "čt 15. 10.")
        XCTAssertEqual(BroadcastDay(year: 2026, month: 10, day: 12).label(today: today), "po 12. 10.")
        XCTAssertEqual(BroadcastDay(year: 2026, month: 10, day: 19).label(today: today), "po 19. 10.")
        XCTAssertEqual(BroadcastDay(year: 2026, month: 10, day: 11).label(today: today), "ne 11. 10.")
    }

    func testDayFollowsPragueTime() {
        // 22:30 UTC on the 16th is 00:30 on the 17th in Prague (CEST).
        XCTAssertEqual(BroadcastDay(containing: utc("2026-10-16T22:30:00Z")), today)
        XCTAssertEqual(BroadcastDay(containing: utc("2026-10-16T21:59:00Z")), today.adding(days: -1))
        XCTAssertEqual(today.startDate(), utc("2026-10-16T22:00:00Z"))
    }

    func testAddingAcrossMonthAndDST() {
        XCTAssertEqual(today.adding(days: 15), BroadcastDay(year: 2026, month: 11, day: 1))
        // DST ends on 25 Oct 2026; adding days must stay on calendar days.
        XCTAssertEqual(BroadcastDay(year: 2026, month: 10, day: 24).adding(days: 2), BroadcastDay(year: 2026, month: 10, day: 26))
    }

    func testGraphQLDate() {
        XCTAssertEqual(today.graphQLDate, "10.17.2026")
        XCTAssertEqual(BroadcastDay(year: 2026, month: 3, day: 5).graphQLDate, "03.05.2026")
    }

    func testGuideRange() {
        let days = GuideRange.days(today: today)
        XCTAssertEqual(days.count, 10)
        XCTAssertEqual(days.first, BroadcastDay(year: 2026, month: 10, day: 10))
        XCTAssertEqual(days.last, BroadcastDay(year: 2026, month: 10, day: 19))
        XCTAssertEqual(days, days.sorted())
    }
}
