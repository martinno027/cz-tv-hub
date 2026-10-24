import Foundation
import XCTest
@testable import CTCore

/// Golden cases of the Anténa timeline player (spec/fixtures/timeline_cases.json) and the gateway's timeline JSON.
final class TimelineTests: XCTestCase {
    private var golden: [String: Any] { get throws { try Fixture.json("timeline_cases") } }

    private func cases(_ name: String) throws -> [[String: Any]] {
        try XCTUnwrap(try golden[name] as? [[String: Any]], name)
    }

    private func double(_ value: Any?) -> Double { (value as! NSNumber).doubleValue }

    private func goldenMap() throws -> TimelineMap {
        let map = try XCTUnwrap(try golden["map"] as? [String: Any])
        let pieces = (map["pieces"] as! [[String: Any]]).map {
            DVBTimeline.Piece(position: double($0["position"]), time: utc($0["time"] as! String), duration: double($0["duration"]))
        }
        return TimelineMap(pieces: pieces)
    }

    private func goldenProgrammes() throws -> [DVBTimeline.Programme] {
        try cases("programmes").map {
            DVBTimeline.Programme(id: nil, title: $0["title"] as! String, start: utc($0["start"] as! String),
                                  end: utc($0["end"] as! String))
        }
    }

    // MARK: golden

    func testMapToTime() throws {
        let map = try goldenMap()
        let items = try XCTUnwrap(try golden["map"] as? [String: Any])["toTime"] as! [[String: Any]]
        XCTAssertEqual(items.count, 7)
        for item in items {
            let time = try XCTUnwrap(map.time(at: double(item["position"])))
            XCTAssertEqual(time.timeIntervalSince1970, utc(item["time"] as! String).timeIntervalSince1970, accuracy: 0.001,
                           item["name"] as! String)
        }
    }

    func testMapToPosition() throws {
        let map = try goldenMap()
        let items = try XCTUnwrap(try golden["map"] as? [String: Any])["toPosition"] as! [[String: Any]]
        XCTAssertEqual(items.count, 5)
        for item in items {
            XCTAssertEqual(map.position(at: utc(item["time"] as! String)), double(item["position"]), accuracy: 0.001,
                           item["name"] as! String)
        }
    }

    func testEdge() throws {
        let items = try cases("edge")
        XCTAssertEqual(items.count, 7)
        for item in items {
            let edge = TimelineRules.edge(target: double(item["target"]), duration: double(item["duration"]),
                                          complete: item["complete"] as! Bool, hasPrevious: item["previous"] as! Bool,
                                          hasNext: item["next"] as! Bool)
            XCTAssertEqual(edge.rawValue, item["result"] as? String, item["name"] as! String)
        }
    }

    func testHold() throws {
        let items = try cases("hold")
        XCTAssertEqual(items.count, 5)
        for item in items {
            XCTAssertEqual(TimelineRules.holdOffset(held: double(item["held"])), double(item["offset"]), accuracy: 0.001,
                           item["name"] as! String)
        }
    }

    func testLabels() throws {
        let items = try cases("labels")
        XCTAssertEqual(items.count, 6)
        for item in items {
            XCTAssertEqual(TimelineRules.label(utc(item["time"] as! String), now: utc(item["now"] as! String)),
                           item["label"] as? String, item["name"] as! String)
        }
    }

    func testEdgeLabels() throws {
        let items = try cases("edges")
        XCTAssertEqual(items.count, 2)
        for item in items {
            let labels = TimelineRules.edgeLabels(start: utc(item["start"] as! String), end: utc(item["end"] as! String),
                                                  playhead: utc(item["playhead"] as! String), now: utc(item["now"] as! String))
            XCTAssertEqual(labels.start, item["startLabel"] as? String, item["name"] as! String)
            XCTAssertEqual(labels.end, item["endLabel"] as? String, item["name"] as! String)
        }
    }

    func testProgrammeAt() throws {
        let programmes = try goldenProgrammes()
        let items = try cases("programmeAt")
        XCTAssertEqual(items.count, 4)
        for item in items {
            XCTAssertEqual(TimelineRules.programmeAt(utc(item["time"] as! String), in: programmes), item["index"] as? Int,
                           item["name"] as! String)
        }
    }

