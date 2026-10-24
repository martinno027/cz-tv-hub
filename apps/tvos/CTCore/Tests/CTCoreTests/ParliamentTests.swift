import Foundation
import XCTest
@testable import CTCore

/// Golden cases `spec/fixtures/psp_cases.json` and the polling rules. Never contacts psp.cz or its CDN:
/// every request goes to `FakeTransport`, which fails anything it does not know.
final class ParliamentTests: XCTestCase {
    private var fixture: [String: Any] { get throws { try Fixture.json("psp_cases") } }

    func testCatalogueMatchesFixture() throws {
        let streams = try fixture["streams"] as! [[String: String]]
        XCTAssertEqual(PSPStreams.all.map(\.key), streams.map { $0["key"]! })
        XCTAssertEqual(PSPStreams.all.map(\.name), streams.map { $0["name"]! })
        XCTAssertEqual(PSPStreams.all.map(\.url.absoluteString), streams.map { $0["url"]! })
        XCTAssertEqual(PSPStreams.byKey("psp:stream3")?.name, "Výbory a semináře 1")
        XCTAssertEqual(ChannelPrefs.pspKey(PSPStreams.all[0]), "psp:stream1")
        XCTAssertEqual(MixedChannel.parliament(PSPStreams.all[1]).source, .psp)
        XCTAssertEqual(MixedChannel.parliament(PSPStreams.all[1]).name, "Tiskové konference")
    }

    func testIntervalsMatchFixture() throws {
        let value = try fixture
        XCTAssertEqual(PSPStreams.checkInterval, value["checkIntervalSeconds"] as! Double)
        XCTAssertEqual(PSPStreams.errorInterval, value["errorIntervalSeconds"] as! Double)
        XCTAssertEqual(PSPStreams.staleLive, value["staleLiveSeconds"] as! Double)
    }

    func testGoldenLiveCases() throws {
        let cases = try fixture["live"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 6)
        for item in cases {
            XCTAssertEqual(PSPStreams.isLive(status: item["status"] as! Int, body: item["body"] as! String),
                           item["live"] as! Bool, item["name"] as! String)
        }
        XCTAssertTrue(PSPStreams.isLive(status: 200, body: "\u{FEFF}#EXTM3U\r\n#EXTINF:6,\r\na.ts\r\n"))
    }

    func testOutcomeClasses() throws {
        // the fixture's answers: on, or not on; only a 5xx waits the error interval (120 s)
        let expected: [PSPLiveTracker.Outcome] = [.live, .live, .off, .off, .off, .serverError]
        let cases = try fixture["live"] as! [[String: Any]]
        XCTAssertEqual(cases.map { PSPLiveTracker.outcome(status: $0["status"] as! Int, body: $0["body"] as! String) }, expected)
        XCTAssertEqual(PSPLiveTracker.outcome(status: 403, body: "#EXTM3U\n#EXTINF:6,\na.ts\n"), .off)
        XCTAssertEqual(PSPLiveTracker.outcome(status: 500, body: ""), .serverError)
    }

    func testGoldenArrangeCases() throws {
        let cases = try fixture["arrange"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 2)
        for item in cases {
            let name = item["name"] as! String
            let raw = item["prefs"] as! [String: Any]
            let prefs = ChannelPrefs(order: raw["order"] as! [String], hidden: raw["hidden"] as! [String])
            let result = prefs.arrange(item["channels"] as! [String])
            XCTAssertEqual(result.all, item["all"] as! [String], name)
            XCTAssertEqual(result.visible, item["visible"] as! [String], name)
        }
    }

    func testGoldenSourcesCases() throws {
        let cases = try fixture["sources"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 2)
        for item in cases {
            // as stored: `null` / a list, decoded like the app's UserDefaults value
            let stored: [String: Any] = ["order": [], "hidden": [], "sourcesOff": item["sourcesOff"]!]
            let prefs = try JSONDecoder().decode(ChannelPrefs.self, from: JSONSerialization.data(withJSONObject: stored))
            let on = ChannelPrefs.Source.allCases.filter(prefs.isOn).map(\.rawValue)
            XCTAssertEqual(on, item["on"] as! [String], item["name"] as! String)
        }
    }

    @MainActor
    func testDefaultOrderAndSourceSwitch() {
        let store = ChannelPrefsStore(backend: MemoryBackend())
        let antenna = [DVBChannel(id: "a", number: 1, name: "A")]
        let keys = store.channels(antenna: antenna).visible.map(\.key)
        XCTAssertEqual(keys, Channels.all.map(ChannelPrefs.ctKey) + ["dvb:a"] + PSPStreams.all.map(\.key))
        // numbers and zapping run over the same list
        XCTAssertEqual(ChannelNumbers.number(of: "psp:stream1", in: keys), 8)
        XCTAssertEqual(ChannelNumbers.step(keys, from: "psp:stream5", by: 1), "ct:CH_1")
        store.update { $0.setOn(.psp, false) }
        XCTAssertFalse(store.channels(antenna: antenna).all.contains { $0.source == .psp })
        store.update { $0.setOn(.psp, true) }
        XCTAssertEqual(store.prefs.sourcesOff, [])
    }

