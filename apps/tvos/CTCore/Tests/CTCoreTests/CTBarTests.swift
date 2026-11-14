import Foundation
import XCTest
@testable import CTCore

/// Golden cases of the ČT player bar (spec/fixtures/ct_bar_cases.json, T62) and the window the timeline player uses
/// for ČT start-over and the archive.
final class CTBarTests: XCTestCase {
    private func cases(_ name: String) throws -> [[String: Any]] {
        try XCTUnwrap(try Fixture.json("ct_bar_cases")[name] as? [[String: Any]], name)
    }

    private func double(_ value: Any?) -> Double { (value as! NSNumber).doubleValue }

    private func date(_ value: Any?) -> Date? { (value as? String).map(utc) }

    // MARK: golden

    func testWindow() throws {
        let items = try cases("window")
        XCTAssertEqual(items.count, 4)
        for item in items {
            let name = item["name"] as! String
            let now = try XCTUnwrap(date(item["now"]))
            let window: BarWindow
            switch item["kind"] as! String {
            case "timeshift":
                // The origin (wall clock of position 0) does not change the window's times.
                window = .ctTimeshift(id: "x", channel: "ČT1", origin: now.addingTimeInterval(-5000), now: now, programmes: [])
            default:
                let start = date(item["programmeStart"])
                window = .ctVOD(id: "x", channel: "ČT1", start: start, duration: double(item["duration"]), title: "Pořad")
            }
            XCTAssertEqual(window.start, date(item["start"]), name)
            XCTAssertEqual(window.end, date(item["end"]), name)
            XCTAssertEqual(window.growing, item["growing"] as! Bool, name)
        }
    }

    func testClamp() throws {
        let items = try cases("clamp")
        XCTAssertEqual(items.count, 9)
        for item in items {
            let name = item["name"] as! String
            switch item["kind"] as! String {
            case "timeshift":
                let now = try XCTUnwrap(date(item["now"]))
                let target = try XCTUnwrap(date(item["target"]))
                let result = try XCTUnwrap(date(item["result"]))
                XCTAssertEqual(CTBarRules.clamp(timeshift: target, now: now), result, name)
                // The same through the window's positions (origin = an arbitrary wall clock of position 0).
                let origin = now.addingTimeInterval(-3 * 3600 - 777)
                let window = BarWindow.ctTimeshift(id: "x", channel: "", origin: origin, now: now, programmes: [])
                XCTAssertEqual(window.clamped(target.timeIntervalSince(origin), now: now),
                               result.timeIntervalSince(origin), accuracy: 0.001, name)
            default:
                let duration = double(item["duration"])
                XCTAssertEqual(CTBarRules.clamp(vod: double(item["target"]), duration: duration), double(item["result"]), name)
                let window = BarWindow.ctVOD(id: "x", channel: "", start: nil, duration: duration, title: "")
                XCTAssertEqual(window.clamped(double(item["target"]), now: Date()), double(item["result"]), name)
            }
        }
    }

    func testLabels() throws {
        let items = try cases("labels")
        XCTAssertEqual(items.count, 3)
        for item in items {
            let name = item["name"] as! String
            let position = double(item["position"]), duration = double(item["duration"])
            XCTAssertEqual(CTBarRules.elapsedLabel(position), item["clock"] as? String, name)
            let edges = CTBarRules.elapsedEdges(duration: duration)
            XCTAssertEqual([edges.start, edges.end], item["edges"] as? [String], name)
            // What the bar shows for an archive item without a known start.
            let window = BarWindow.ctVOD(id: "x", channel: "", start: nil, duration: duration, title: "")
            XCTAssertTrue(window.elapsedClock)
            XCTAssertEqual(window.clockLabel(position: position, now: Date()), item["clock"] as? String, name)
            let end = try XCTUnwrap(window.map.time(at: duration))
            let bar = window.edgeLabels(end: end, playhead: end, now: Date())
            XCTAssertEqual([bar.start, bar.end], item["edges"] as? [String], name)
        }
    }

    // MARK: the window in the player

