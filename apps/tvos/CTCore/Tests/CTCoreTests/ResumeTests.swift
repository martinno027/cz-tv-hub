import Foundation
import XCTest
@testable import CTCore

final class ResumeRulesTests: XCTestCase {
    func testConstantsMatchGoldenFile() throws {
        let golden = try Fixture.json("resume_cases")
        XCTAssertEqual(golden["minSeconds"] as? Double, ResumeRules.minSeconds)
        XCTAssertEqual(golden["endMarginSeconds"] as? Double, ResumeRules.endMarginSeconds)
        XCTAssertEqual(golden["maxEntries"] as? Int, ResumeRules.maxEntries)
        XCTAssertEqual(golden["maxAgeDays"] as? Int, ResumeRules.maxAgeDays)
    }

    func testGoldenOfferCases() throws {
        let cases = try Fixture.json("resume_cases")["offer"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 9)
        for item in cases {
            let progress = (item["progress"] as! NSNumber).doubleValue
            let length = (item["length"] as! NSNumber).doubleValue
            let name = "\(progress)/\(length)"
            let decision = ResumeRules.decide(progress: progress, length: length)
            switch item["action"] as! String {
            case "none": XCTAssertEqual(decision, .none, name)
            case "clear": XCTAssertEqual(decision, .clear, name)
            case "offer":
                XCTAssertEqual(decision, .offer(seconds: progress), name)
                XCTAssertEqual(ResumeRules.label(progress), item["label"] as? String, name)
            case let other: XCTFail("unknown action \(other)")
            }
        }
    }

    func testButtonTitleAndKeys() {
        XCTAssertEqual(ResumeRules.buttonTitle(1390), "Pokračovat od 23:10")
        XCTAssertEqual(ResumeRules.ctKey(idec: "226411010101016"), "ct:226411010101016")
        XCTAssertEqual(ResumeRules.dvbKey(recordingID: "abc"), "dvb:abc")
    }

    func testGoldenStartOverCases() throws {
        let cases = try Fixture.json("resume_cases")["startover"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 3)
        for item in cases {
            let name = item["name"] as! String
            let expect = item["expect"] as! [String: Any]
            let mode = PlaybackPlanner.startOverMode(start: utc(item["start"] as! String), now: utc(item["now"] as! String),
                                                     resumeAt: (item["progress"] as! NSNumber).doubleValue)
            XCTAssertEqual(mode, .startOver(seekDate: utc(expect["seekToWallClock"] as! String),
                                            offset: (expect["fallbackOffsetSeconds"] as! NSNumber).doubleValue), name)
        }
    }

    func testProgressPerMode() {
        let start = utc("2026-10-17T16:00:00Z")
        let now = utc("2026-10-17T18:00:00Z")
        let startOver = PlaybackSource.Mode.startOver(seekDate: start, offset: 0)
        XCTAssertNil(ResumeRules.progress(mode: .live, position: 500, frameDate: now, liveEdgePosition: 600,
                                          programmeStart: start, now: now))
        XCTAssertEqual(ResumeRules.progress(mode: .vod, position: 1390, frameDate: nil, liveEdgePosition: nil,
                                            programmeStart: start, now: now), 1390)
        XCTAssertEqual(ResumeRules.progress(mode: .recording, position: 61, frameDate: nil, liveEdgePosition: nil,
                                            programmeStart: start, now: now), 61)
        XCTAssertEqual(ResumeRules.progress(mode: startOver, position: 0, frameDate: utc("2026-10-17T16:20:00Z"),
                                            liveEdgePosition: nil, programmeStart: start, now: now), 1200)
        // No frame date: 1 h behind the live edge = 17:00 → 3600 s into the programme.
        XCTAssertEqual(ResumeRules.progress(mode: startOver, position: 6000, frameDate: nil, liveEdgePosition: 9600,
                                            programmeStart: start, now: now), 3600)
        XCTAssertNil(ResumeRules.progress(mode: startOver, position: nil, frameDate: nil, liveEdgePosition: nil,
                                          programmeStart: start, now: now))
    }
}