    // MARK: polling rules

    func testTrackerStatesAndIntervals() {
        let t0 = utc("2026-10-24T10:00:00Z")
        var tracker = PSPLiveTracker()
        let key = "psp:stream1"
        XCTAssertEqual(tracker.state(of: key, now: t0), .unknown)
        XCTAssertEqual(tracker.due(key, now: t0), t0)

        tracker.record(key, .live, at: t0)
        XCTAssertEqual(tracker.state(of: key, now: t0), .live)
        XCTAssertEqual(tracker.due(key, now: t0), t0.addingTimeInterval(60))

        // a network error keeps "live", retried after 120 s; unconfirmed for 180 s it is unknown, not live
        tracker.record(key, .networkError, at: t0.addingTimeInterval(60))
        XCTAssertEqual(tracker.state(of: key, now: t0.addingTimeInterval(61)), .live)
        XCTAssertEqual(tracker.due(key, now: t0), t0.addingTimeInterval(180))
        XCTAssertEqual(tracker.nextExpiry(among: [key], now: t0.addingTimeInterval(61)), t0.addingTimeInterval(180))
        XCTAssertEqual(tracker.state(of: key, now: t0.addingTimeInterval(179)), .live)
        XCTAssertEqual(tracker.state(of: key, now: t0.addingTimeInterval(180)), .unknown)
        XCTAssertNil(tracker.nextExpiry(among: [key], now: t0.addingTimeInterval(180)))

        tracker.record(key, .live, at: t0.addingTimeInterval(200))
        XCTAssertEqual(tracker.state(of: key, now: t0.addingTimeInterval(201)), .live)
        tracker.record(key, .off, at: t0.addingTimeInterval(260))
        XCTAssertEqual(tracker.state(of: key, now: t0.addingTimeInterval(261)), .off)
        XCTAssertEqual(tracker.due(key, now: t0), t0.addingTimeInterval(320))

        // 5xx = not on, retried after 120 s
        tracker.record(key, .serverError, at: t0.addingTimeInterval(320))
        XCTAssertEqual(tracker.state(of: key, now: t0.addingTimeInterval(321)), .off)
        XCTAssertEqual(tracker.due(key, now: t0), t0.addingTimeInterval(440))

        // a network error keeps "off" too
        tracker.record(key, .off, at: t0.addingTimeInterval(500))
        tracker.record(key, .networkError, at: t0.addingTimeInterval(560))
        XCTAssertEqual(tracker.state(of: key, now: t0.addingTimeInterval(561)), .off)
        XCTAssertEqual(tracker.due(key, now: t0), t0.addingTimeInterval(680))

        // a first check that fails without an answer stays unknown
        tracker.record("psp:stream2", .networkError, at: t0)
        XCTAssertEqual(tracker.state(of: "psp:stream2", now: t0), .unknown)
    }

    func testTrackerPicksStreamsOneAfterAnother() {
        let t0 = utc("2026-10-24T10:00:00Z")
        let keys = PSPStreams.all.map(\.key)
        var tracker = PSPLiveTracker()
        var now = t0
        var order: [String] = []
        for _ in 0..<5 {
            let next = tracker.next(among: keys, now: now)!
            XCTAssertEqual(next.at, now)
            order.append(next.key)
            tracker.record(next.key, .off, at: now)
            now = now.addingTimeInterval(1)
        }
        XCTAssertEqual(order, keys)
        // all checked: the first is due again 60 s after its check
        let next = tracker.next(among: keys, now: now)!
        XCTAssertEqual(next.key, "psp:stream1")
        XCTAssertEqual(next.at, t0.addingTimeInterval(60))
    }