    func testTimeshiftMapsThroughTheOrigin() throws {
        let now = utc("2026-11-14T17:00:00Z")
        let origin = utc("2026-11-14T13:58:00Z")  // position 0 two minutes before the buffer's start
        let window = BarWindow.ctTimeshift(id: "x", channel: "", origin: origin, now: now, programmes: [])
        XCTAssertEqual(window.map.time(at: 3720), utc("2026-11-14T15:00:00Z"))
        XCTAssertEqual(window.map.position(at: utc("2026-11-14T16:00:00Z")), 7320, accuracy: 0.001)
        // The live edge's label reads "Teď".
        let edges = window.edgeLabels(end: now, playhead: utc("2026-11-14T16:00:00Z"), now: now)
        XCTAssertEqual(edges.end, "Teď")
        XCTAssertEqual(edges.start, "15:00")  // 14:00 UTC in Prague
        XCTAssertEqual(window.positionLimits(now: now), 180...10910)
    }

    func testTimeshiftLowerLimitSlidesWithNow() {
        let origin = utc("2026-11-14T14:00:00Z")
        let window = BarWindow.ctTimeshift(id: "x", channel: "", origin: origin, now: utc("2026-11-14T17:00:00Z"),
                                           programmes: [])
        // The same window asked later (a movement going on): the bounds move with now, not with the window's build.
        XCTAssertEqual(window.positionLimits(now: utc("2026-11-14T17:05:00Z")), 360...11090)
        XCTAssertEqual(window.clamped(0, now: utc("2026-11-14T17:05:00Z")), 360)
    }

    func testTimeshiftNowIsTheLiveEdge() {
        // The stream's newest segment is 25 s behind the real now: the window ends there and the upper limit is 10 s
        // before it (a wall-clock "now − 10 s" would be past the seekable end); the bounds still slide with now.
        let now = utc("2026-11-14T17:00:00Z")
        let origin = utc("2026-11-14T14:00:00Z")
        let edge = now.addingTimeInterval(-25)
        let window = BarWindow.ctTimeshift(id: "x", channel: "", origin: origin, now: edge, lag: 25, programmes: [])
        XCTAssertEqual(window.end, edge)
        XCTAssertEqual(window.positionLimits(now: now), 35...10765)
        XCTAssertEqual(window.positionLimits(now: now.addingTimeInterval(60)), 95...10825)
        // Its end is "Teď" although the live edge is more than 30 s behind the real now.
        XCTAssertEqual(BarWindow.ctTimeshift(id: "x", channel: "", origin: origin, now: now.addingTimeInterval(-184),
                                             lag: 184, programmes: [])
                        .edgeLabels(end: now.addingTimeInterval(-184), playhead: edge, now: now).end, "Teď")
        let realNow = BarWindow.ctTimeshift(id: "x", channel: "", origin: origin, now: now, programmes: [])
        XCTAssertNotEqual(realNow.positionLimits(now: now), window.positionLimits(now: now))
    }

    func testAnténaWindowHasNoLimits() {
        let piece = DVBTimeline.Piece(position: 0, time: utc("2026-11-14T14:00:00Z"), duration: 600)
        let timeline = DVBTimeline(id: "b1", channel: "c", playlist: "/p.m3u8", complete: true,
                                   programmeStart: utc("2026-11-14T14:00:00Z"), programmeEnd: utc("2026-11-14T14:10:00Z"),
                                   pieces: [piece], programmes: [], previous: "b0", next: nil)
        let window = BarWindow(timeline)
        XCTAssertNil(window.positionLimits(now: Date()))
        XCTAssertEqual(window.clamped(-50, now: Date()), -50)
        XCTAssertEqual(window.previous, "b0")
        XCTAssertFalse(window.elapsedClock)
    }

    func testVODWithAStartIsTheProgramme() throws {
        let start = utc("2026-11-10T18:55:00Z")
        let window = BarWindow.ctVOD(id: "123", channel: "ČT1", start: start, duration: 3600, title: "Film", subtitle: "Díl 2")
        XCTAssertFalse(window.elapsedClock)
        XCTAssertEqual(window.programmes.map(\.title), ["Film"])
        XCTAssertEqual(window.programmes.first?.id, "123")
        XCTAssertEqual(window.map.time(at: 600), start.addingTimeInterval(600))
        XCTAssertEqual(window.clockLabel(position: 600, now: utc("2026-11-14T17:00:00Z")), "út 10. 11. 20:05:00")
        // ▲ more than 10 s in = the programme's start; ▼ = nothing.
        XCTAssertEqual(TimelineRules.jumpTarget(from: 600, .up, map: window.map, programmes: window.programmes), 0)
        XCTAssertNil(TimelineRules.jumpTarget(from: 600, .down, map: window.map, programmes: window.programmes))
    }

