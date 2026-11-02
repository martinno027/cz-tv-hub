import XCTest
@testable import CTCore

/// Watch reports (spec/dvb-gateway.md "Watch log (T53)", spec/apps.md "Watch reports (T53)").
@MainActor
final class WatchTests: XCTestCase {
    private let gateway = GatewayConfig(baseURL: URL(string: "http://pi.local:8098")!, key: "k")
    private var transport: FakeTransport!
    private var clock: MutableClock!
    private var token: String?
    private var refreshes = 0
    /// What the next status read says (nil = the read fails).
    private var statusSays: Bool? = true
    private var sessions: Counter!
    private var reporter: WatchReporter!

    override func setUp() async throws {
        transport = FakeTransport()
        transport.on("pi.local", HTTPResponse(status: 204, body: Data()))
        clock = MutableClock(utc("2026-11-02T18:00:00Z"))
        token = nil
        refreshes = 0
        statusSays = true
        sessions = Counter()
        reporter = makeReporter()
    }

    private func makeReporter(target: GatewayConfig? = nil, transport custom: HTTPTransport? = nil,
                              sleep: (@Sendable (TimeInterval) async -> Void)? = nil) -> WatchReporter {
        let config = target ?? gateway
        let transport: HTTPTransport = custom ?? self.transport
        let clock = clock!
        return WatchReporter(
            clock: { clock.now },
            target: { [unowned self] in
                WatchTarget(client: DVBClient(config: config, transport: transport, clientHeader: "tvos/1.0 (7)"),
                            profileToken: self.token)
            },
            refreshStatus: { [unowned self] in
                self.refreshes += 1
                if let says = self.statusSays { self.reporter.setWatchLog(says) }
            },
            newSession: { [sessions] in "session0000000000\(sessions!.next())" },
            sleep: sleep ?? { _ in try? await Task.sleep(for: .seconds(3600)) }
        )
    }

    private var bodies: [[String: String]] {
        transport.requests.map { request in
            (try! JSONSerialization.jsonObject(with: request.httpBody!) as! [String: Any]).mapValues { "\($0)" }
        }
    }

    private var events: [String] { bodies.map { "\($0["event"]!) \($0["session"]!)" } }

    private let ct1 = WatchItem.ct(channelID: "CH_1", playing: .live, idec: nil, title: "Události")
    private let ct2 = WatchItem.ct(channelID: "CH_2", playing: .live, idec: nil, title: "Film")

    // MARK: shape

    func testStartCarriesEveryFieldBeatAndStopOnlyEventAndSession() throws {
        let item = WatchItem.ct(channelID: "CH_1", playing: .vod, idec: "21754215148", title: "Kolja")
        let start = try JSONSerialization.jsonObject(with: WatchRules.body(.start, session: "s", item: item)) as! [String: String]
        XCTAssertEqual(start, ["event": "start", "session": "s", "source": "ct", "mode": "archive", "channel": "CH_1",
                               "title": "Kolja", "idec": "21754215148"])
        for event in [WatchRules.Event.beat, .stop] {
            let body = try JSONSerialization.jsonObject(with: WatchRules.body(event, session: "s", item: item)) as! [String: String]
            XCTAssertEqual(body, ["event": event.rawValue, "session": "s"])
        }
        let recording = WatchItem.dvb(.recording, recordingID: "r1", channelID: "ch-nova", title: "Zprávy")
        let dvb = try JSONSerialization.jsonObject(with: WatchRules.body(.start, session: "s", item: recording)) as! [String: String]
        XCTAssertEqual(dvb, ["event": "start", "session": "s", "source": "dvb", "mode": "recording", "channel": "ch-nova",
                             "title": "Zprávy", "recording": "r1"])
        XCTAssertLessThanOrEqual(WatchRules.body(.start, session: String(repeating: "a", count: 64),
                                                 item: WatchItem(source: .ct, mode: .live, channel: "CH_1",
                                                                 title: String(repeating: "ž", count: 500))).count, 2048)
    }

