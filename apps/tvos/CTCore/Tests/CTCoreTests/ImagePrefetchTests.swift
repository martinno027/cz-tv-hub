import Foundation
import XCTest
@testable import CTCore

final class ImagePrefetchTests: XCTestCase {
    private struct Item: TimedItem, Equatable {
        let name: String
        let start: Date
        let end: Date
    }

    private let noon = utc("2026-10-23T10:00:00Z")

    /// One row of hour-long programmes from 05:00 to 05:00 (UTC hours relative to noon).
    private func row(_ index: Int) -> [Item] {
        (-7..<17).map { hour in
            Item(name: "r\(index)h\(hour)", start: noon.addingTimeInterval(Double(hour) * 3600),
                 end: noon.addingTimeInterval(Double(hour + 1) * 3600))
        }
    }

    func testWindowRowsAndTime() {
        let rows = (0..<20).map(row)
        let window = ImagePrefetch.window(rows: rows, around: .init(row: 5, time: noon.addingTimeInterval(1800)))
        XCTAssertEqual(Set(window.map(\.row)), Set(2...13), "3 rows above, 8 below")
        // ±3 h around 12:30 overlaps the programmes starting at 9:00 ... 15:00
        XCTAssertEqual(Set(window.filter { $0.row == 5 }.map(\.item.name)), Set((-3...3).map { "r5h\($0)" }))
        XCTAssertEqual(window.count, 12 * 7)
    }

    func testWindowOrderNearestFirst() {
        let rows = (0..<20).map(row)
        let window = ImagePrefetch.window(rows: rows, around: .init(row: 5, time: noon.addingTimeInterval(1800)))
        let names = window.map(\.item.name)
        XCTAssertEqual(names.first, "r5h0", "the focused programme first")
        // both neighbours are 30 min away: the earlier one first
        XCTAssertEqual(Array(names[1...4]), ["r5h-1", "r5h1", "r5h-2", "r5h2"])
        // the whole focused row comes before the next rows, and the row below before the row above
        XCTAssertTrue(names[0..<7].allSatisfy { $0.hasPrefix("r5h") })
        XCTAssertEqual(names[7], "r6h0")
        XCTAssertEqual(names[8], "r4h0")
        XCTAssertTrue(names.suffix(35).allSatisfy { $0.hasPrefix("r9h") || $0.hasPrefix("r10h") || $0.hasPrefix("r11h")
                || $0.hasPrefix("r12h") || $0.hasPrefix("r13h") })
    }

    func testWindowAtTheEdges() {
        let rows = (0..<4).map(row)
        let window = ImagePrefetch.window(rows: rows, around: .init(row: 0, time: noon))
        XCTAssertEqual(Set(window.map(\.row)), Set(0...3))
        XCTAssertTrue(ImagePrefetch.window(rows: [[Item]](), around: .init(row: 0, time: noon)).isEmpty)
    }

    func testRefreshAfterMoreThanAnHourOrThreeRows() {
        let last = ImagePrefetch.Point(row: 10, time: noon)
        XCTAssertTrue(ImagePrefetch.needsRefresh(from: nil, to: last))
        XCTAssertFalse(ImagePrefetch.needsRefresh(from: last, to: .init(row: 13, time: noon.addingTimeInterval(3600))))
        XCTAssertFalse(ImagePrefetch.needsRefresh(from: last, to: .init(row: 7, time: noon.addingTimeInterval(-3600))))
        XCTAssertTrue(ImagePrefetch.needsRefresh(from: last, to: .init(row: 14, time: noon)))
        XCTAssertTrue(ImagePrefetch.needsRefresh(from: last, to: .init(row: 10, time: noon.addingTimeInterval(-3601))))
    }

    func testQueueLimitsPerLane() {
        var queue = ImageQueue<String>()
        queue.replace(with: (1...6).map { ("ct\($0)", .ct) } + (1...3).map { ("tv\($0)", .gateway) })
        let started = queue.start().map(\.key)
        XCTAssertEqual(started, ["ct1", "ct2", "ct3", "ct4", "tv1", "tv2"])
        XCTAssertTrue(queue.start().isEmpty, "all lanes full")
        queue.finish("tv1")
        XCTAssertEqual(queue.start().map(\.key), ["tv3"])
        queue.finish("ct2")
        XCTAssertEqual(queue.start().map(\.key), ["ct5"])
    }

    func testQueueDropsStaleAndPrioritizesFocus() {
        var queue = ImageQueue<String>()
        queue.replace(with: (1...8).map { ("ct\($0)", .ct) })
        _ = queue.start()  // ct1...ct4 running
        queue.prioritize("ct8", lane: .ct)
        XCTAssertEqual(queue.pending.map(\.key), ["ct8", "ct5", "ct6", "ct7"])
        // a new window: ct5/ct6 left it, ct2 still running is not queued again, focused ct8 stays first
        queue.replace(with: [("ct2", .ct), ("ct9", .ct), ("ct7", .ct)], keep: "ct8")
        XCTAssertEqual(queue.pending.map(\.key), ["ct8", "ct9", "ct7"])
        queue.prioritize("ct3", lane: .ct)
        XCTAssertEqual(queue.pending.map(\.key), ["ct8", "ct9", "ct7"], "a running key is not queued")
        queue.finish("ct1")
        XCTAssertEqual(queue.start().map(\.key), ["ct8"])
    }

    func testThumbnailMisses() {
        var misses = ThumbnailMisses()
        misses.record("done", final: true, at: noon)
        misses.record("growing", final: false, at: noon)
        XCTAssertTrue(misses.isMissing("done", at: noon.addingTimeInterval(86_400)))
        XCTAssertTrue(misses.isMissing("growing", at: noon.addingTimeInterval(59)))
        XCTAssertFalse(misses.isMissing("growing", at: noon.addingTimeInterval(60)))
        XCTAssertFalse(misses.isMissing("other", at: noon))
        misses.removeAll()
        XCTAssertFalse(misses.isMissing("done", at: noon))
    }

    func testLRU() {
        var cache = LRUCache<String, Int>(capacity: 2)
        cache.set("a", 1)
        cache.set("b", 2)
        XCTAssertEqual(cache.get("a"), 1)
        cache.set("c", 3)  // b is the least recently used
        XCTAssertNil(cache.peek("b"))
        XCTAssertEqual(cache.peek("a"), 1)
        XCTAssertEqual(cache.count, 2)
        cache.removeAll { $0 == "a" }
        XCTAssertNil(cache.peek("a"))
        XCTAssertEqual(cache.count, 1)
    }
}