final class MemoryBackend: ResumeBackend {
    var values: [String: Any] = [:]
    func data(forKey key: String) -> Data? { values[key] as? Data }
    func set(_ value: Any?, forKey key: String) { values[key] = value }
}

@MainActor
final class ResumeStoreTests: XCTestCase {
    let target = ResumeTarget(key: "ct:1", programmeStart: utc("2026-10-17T16:00:00Z"), length: 3600)

    func testSaveOfferAndClear() {
        let backend = MemoryBackend()
        let store = ResumeStore(backend: backend, clock: { utc("2026-10-17T18:00:00Z") })
        store.save(target, progress: 30)
        XCTAssertNil(store.offer(for: "ct:1"), "below 60 s nothing is stored")
        XCTAssertEqual(store.count, 0)

        store.save(target, progress: 1390)
        XCTAssertEqual(store.offer(for: "ct:1"), 1390)
        XCTAssertEqual(store.entry(for: "ct:1"),
                       ResumeEntry(progressSeconds: 1390, lengthSeconds: 3600, savedAt: utc("2026-10-17T18:00:00Z")))

        store.save(target, progress: 30)
        XCTAssertEqual(store.offer(for: "ct:1"), 1390, "a short replay keeps the earlier position")

        // Persisted: a new store over the same backend sees it.
        XCTAssertEqual(ResumeStore(backend: backend, clock: { utc("2026-10-17T18:00:00Z") }).offer(for: "ct:1"), 1390)

        store.save(target, progress: 3480)
        XCTAssertNil(store.offer(for: "ct:1"), "watched: removed")
        XCTAssertEqual(store.count, 0)
    }

    func testOfferUsesCurrentLength() {
        let store = ResumeStore(backend: MemoryBackend(), clock: { utc("2026-10-17T18:00:00Z") })
        store.save(target, progress: 3000)
        XCTAssertEqual(store.offer(for: "ct:1"), 3000)
        XCTAssertNil(store.offer(for: "ct:1", length: 3100), "the programme turned out shorter: counts as watched")
        store.remove("ct:1")
        XCTAssertEqual(store.count, 0)
    }

    func testDropsEntriesOlderThan30Days() {
        let backend = MemoryBackend()
        let clock = MutableClock(utc("2026-09-01T12:00:00Z"))
        let store = ResumeStore(backend: backend, clock: { clock.now })
        store.save(ResumeTarget(key: "dvb:old", programmeStart: clock.now, length: 3600), progress: 600)
        clock.now = utc("2026-09-25T12:00:00Z")
        store.save(ResumeTarget(key: "dvb:new", programmeStart: clock.now, length: 3600), progress: 600)

        clock.now = utc("2026-10-01T12:00:01Z")  // 30 days + 1 s after the first save
        let reopened = ResumeStore(backend: backend, clock: { clock.now })
        XCTAssertNil(reopened.offer(for: "dvb:old"))
        XCTAssertEqual(reopened.offer(for: "dvb:new"), 600)
        XCTAssertEqual(reopened.count, 1)
    }

    func testKeepsNewest200Entries() {
        let clock = MutableClock(utc("2026-10-01T00:00:00Z"))
        let store = ResumeStore(backend: MemoryBackend(), clock: { clock.now })
        for index in 0..<205 {
            clock.now = utc("2026-10-01T00:00:00Z").addingTimeInterval(TimeInterval(index * 60))
            store.save(ResumeTarget(key: "ct:\(index)", programmeStart: clock.now, length: 3600), progress: 600)
        }
        XCTAssertEqual(store.count, 200)
        for index in 0..<5 { XCTAssertNil(store.offer(for: "ct:\(index)"), "oldest dropped: \(index)") }
        XCTAssertEqual(store.offer(for: "ct:5"), 600)
        XCTAssertEqual(store.offer(for: "ct:204"), 600)
    }

