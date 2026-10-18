import Foundation
import XCTest
@testable import CTCore

final class DecisionTests: XCTestCase {
    func testConstantsMatchGoldenFile() throws {
        let golden = try Fixture.json("decisions")
        XCTAssertEqual(golden["timeshiftWindowSeconds"] as? Double, DecisionRules.timeshiftWindow)
        XCTAssertEqual(golden["startMarginSeconds"] as? Double, DecisionRules.startMargin)
    }

    func testGoldenCases() throws {
        let golden = try Fixture.json("decisions")
        let now = utc(golden["now"] as! String)
        let cases = golden["programmes"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 9)
        for item in cases {
            let name = item["name"] as! String
            let expect = item["expect"] as! [String: Any]
            let programme = Programme(
                channel: "ct1", idec: name, title: name,
                start: utc(item["start"] as! String), end: utc(item["end"] as! String),
                isPlayableNow: item["isPlayableNow"] as! Bool
            )
            let action = DecisionRules.action(for: programme, now: now)
            switch expect["action"] as! String {
            case "future": XCTAssertEqual(action, .future, name)
            case "vod": XCTAssertEqual(action, .vod, name)
            case "vod_unavailable": XCTAssertEqual(action, .vodUnavailable, name)
            case "startover":
                XCTAssertEqual(action, .startOver(offset: expect["offsetSeconds"] as! Double), name)
            case let other: XCTFail("unknown action \(other) in \(name)")
            }
        }
    }

    func testBoundaries() {
        let start = utc("2026-10-17T12:00:00Z")
        let programme = Programme(channel: "ct1", idec: "1", title: "", start: start,
                                  end: start.addingTimeInterval(3600), isPlayableNow: false)
        XCTAssertEqual(DecisionRules.action(for: programme, now: start.addingTimeInterval(-1)), .future)
        // start ≤ now: airing, buffer offset = 3 h − 30 s.
        XCTAssertEqual(DecisionRules.action(for: programme, now: start), .startOver(offset: 10770))
        // now == end: ended; still in buffer (now − start = 1 h < 3 h).
        XCTAssertEqual(DecisionRules.action(for: programme, now: start.addingTimeInterval(3600)), .startOver(offset: 7170))
        // now − start == W: out of the buffer.
        XCTAssertEqual(DecisionRules.action(for: programme, now: start.addingTimeInterval(3 * 3600)), .vodUnavailable)
    }

    func testRecentProgrammeUsesBufferEvenWhenArchived() {
        let start = utc("2026-10-17T12:00:00Z")
        let archived = Programme(channel: "ct1", idec: "1", title: "", start: start,
                                 end: start.addingTimeInterval(3600), isPlayableNow: true)
        XCTAssertEqual(DecisionRules.action(for: archived, now: start.addingTimeInterval(3 * 3600 - 1)), .startOver(offset: 0))
        XCTAssertEqual(DecisionRules.action(for: archived, now: start.addingTimeInterval(3 * 3600)), .vod)
    }

    func testStartOverDate() {
        XCTAssertEqual(DecisionRules.startOverDate(start: utc("2026-10-17T17:30:00Z")), utc("2026-10-17T17:29:30Z"))
    }
}
