import Foundation
import XCTest
@testable import CTCore

final class GuideTimelineTests: XCTestCase {
    private func programme(_ idec: String, _ start: String, _ end: String) -> Programme {
        Programme(channel: "ct1", idec: idec, title: idec, start: utc(start), end: utc(end), isPlayableNow: true)
    }

    func testAxisRoundsOutToTicks() {
        let programmes = [programme("a", "2026-10-17T04:59:00Z", "2026-10-17T06:00:00Z"),
                          programme("b", "2026-10-17T06:00:00Z", "2026-10-17T22:10:00Z")]
        let timeline = GuideTimeline(spans: programmes, fallbackDay: BroadcastDay(year: 2026, month: 10, day: 17),
                                     pointsPerSecond: 0.1)
        XCTAssertEqual(timeline.start, utc("2026-10-17T04:30:00Z"))
        XCTAssertEqual(timeline.end, utc("2026-10-17T22:30:00Z"))
        XCTAssertEqual(timeline.ticks.count, 36)
        XCTAssertEqual(timeline.width, 18 * 3600 * 0.1, accuracy: 0.001)
        XCTAssertEqual(timeline.tickIndex(for: utc("2026-10-17T05:45:00Z")), 2)
        XCTAssertEqual(timeline.x(for: utc("2026-10-17T05:30:00Z")), 360, accuracy: 0.001)
        XCTAssertEqual(timeline.x(for: utc("2026-10-18T05:30:00Z")), timeline.width, accuracy: 0.001)
    }

    func testEmptyFallsBackToWholeDay() {
        let day = BroadcastDay(year: 2026, month: 10, day: 17)
        let timeline = GuideTimeline(spans: [], fallbackDay: day, pointsPerSecond: 1)
        XCTAssertEqual(timeline.start, day.startDate())
        XCTAssertEqual(timeline.end, day.adding(days: 1).startDate())
    }

    func testSegmentsFillTheWholeAxis() {
        let timeline = GuideTimeline(start: utc("2026-10-17T10:00:00Z"), end: utc("2026-10-17T12:00:00Z"), pointsPerSecond: 0.01)
        XCTAssertEqual(timeline.segments(for: [Programme]()), [.gap(width: 72)])
        let segments = timeline.segments(for: [programme("a", "2026-10-17T10:00:00Z", "2026-10-17T11:00:00Z")])
        XCTAssertEqual(segments.count, 2)
        XCTAssertEqual(segments.last, .gap(width: 36))
    }

    func testSplitGapsAtTicks() {
        let timeline = GuideTimeline(start: utc("2026-10-17T10:00:00Z"), end: utc("2026-10-17T12:00:00Z"), pointsPerSecond: 0.01)
        let segments = timeline.segments(
            for: [programme("a", "2026-10-17T10:10:00Z", "2026-10-17T10:20:00Z")], splitGapsAtTicks: true
        )
        // 10:00-10:10 gap, a, then 10:20-10:30, 10:30-11:00, 11:00-11:30, 11:30-12:00.
        XCTAssertEqual(segments, [.gap(width: 6), .item(programme("a", "2026-10-17T10:10:00Z", "2026-10-17T10:20:00Z"), width: 6),
                                  .gap(width: 6), .gap(width: 18), .gap(width: 18), .gap(width: 18)])
    }

    func testSegmentsWithGapsAndOverlaps() {
        let timeline = GuideTimeline(start: utc("2026-10-17T10:00:00Z"), end: utc("2026-10-17T13:00:00Z"), pointsPerSecond: 0.01)
        let segments = timeline.segments(for: [
            programme("b", "2026-10-17T11:00:00Z", "2026-10-17T11:30:00Z"),
            programme("a", "2026-10-17T10:30:00Z", "2026-10-17T11:00:00Z"),
            programme("overlap", "2026-10-17T11:15:00Z", "2026-10-17T12:00:00Z"),
            programme("swallowed", "2026-10-17T11:35:00Z", "2026-10-17T11:50:00Z"),
            programme("clipped", "2026-10-17T12:30:00Z", "2026-10-17T14:00:00Z"),
        ])
        let widths: [String] = segments.map {
            switch $0 {
            case .gap(let width): return "gap \(Int(width.rounded()))"
            case .item(let p, let width): return "\(p.idec) \(Int(width.rounded()))"
            }
        }
        XCTAssertEqual(widths, ["gap 18", "a 18", "b 18", "overlap 18", "gap 18", "clipped 18"])
        let total = segments.reduce(0.0) { sum, segment in
            switch segment {
            case .gap(let width), .item(_, let width): return sum + width
            }
        }
        XCTAssertEqual(total, timeline.width, accuracy: 0.001)
    }
}