    func testJump() throws {
        let programmes = try goldenProgrammes()
        let items = try cases("jump")
        XCTAssertEqual(items.count, 7)
        for item in items {
            let direction = try XCTUnwrap(TimelineRules.Direction(rawValue: item["direction"] as! String))
            let target = TimelineRules.jump(from: utc(item["time"] as! String), direction, in: programmes)
            XCTAssertEqual(target, (item["target"] as? String).map(utc), item["name"] as! String)
        }
    }

    func testResume() throws {
        let items = try cases("resume")
        XCTAssertEqual(items.count, 2)
        for item in items {
            let programme = item["programme"] as! [String: Any]
            XCTAssertEqual(TimelineRules.resumeProgress(time: utc(item["time"] as! String),
                                                        programmeStart: utc(programme["start"] as! String)),
                           double(item["progress"]), item["name"] as! String)
        }
    }

    // MARK: rules beyond the golden file

    func testGrowingWindowRunsOnPastItsLastPiece() throws {
        let pieces = try goldenMap().pieces
        let growing = TimelineMap(pieces: pieces, growing: true)
        XCTAssertEqual(growing.time(at: 6000), utc("2026-10-24T10:45:00Z"))
        XCTAssertEqual(growing.position(at: utc("2026-10-24T10:45:00Z")), 6000)
        XCTAssertNotEqual(growing.time(at: 6000), TimelineMap(pieces: pieces).time(at: 6000))
    }

    func testGrowingEndReadsNowWhileCurrent() {
        let now = utc("2026-10-24T19:00:00Z")
        let start = utc("2026-10-24T14:00:00Z")
        XCTAssertEqual(TimelineRules.edgeLabels(start: start, end: utc("2026-10-24T18:59:31Z"), playhead: start, now: now,
                                                growing: true).end, "Teď")
        XCTAssertEqual(TimelineRules.edgeLabels(start: start, end: utc("2026-10-24T18:59:00Z"), playhead: start, now: now,
                                                growing: true).end, "20:59")
        XCTAssertEqual(TimelineRules.edgeLabels(start: start, end: utc("2026-10-24T18:59:59Z"), playhead: start, now: now).end,
                       "20:59", "a complete window shows its end time")
    }

    func testStartTime() {
        let start = utc("2026-10-24T09:00:00Z")
        XCTAssertEqual(TimelineRules.startTime(programmeStart: start, resumeAt: nil), utc("2026-10-24T08:59:30Z"))
        XCTAssertEqual(TimelineRules.startTime(programmeStart: start, resumeAt: 750), utc("2026-10-24T09:12:30Z"))
    }

    func testWindowBreaksGoThroughTheMap() throws {
        let map = try goldenMap()
        // Programme starts 10:05:00 (= window position 3600); its playlist starts 30 s earlier, in the gap.
        let breaks = TimelineRules.windowBreaks([AdBreak(start: 90, end: 150, confidence: 0.8), AdBreak(start: 0, end: 20)],
                                                programmeStart: utc("2026-10-24T10:05:00Z"), map: map)
        XCTAssertEqual(breaks, [AdBreak(start: 3660, end: 3720, confidence: 0.8)], "a break inside the gap is dropped")
    }

    func testProgrammeEndBeforeAGapIsNotOnAir() throws {
        let programmes = try goldenProgrammes()
        // "Film" ends 11:00, "Počasí" starts 11:10.
        XCTAssertNil(TimelineRules.programmeAt(utc("2026-10-24T11:00:00Z"), in: programmes))
        XCTAssertEqual(TimelineRules.programmeAt(utc("2026-10-24T10:59:59Z"), in: programmes), 1)
    }

    func testUpSkipsAProgrammeStartingInARecordingGap() throws {
        let timeline = try DVBParser.timeline(status: 200, body: try sample())
        let map = timeline.map
        // Recording resumes 09:05 (10800); "Zprávy" starts 09:00 inside the gap, so it maps to the playhead.
        let atResume = map.position(at: utc("2026-10-24T09:05:00Z"))
        XCTAssertEqual(atResume, 10800)
        XCTAssertEqual(TimelineRules.jumpTarget(from: atResume, .up, map: map, programmes: timeline.programmes), 0,
                       "▲ takes the programme before (Ranní show 06:00)")
        XCTAssertEqual(TimelineRules.jumpTarget(from: atResume + 5, .up, map: map, programmes: timeline.programmes), 0)
        // More than 10 s after the resume ▲ goes to Zprávy's mapped start, the resume point.
        XCTAssertEqual(TimelineRules.jumpTarget(from: atResume + 60, .up, map: map, programmes: timeline.programmes), 10800)
        // Without the gap rule the target would be the playhead itself (stuck): prove the variants differ.
        let plain = map.position(at: try XCTUnwrap(TimelineRules.jump(from: try XCTUnwrap(map.time(at: atResume)), .up,
                                                                      in: timeline.programmes)))
        XCTAssertEqual(plain, atResume)
        // ▼ from Zprávy: Teleshopping 09:30 = 10800 + 25 min.
        XCTAssertEqual(TimelineRules.jumpTarget(from: atResume, .down, map: map, programmes: timeline.programmes), 12300)
    }