    func testItemsOfEverySource() {
        let now = utc("2026-11-02T18:00:00Z")
        XCTAssertEqual(WatchItem.ct(channelID: "CH_24", playing: .live, idec: "x", title: "a"),
                       WatchItem(source: .ct, mode: .live, channel: "CH_24", title: "a"), "idec only for archive")
        // anything from the 3-hour buffer is `startover` (lead decision 2026-11-02: no `timeshift`)
        let buffer = WatchItem.ct(channelID: "CH_1", playing: .startOver(seekDate: now, offset: 0), idec: "1", title: "a")
        XCTAssertEqual(buffer.mode, .startover)
        XCTAssertNil(buffer.idec)
        XCTAssertEqual(WatchItem.ct(channelID: "CH_1", playing: .vod, idec: "1", title: "a").mode, .archive)
        XCTAssertEqual(WatchItem.ct(channelID: nil, playing: .vod, idec: "9", title: nil),
                       WatchItem(source: .ct, mode: .archive, channel: "", idec: "9"))
        let stream = PSPStreams.all[0]
        XCTAssertEqual(WatchItem.psp(stream), WatchItem(source: .psp, mode: .live, channel: stream.id, title: stream.name))
        XCTAssertEqual(WatchItem.dvbLive(channelID: "c", title: nil), WatchItem(source: .dvb, mode: .live, channel: "c"))
        XCTAssertEqual(WatchItem.dvb(.timeline, recordingID: "b1", channelID: "c", title: "t").recording, "b1")
    }

    func testModesAreTheGatewaysList() {
        let all: [WatchItem.Mode] = [.live, .startover, .archive, .recording, .timeline]
        XCTAssertEqual(all.map(\.rawValue), ["live", "startover", "archive", "recording", "timeline"])
        XCTAssertNil(WatchItem.Mode(rawValue: "timeshift"))
    }

    func testTitleIsCleanedAndCut() {
        XCTAssertEqual(WatchItem(source: .ct, mode: .live, channel: "CH_1", title: " A\u{0}b\nc ").title, "Abc")
        XCTAssertNil(WatchItem(source: .ct, mode: .live, channel: "CH_1", title: " \n").title)
        XCTAssertEqual(WatchItem(source: .ct, mode: .live, channel: "CH_1", title: String(repeating: "é", count: 300)).title?.count, 200)
    }

    func testSessionIDsFollowTheGatewaysRuleAndDiffer() {
        let ids = (0..<50).map { _ in WatchRules.newSessionID() }
        XCTAssertEqual(Set(ids).count, 50)
        for id in ids { XCTAssertTrue(WatchRules.isValidSession(id), id) }
        XCTAssertFalse(WatchRules.isValidSession("short"))
        XCTAssertFalse(WatchRules.isValidSession(String(repeating: "a", count: 65)))
        XCTAssertFalse(WatchRules.isValidSession("aaaaaaaaaaaaaaaa/"))
    }