    func testCorruptStorageIsIgnored() {
        let backend = MemoryBackend()
        backend.values[ResumeStore.storageKey] = Data("not json".utf8)
        let store = ResumeStore(backend: backend)
        XCTAssertNil(store.offer(for: "ct:1"))
        store.save(target, progress: 600)
        XCTAssertEqual(store.offer(for: "ct:1"), 600)
    }
}

final class ResumePlannerTests: XCTestCase {
    let now = utc("2026-10-17T18:00:00Z")
    let ct1 = Channels.byID("CH_1")!
    var transport: FakeTransport!
    var planner: PlaybackPlanner!

    override func setUp() async throws {
        transport = FakeTransport()
        try transport.on("/channel/", fixture: "live_CH_1")
        let now = now
        planner = PlaybackPlanner(client: CTClient(transport: transport), clock: { now })
    }

    private func programme(_ start: String, _ end: String, playable: Bool) -> Programme {
        Programme(channel: "ct1", idec: "226411010101016", title: "Pořad", start: utc(start), end: utc(end), isPlayableNow: playable)
    }

    func testEndedInBufferResumesByWallClock() async throws {
        let p = programme("2026-10-17T16:00:00Z", "2026-10-17T17:00:00Z", playable: true)
        let source = try await planner.resolve(.programme(p, channel: ct1), resumeAt: 1200)
        XCTAssertEqual(source.mode, .startOver(seekDate: utc("2026-10-17T16:20:00Z"), offset: 4800))
        XCTAssertNil(source.startPosition)
    }

    func testAiringResumes() async throws {
        let p = programme("2026-10-17T17:30:00Z", "2026-10-17T19:00:00Z", playable: true)
        let source = try await planner.resolve(.programme(p, channel: ct1), resumeAt: 600)
        XCTAssertEqual(source.mode, .startOver(seekDate: utc("2026-10-17T17:40:00Z"), offset: 9600))
    }

    func testArchiveStartsAtProgress() async throws {
        try transport.on("/media/external/", fixture: "vod_ok")
        let p = programme("2026-10-17T08:00:00Z", "2026-10-17T09:00:00Z", playable: true)
        let source = try await planner.resolve(.programme(p, channel: ct1), resumeAt: 1390)
        XCTAssertEqual(source.mode, .vod)
        XCTAssertEqual(source.startPosition, 1390)
    }

    func testStillLiveFallbackKeepsResume() async throws {
        try transport.on("/media/external/", fixture: "vod_still_live")
        let p = programme("2026-10-17T14:00:00Z", "2026-10-17T14:50:00Z", playable: true)
        let source = try await planner.resolve(.programme(p, channel: ct1), resumeAt: 600)
        XCTAssertEqual(source.mode, .startOver(seekDate: utc("2026-10-17T14:10:00Z"), offset: 0))
    }

    func testWithoutResumeUnchanged() async throws {
        try transport.on("/media/external/", fixture: "vod_ok")
        let archived = try await planner.resolve(.programme(programme("2026-10-17T08:00:00Z", "2026-10-17T09:00:00Z", playable: true), channel: ct1))
        XCTAssertNil(archived.startPosition)
        let live = try await planner.resolve(.live(ct1), resumeAt: 600)
        XCTAssertEqual(live.mode, .live)
        XCTAssertNil(live.startPosition)
    }

    func testAntennaRecordingStartsAtProgress() {
        let url = URL(string: "http://pi:8098/dvb/recording/r1.m3u8")!
        let resumed = PlaybackSource.antennaRecording(url: url, title: "Zprávy", headers: ["Authorization": "Bearer k"], resumeAt: 754)
        XCTAssertEqual(resumed.mode, .recording)
        XCTAssertEqual(resumed.startPosition, 754)
        XCTAssertEqual(resumed.headers, ["Authorization": "Bearer k"])
        XCTAssertNil(PlaybackSource.antennaRecording(url: url, title: "Zprávy", headers: [:]).startPosition)
    }
}