    func testJumpTargetsOutsideTheWindowFollowTheEdgeRule() throws {
        let map = try goldenMap()  // 09:00–10:35 with a gap 10:00–10:05
        let programmes = [
            DVBTimeline.Programme(id: "a", title: "Před", start: utc("2026-10-24T08:30:00Z"), end: utc("2026-10-24T09:20:00Z")),
            DVBTimeline.Programme(id: "b", title: "Uvnitř", start: utc("2026-10-24T09:20:00Z"), end: utc("2026-10-24T10:40:00Z")),
            DVBTimeline.Programme(id: "c", title: "Po", start: utc("2026-10-24T10:40:00Z"), end: utc("2026-10-24T11:00:00Z")),
        ]
        // ▲ from 09:05 (5 min into "Před") goes to 08:30, 30 min before the window: a negative target.
        let up = try XCTUnwrap(TimelineRules.jumpTarget(from: 300, .up, map: map, programmes: programmes))
        XCTAssertEqual(up, -1800)
        XCTAssertEqual(TimelineRules.edge(target: up, duration: map.duration, complete: true, hasPrevious: true, hasNext: false), .previous)
        // ▼ from inside "Uvnitř" goes to 10:40, 5 min after the window's end.
        let down = try XCTUnwrap(TimelineRules.jumpTarget(from: 4000, .down, map: map, programmes: programmes))
        XCTAssertEqual(down, 5400 + 300)
        XCTAssertEqual(TimelineRules.edge(target: down, duration: map.duration, complete: true, hasPrevious: false, hasNext: true), .next)
        XCTAssertEqual(map.unclampedPosition(at: utc("2026-10-24T09:30:00Z")), map.position(at: utc("2026-10-24T09:30:00Z")))
    }

    func testContinuousRecordingIDs() {
        XCTAssertTrue(TimelineIDs.isContinuous("bnova1-1792832400-1792834200"))
        XCTAssertTrue(TimelineIDs.isContinuous("bct1_hd-1792832400-1792834200"))
        XCTAssertFalse(TimelineIDs.isContinuous("b2de4250e92ae9a4c549f7e48885ff8"), "a TVHeadend uuid")
        XCTAssertFalse(TimelineIDs.isContinuous("b78bcbaa893cfa623208e2efbab5f165"), "the uuid in dvb_epg_today.json")
        XCTAssertFalse(TimelineIDs.isContinuous("cb2de4250e92ae9a4c549f7e48885ff8"))
        XCTAssertFalse(TimelineIDs.isContinuous("bnova1-1792832400-1792834200/../x"))
    }

    // MARK: parser

    private func sample() throws -> Data {
        try Data(contentsOf: Fixture.directory.appendingPathComponent("dvb/dvb_timeline.json"))
    }

    func testParsesSample() throws {
        let timeline = try DVBParser.timeline(status: 200, body: try sample())
        XCTAssertEqual(timeline.id, "bnova1-1792832400-1792834200")
        XCTAssertEqual(timeline.channel, "nova1")
        XCTAssertEqual(timeline.playlist, "/dvb/timeline/bnova1-1792832400-1792834200.m3u8")
        XCTAssertTrue(timeline.complete)
        XCTAssertFalse(timeline.map.growing)
        XCTAssertEqual(timeline.programmeStart, utc("2026-10-24T09:00:00Z"))
        XCTAssertEqual(timeline.map.pieces.count, 2)
        XCTAssertEqual(timeline.map.duration, 21300.5)
        XCTAssertEqual(timeline.programmes.map(\.title), ["Ranní show", "Zprávy", "Teleshopping", "Film: Testovací obrazec"])
        XCTAssertNil(timeline.programmes[2].id)
        XCTAssertEqual(timeline.programmes[1].subtitle, "Ranní vydání")
        XCTAssertEqual(timeline.programmes.map(\.breaks), [false, true, false, false])
        XCTAssertEqual(timeline.previous, "bnova1-1792810800-1792821600")
        XCTAssertNil(timeline.next)
        // Programme start − 30 s is the end of the first piece; the start itself lies in the gap (09:00–09:05).
        XCTAssertEqual(timeline.map.position(at: TimelineRules.startTime(programmeStart: timeline.programmeStart, resumeAt: nil)),
                       10770)
        XCTAssertEqual(timeline.map.position(at: timeline.programmeStart), 10800)
    }