    func testStatusWatchLogMissingIsOff() throws {
        let off = Data(#"{"enabled": true, "retentionDays": 3, "tuners": []}"#.utf8)
        XCTAssertFalse(try DVBParser.status(status: 200, body: off).watchLog)
        let on = Data(#"{"enabled": true, "watchLog": true}"#.utf8)
        XCTAssertTrue(try DVBParser.status(status: 200, body: on).watchLog)
        let wrongType = Data(#"{"enabled": true, "watchLog": "yes"}"#.utf8)
        XCTAssertFalse(try DVBParser.status(status: 200, body: wrongType).watchLog)
    }

    // MARK: when

    func testStartBeatEveryMinuteStop() async {
        reporter.setWatchLog(true)
        let handle = reporter.start(WatchStart(ct1))
        await reporter.settle()
        XCTAssertEqual(events, ["start session00000000001"])
        XCTAssertEqual(bodies[0]["channel"], "CH_1")
        XCTAssertEqual(bodies[0]["title"], "Události")
        clock.now += 30
        reporter.tick()
        await reporter.settle()
        XCTAssertEqual(transport.requests.count, 1, "no beat before 60 s")
        clock.now += 30
        reporter.tick()
        clock.now += 60
        reporter.tick()
        reporter.stop(handle)
        await reporter.settle()
        XCTAssertEqual(events, ["start session00000000001", "beat session00000000001", "beat session00000000001",
                                "stop session00000000001"])
        reporter.stop(handle)
        reporter.tick()
        await reporter.settle()
        XCTAssertEqual(transport.requests.count, 4, "a closed player reports nothing more")
    }

    func testNothingWhileTheLogIsOff() async {
        statusSays = false
        let handle = reporter.start(WatchStart(ct1))
        await reporter.settle()
        clock.now += 61
        reporter.tick()
        reporter.stop(handle)
        await reporter.settle()
        XCTAssertEqual(refreshes, 1, "the status was asked")
        XCTAssertTrue(transport.requests.isEmpty)
    }

    /// A failed read keeps the last known answer (spec/dvb-gateway.md "Watch log (T53)"), initially off.
    func testAFailedStatusReadKeepsTheLastAnswer() async {
        statusSays = nil
        let first = reporter.start(WatchStart(ct1))
        await reporter.settle()
        XCTAssertTrue(transport.requests.isEmpty, "never read: off")
        reporter.stop(first)
        reporter.setWatchLog(true)
        clock.now += WatchRules.statusMaxAge
        reporter.start(WatchStart(ct2))  // stale: read again, the read fails
        await reporter.settle()
        XCTAssertEqual(refreshes, 2)
        XCTAssertEqual(events, ["start session00000000001"], "still on after a failed read")
        clock.now += WatchRules.statusMaxAge
        reporter.tick()  // stale while beating: read again, fails again
        await reporter.settle()
        XCTAssertEqual(refreshes, 3)
        XCTAssertTrue(reporter.watchLog)
        XCTAssertEqual(events, ["start session00000000001", "beat session00000000001"])
    }

    func testTheLogTurnedOnReachesAnOpenPlayerAndOffEndsReporting() async {
        statusSays = false
        reporter.start(WatchStart(ct1))
        await reporter.settle()
        XCTAssertTrue(transport.requests.isEmpty)
        statusSays = true
        clock.now += 60
        reporter.tick()  // status still fresh: not asked again
        await reporter.settle()
        XCTAssertEqual(refreshes, 1)
        clock.now += WatchRules.statusMaxAge
        reporter.tick()
        await reporter.settle()
        XCTAssertEqual(refreshes, 2)
        XCTAssertEqual(events, ["start session00000000001"])
        reporter.setWatchLog(false)
        clock.now += 61
        reporter.tick()
        await reporter.settle()
        XCTAssertEqual(events, ["start session00000000001"], "off: no beat (the gateway drops its sessions)")
    }

    func testAStaleStatusIsReadAgainWhileBeating() async {
        reporter.start(WatchStart(ct1))
        await reporter.settle()
        XCTAssertEqual(refreshes, 1)
        for _ in 0..<5 {
            clock.now += 60
            reporter.tick()
        }
        await reporter.settle()
        XCTAssertEqual(refreshes, 2, "once after 5 minutes, not on every beat")
    }

    func testZapStopsTheOldSessionBeforeTheNewStart() async {
        reporter.setWatchLog(true)
        reporter.start(WatchStart(ct1))
        await reporter.settle()
        let second = reporter.start(WatchStart(ct2))
        await reporter.settle()
        XCTAssertEqual(events, ["start session00000000001", "stop session00000000001", "start session00000000002"])
        XCTAssertEqual(bodies[2]["channel"], "CH_2")
        reporter.stop(second)
        await reporter.settle()
        XCTAssertEqual(events.last, "stop session00000000002")
    }

    func testAnOlderPlayersStopDoesNotEndTheNewOne() async {
        reporter.setWatchLog(true)
        let first = reporter.start(WatchStart(ct1))
        reporter.start(WatchStart(ct2))
        reporter.stop(first)
        await reporter.settle()
        XCTAssertEqual(events, ["start session00000000001"])
        XCTAssertEqual(bodies.first?["channel"], "CH_2", "the first never got its start out")
    }

    func testBackgroundStopsAndFrontStartsANewSession() async {
        reporter.setWatchLog(true)
        reporter.start(WatchStart(ct1))
        await reporter.settle()
        reporter.background()
        reporter.background()
        XCTAssertFalse(reporter.isBeating)
        clock.now += 120
        reporter.tick()
        await reporter.settle()
        XCTAssertEqual(events, ["start session00000000001", "stop session00000000001"])
        reporter.foreground()
        await reporter.settle()
        XCTAssertEqual(events, ["start session00000000001", "stop session00000000001", "start session00000000002"])
        XCTAssertTrue(reporter.isBeating)
    }

    func testBackgroundWithoutAPlayerSendsNothing() async {
        reporter.setWatchLog(true)
        reporter.background()
        reporter.foreground()
        await reporter.settle()
        XCTAssertTrue(transport.requests.isEmpty)
        XCTAssertFalse(reporter.isBeating)
    }

    func testTheLiveTitleIsLookedUpAfterTheStart() async {
        reporter.setWatchLog(true)
        reporter.start(WatchStart(WatchItem.dvbLive(channelID: "nova", title: nil), title: { "Ulice" }))
        await reporter.settle()
        XCTAssertEqual(bodies.first?["title"], "Ulice")
        XCTAssertEqual(bodies.first?["mode"], "live")
        XCTAssertEqual(bodies.first?["source"], "dvb")
    }

    func testAnotherGatewayStartsAfresh() async {
        reporter.setWatchLog(true)
        reporter.start(WatchStart(ct1))
        await reporter.settle()
        reporter.gatewayChanged()
        await reporter.settle()
        XCTAssertEqual(refreshes, 1, "the new gateway is asked")
        XCTAssertEqual(events, ["start session00000000001", "start session00000000002"],
                       "the old gateway's session is left to its timeout")
    }

    func testTheBeatLoopBeatsEveryMinute() async {
        let clock = clock!
        let waits = Waits()
        reporter = makeReporter(sleep: { seconds in
            if await waits.add(seconds) > 3 { try? await Task.sleep(for: .seconds(3600)); return }
            clock.now += seconds
            await Task.yield()
        })
        reporter.setWatchLog(true)
        reporter.start(WatchStart(ct1))
        for _ in 0..<100 where transport.requests.count < 4 { await Task.yield() }
        await reporter.settle()
        XCTAssertEqual(events.map { $0.split(separator: " ")[0] }, ["start", "beat", "beat", "beat"])
        let all = await waits.all
        XCTAssertEqual(Array(all.prefix(3)), [60, 60, 60])
    }

    // MARK: how

    func testRequestShapeHeadersAndTimeout() async {
        token = "profile-token"
        reporter.setWatchLog(true)
        reporter.start(WatchStart(ct1))
        await reporter.settle()
        let request = transport.requests[0]
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.url?.absoluteString, "http://pi.local:8098/dvb/watch")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer k")
        XCTAssertNil(request.value(forHTTPHeaderField: "Cookie"), "the key only as bearer")
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-Cztvhub-Profile"), "profile-token")
        XCTAssertEqual(request.value(forHTTPHeaderField: ClientHeader.field), "tvos/1.0 (7)")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertEqual(request.timeoutInterval, 5)
        token = nil
        clock.now += 60
        reporter.tick()
        await reporter.settle()
        XCTAssertNil(transport.requests[1].value(forHTTPHeaderField: "X-Cztvhub-Profile"), "signed out: no token")
    }

    func testErrorsAreIgnoredAndTheNextReportsStillGo() async {
        let failing = FakeTransport()
        failing.fail("pi.local", URLError(.timedOut))
        reporter = makeReporter(transport: failing)
        reporter.setWatchLog(true)
        let handle = reporter.start(WatchStart(ct1))
        await reporter.settle()
        clock.now += 60
        reporter.tick()
        await reporter.settle()
        reporter.stop(handle)
        await reporter.settle()
        XCTAssertEqual(failing.requests.count, 3)
    }

    func testAHungGatewayDoesNotHoldThePlayerAndTheQueueIsBounded() async {
        let hung = HungTransport()
        reporter = makeReporter(transport: hung)
        reporter.setWatchLog(true)
        reporter.start(WatchStart(ct1))
        for _ in 0..<20 {  // every call returns at once
            await Task.yield()
            clock.now += 60
            reporter.tick()
        }
        for _ in 0..<50 { await Task.yield() }
        XCTAssertEqual(hung.count, 1, "in order, one at a time")
        XCTAssertEqual(reporter.pendingForTests, WatchRules.maxPending, "the rest dropped")
    }

    /// Every source's reports go to the gateway only, never to ČT, the Chamber or TMDb.
    func testOnlyEverToTheGateway() async {
        reporter.setWatchLog(true)
        let now = Date()
        let items: [WatchItem] = [
            ct1,
            .ct(channelID: "CH_1", playing: .startOver(seekDate: now, offset: 0), idec: "1", title: "a"),
            .ct(channelID: "CH_1", playing: .vod, idec: "1", title: "a"),
            .psp(PSPStreams.all[0]),
            .dvbLive(channelID: "c", title: "t"),
            .dvb(.recording, recordingID: "r", channelID: "c", title: "t"),
            .dvb(.timeline, recordingID: "b1", channelID: "c", title: "t"),
        ]
        var handle = 0
        for item in items {
            handle = reporter.start(WatchStart(item))
            await reporter.settle()
        }
        reporter.stop(handle)
        await reporter.settle()
        XCTAssertEqual(transport.requests.count, items.count * 2)
        XCTAssertEqual(Set(bodies.compactMap { $0["source"] }), ["ct", "psp", "dvb"])
        // channel ids as the gateway's catalogues know them: ČT `CH_*`, the Chamber's `stream1`…`stream5`, the
        // gateway's DVB id
        XCTAssertEqual(bodies.filter { $0["event"] == "start" }.map { $0["channel"]! },
                       ["CH_1", "CH_1", "CH_1", "stream1", "c", "c", "c"])
        XCTAssertEqual(PSPStreams.all.map { WatchItem.psp($0).channel }, ["stream1", "stream2", "stream3", "stream4", "stream5"])
        for request in transport.requests {
            XCTAssertEqual(request.url?.host(), "pi.local")
            XCTAssertEqual(request.url?.path, "/dvb/watch")
        }
    }

    func testNoGatewayNoReports() async {
        reporter = WatchReporter(clock: { [clock] in clock!.now }, target: { nil }, refreshStatus: {})
        reporter.setWatchLog(true)
        let handle = reporter.start(WatchStart(ct1))
        reporter.stop(handle)
        await reporter.settle()
        XCTAssertTrue(transport.requests.isEmpty)
    }
}

private final class Counter: @unchecked Sendable {
    private let lock = NSLock()
    private var value = 0
    func next() -> Int { lock.withLock { value += 1; return value } }
}

private actor Waits {
    private(set) var all: [TimeInterval] = []
    func add(_ seconds: TimeInterval) -> Int {
        all.append(seconds)
        return all.count
    }
}

/// Never answers.
private final class HungTransport: HTTPTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var _count = 0
    var count: Int { lock.withLock { _count } }

    func send(_ request: URLRequest) async throws -> HTTPResponse {
        lock.withLock { _count += 1 }
        try await Task.sleep(for: .seconds(3600))
        throw CancellationError()
    }
}