    @MainActor
    func testMonitorChecksPlaylistsOnlyWithoutHeaders() async throws {
        let transport = FakeTransport()
        let live = try (fixture["live"] as! [[String: Any]])[0]["body"] as! String
        transport.on("ps-stream1/", HTTPResponse(status: 200, body: Data(live.utf8)))
        transport.on("ps-stream2/", HTTPResponse(status: 404, body: Data("NOT_FOUND".utf8)))
        transport.on("ps-stream3/", HTTPResponse(status: 503, body: Data()))
        transport.fail("ps-stream4/", URLError(.notConnectedToInternet))
        // stream5: no route → FakeTransport throws, like a network error
        let clock = MutableClock(utc("2026-10-24T10:00:00Z"))
        let monitor = PSPLiveMonitor(transport: transport, clock: { clock.now })

        let task = Task { await monitor.watch(PSPStreams.all) }
        for _ in 0..<200 where transport.requests.count < 5 { try await Task.sleep(for: .milliseconds(10)) }
        try await Task.sleep(for: .milliseconds(50))
        task.cancel()
        await task.value

        // one request per stream (the next ones are 60/120 s away), only the published playlist URLs
        XCTAssertEqual(transport.requests.map { $0.url!.absoluteString }, PSPStreams.all.map(\.url.absoluteString))
        for request in transport.requests {
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertTrue(request.allHTTPHeaderFields?.isEmpty ?? true, "no extra or faked headers")
        }
        XCTAssertEqual(monitor.state(of: "psp:stream1"), .live)
        XCTAssertEqual(monitor.state(of: "psp:stream2"), .off)
        XCTAssertEqual(monitor.state(of: "psp:stream3"), .off)
        XCTAssertEqual(monitor.state(of: "psp:stream4"), .unknown)
        XCTAssertEqual(monitor.state(of: "psp:stream5"), .unknown)

        // a 404 when opening
        monitor.markOff("psp:stream1")
        XCTAssertEqual(monitor.state(of: "psp:stream1"), .off)
    }

    @MainActor
    func testMonitorRespectsIntervalAcrossWatches() async throws {
        let transport = FakeTransport()
        transport.on("ps-stream1/", HTTPResponse(status: 404, body: Data()))
        let clock = MutableClock(utc("2026-10-24T10:00:00Z"))
        let monitor = PSPLiveMonitor(transport: transport, clock: { clock.now })
        let stream = [PSPStreams.all[0]]

        var task = Task { await monitor.watch(stream) }
        for _ in 0..<200 where transport.requests.isEmpty { try await Task.sleep(for: .milliseconds(10)) }
        task.cancel()
        await task.value
        XCTAssertEqual(transport.requests.count, 1)

        // leaving and coming back within a minute does not check again
        clock.now = clock.now.addingTimeInterval(30)
        task = Task { await monitor.watch(stream) }
        try await Task.sleep(for: .milliseconds(100))
        task.cancel()
        await task.value
        XCTAssertEqual(transport.requests.count, 1)

        // nothing watches: nothing is checked, however long
        clock.now = clock.now.addingTimeInterval(3600)
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(transport.requests.count, 1)
    }

    @MainActor
    func testStaleLiveIsNotShownWhenWatchingAgain() async throws {
        let transport = FakeTransport()
        transport.on("ps-stream1/", HTTPResponse(status: 200, body: Data("#EXTM3U\n#EXTINF:6,\na.ts\n".utf8)))
        let clock = MutableClock(utc("2026-10-24T10:00:00Z"))
        let monitor = PSPLiveMonitor(transport: transport, clock: { clock.now })
        var task = Task { await monitor.watch([PSPStreams.all[0]]) }
        for _ in 0..<200 where monitor.state(of: "psp:stream1") != .live { try await Task.sleep(for: .milliseconds(10)) }
        task.cancel()
        await task.value
        XCTAssertEqual(monitor.state(of: "psp:stream1"), .live)

        // back after 10 minutes, the network is gone: "Zjišťuji…", no longer live
        transport.fail("ps-stream1/", URLError(.timedOut))
        clock.now = clock.now.addingTimeInterval(600)
        task = Task { await monitor.watch([PSPStreams.all[0]]) }
        for _ in 0..<200 where transport.requests.count < 2 { try await Task.sleep(for: .milliseconds(10)) }
        task.cancel()
        await task.value
        XCTAssertEqual(monitor.state(of: "psp:stream1"), .unknown)
    }

    @MainActor
    func testCheckRightBeforePlaying() async throws {
        let transport = FakeTransport()
        transport.on("ps-stream1/", HTTPResponse(status: 404, body: Data()))
        let clock = MutableClock(utc("2026-10-24T10:00:00Z"))
        let monitor = PSPLiveMonitor(transport: transport, clock: { clock.now })
        let stream = PSPStreams.all[0]

        // never checked: one GET of the playlist
        let first = await monitor.stateBeforePlaying(stream)
        XCTAssertEqual(first, .off)
        XCTAssertEqual(transport.requests.count, 1)
        // a check younger than 60 s is reused
        clock.now = clock.now.addingTimeInterval(59)
        let reused = await monitor.stateBeforePlaying(stream)
        XCTAssertEqual(reused, .off)
        XCTAssertEqual(transport.requests.count, 1)
        // older: checked again, and the broadcast has started meanwhile
        transport.on("ps-stream1/", HTTPResponse(status: 200, body: Data("#EXTM3U\n#EXTINF:6,\na.ts\n".utf8)))
        clock.now = clock.now.addingTimeInterval(2)
        let again = await monitor.stateBeforePlaying(stream)
        XCTAssertEqual(again, .live)
        XCTAssertEqual(transport.requests.count, 2)
        XCTAssertEqual(transport.requests.map { $0.url! }, [stream.url, stream.url])
    }
}