    func testTimeshiftResumeProgramme() {
        let start = utc("2026-11-14T15:00:00Z")
        let programmes = [
            DVBTimeline.Programme(id: "a", title: "A", start: start.addingTimeInterval(-3600), end: start),
            DVBTimeline.Programme(id: "b", title: "B", start: start, end: start.addingTimeInterval(3600)),
            DVBTimeline.Programme(id: nil, title: "gap filler", start: start.addingTimeInterval(3600),
                                  end: start.addingTimeInterval(3700)),
        ]
        let opened = (id: "b", start: start)
        // In the 30 s before the opened programme: the opened one at 0, not the end of "a".
        let margin = CTBarRules.resumeProgramme(at: start.addingTimeInterval(-20), in: programmes, opened: opened)
        XCTAssertEqual(margin?.id, "b")
        XCTAssertEqual(margin?.progress, 0)
        // Earlier the programme at the playhead.
        let before = CTBarRules.resumeProgramme(at: start.addingTimeInterval(-40), in: programmes, opened: opened)
        XCTAssertEqual(before?.id, "a")
        XCTAssertEqual(before?.progress, 3560)
        let inside = CTBarRules.resumeProgramme(at: start.addingTimeInterval(600), in: programmes, opened: opened)
        XCTAssertEqual(inside?.id, "b")
        XCTAssertEqual(inside?.progress, 600)
        XCTAssertNil(CTBarRules.resumeProgramme(at: start.addingTimeInterval(3650), in: programmes, opened: opened))
    }

    func testSubtitleItems() {
        XCTAssertEqual(CTBarRules.subtitleTitle(name: "Czech", language: "cs", index: 1), "Titulky: Czech")
        XCTAssertEqual(CTBarRules.subtitleTitle(name: "  ", language: "cs", index: 1), "Titulky: čeština")
        XCTAssertEqual(CTBarRules.subtitleTitle(name: nil, language: "en-US", index: 2), "Titulky: angličtina")
        XCTAssertEqual(CTBarRules.subtitleTitle(name: nil, language: nil, index: 3), "Titulky 3")
        XCTAssertEqual(CTBarRules.subtitleTitle(name: nil, language: "qqq", index: 4), "Titulky 4")
        XCTAssertEqual(CTBarRules.subtitlesOff, "Titulky: Vypnuto")
        XCTAssertEqual(CTBarRules.trackListTitle(hasSubtitles: true), "Zvuk a titulky")
        XCTAssertEqual(CTBarRules.trackListTitle(hasSubtitles: false), "Zvuk")
    }

    func testKnownStart() {
        XCTAssertNil(CTBarRules.knownStart(Date(timeIntervalSince1970: 0)))
        XCTAssertEqual(CTBarRules.knownStart(utc("2026-11-10T18:55:00Z")), utc("2026-11-10T18:55:00Z"))
    }

    func testOnlyStartOverAndArchiveUseTheBar() {
        XCTAssertTrue(CTBarRules.usesBar(.startOver(seekDate: Date(), offset: 0)))
        XCTAssertTrue(CTBarRules.usesBar(.vod))
        XCTAssertFalse(CTBarRules.usesBar(.live))
        XCTAssertFalse(CTBarRules.usesBar(.recording))
    }

    func testTimeshiftProgrammesAddTheOpenedOne() {
        let start = utc("2026-11-14T15:00:00Z")
        func programme(_ idec: String, _ from: Double, _ to: Double) -> Programme {
            Programme(channel: "ct1", idec: idec, title: "P\(idec)", start: start.addingTimeInterval(from),
                      end: start.addingTimeInterval(to), isPlayableNow: true, episodeTitle: "e\(idec)")
        }
        let guide = [programme("2", 3600, 7200), programme("1", 0, 3600)]
        let inGuide = CTBarRules.programmes(guide: guide, opened: programme("1", 0, 3600))
        XCTAssertEqual(inGuide.map(\.id), ["1", "2"])
        XCTAssertEqual(inGuide.first?.subtitle, "e1")
        let missing = CTBarRules.programmes(guide: guide, opened: programme("9", -1800, 0))
        XCTAssertEqual(missing.map(\.id), ["9", "1", "2"])
        XCTAssertEqual(CTBarRules.programmes(guide: [], opened: programme("9", -1800, 0)).map(\.id), ["9"])
    }
}