    private func mutated(_ change: (inout [String: Any]) -> Void) throws -> Data {
        var root = try JSONSerialization.jsonObject(with: try sample()) as! [String: Any]
        change(&root)
        return try JSONSerialization.data(withJSONObject: root)
    }

    func testBadValuesThrow() throws {
        let broken: [String: (inout [String: Any]) -> Void] = [
            "no id": { $0["id"] = nil },
            "absolute playlist": { $0["playlist"] = "http://elsewhere.example/x.m3u8" },
            "protocol-relative playlist": { $0["playlist"] = "//elsewhere.example/x.m3u8" },
            "relative playlist": { $0["playlist"] = "x.m3u8" },
            "no pieces": { $0["pieces"] = [] },
            "piece without time": { $0["pieces"] = [["position": 0, "duration": 10]] },
            "negative duration": { $0["pieces"] = [["position": 0, "time": "2026-10-24T06:00:00Z", "duration": -1]] },
            "string position": { $0["pieces"] = [["position": "0", "time": "2026-10-24T06:00:00Z", "duration": 1]] },
            "overlapping pieces": { $0["pieces"] = [["position": 0, "time": "2026-10-24T06:00:00Z", "duration": 100],
                                                    ["position": 50, "time": "2026-10-24T07:00:00Z", "duration": 100]] },
            "no programme": { $0["programme"] = nil },
        ]
        for (name, change) in broken {
            XCTAssertThrowsError(try DVBParser.timeline(status: 200, body: try mutated(change)), name)
        }
        XCTAssertThrowsError(try DVBParser.timeline(status: 200, body: Data("[]".utf8)))
        XCTAssertThrowsError(try DVBParser.timeline(status: 404, body: Data(#"{"error": "Pořad není k dispozici"}"#.utf8))) {
            XCTAssertEqual(($0 as? CTError)?.userMessage, "Pořad není k dispozici")
        }
    }

    func testLenientFields() throws {
        let timeline = try DVBParser.timeline(status: 200, body: try mutated {
            $0["complete"] = nil
            $0["previous"] = ""
            $0["programmes"] = [42, ["title": "bez času"], ["id": "", "title": "B", "start": "2026-10-24T10:00:00Z",
                                                            "end": "2026-10-24T11:00:00Z", "breaks": "yes"],
                                ["title": "A", "start": "2026-10-24T09:00:00Z", "end": "2026-10-24T10:00:00Z"]]
        })
        XCTAssertFalse(timeline.complete, "missing complete = growing")
        XCTAssertTrue(timeline.map.growing)
        XCTAssertNil(timeline.previous)
        XCTAssertEqual(timeline.programmes.map(\.title), ["A", "B"], "invalid entries dropped, sorted by start")
        XCTAssertNil(timeline.programmes[1].id)
        XCTAssertFalse(timeline.programmes[1].breaks)
    }

    func testClientRequestsTimeline() async throws {
        let transport = FakeTransport()
        transport.on("/dvb/timeline/bnova1-1792832400-1792834200", HTTPResponse(status: 200, body: try sample()))
        let client = DVBClient(config: GatewayConfig(baseURL: URL(string: "http://pi.local:8098")!, key: "k"), transport: transport)
        let timeline = try await client.timeline(id: "bnova1-1792832400-1792834200")
        XCTAssertEqual(transport.requests.first?.value(forHTTPHeaderField: "Authorization"), "Bearer k")
        XCTAssertEqual(client.timelinePlaylistURL(timeline).absoluteString,
                       "http://pi.local:8098/dvb/timeline/bnova1-1792832400-1792834200.m3u8")
    }
}
