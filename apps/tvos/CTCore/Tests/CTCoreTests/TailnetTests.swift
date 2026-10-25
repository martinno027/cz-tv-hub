import Foundation
import XCTest
@testable import CTCore

/// Golden cases `spec/fixtures/tailnet_cases.json` ("Tailscale v aplikaci").
final class TailnetTests: XCTestCase {
    private var fixture: [String: Any] { get throws { try Fixture.json("tailnet_cases") } }

    func testGoldenTargetCases() throws {
        let cases = try fixture["target"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 10)
        for item in cases {
            let address = item["address"] as! String
            XCTAssertEqual(TailnetRules.target(address: address), item["target"] as? String, address)
        }
    }

    func testGoldenStatusCases() throws {
        let cases = try fixture["status"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 14)
        for item in cases {
            let name = item["name"] as! String
            var info: TailnetInfo?
            if let raw = item["info"] as? [String: Any] {
                let json = String(data: try JSONSerialization.data(withJSONObject: raw), encoding: .utf8)!
                info = try XCTUnwrap(TailnetInfo.parse(json), name)
            }
            let status = TailnetRules.status(enabled: item["enabled"] as! Bool, info: info, error: item["error"] as? String)
            XCTAssertEqual(status.line, item["line"] as! String, name)
            XCTAssertEqual(status.qr, item["qr"] as! Bool, name)
            XCTAssertEqual(status.logout, item["logout"] as! Bool, name)
            XCTAssertEqual(status.usesBridge, item["usesBridge"] as! Bool, name)
        }
    }

    func testConnectionLine() {
        XCTAssertEqual(TailnetRules.connectionLine(TailnetGatewayPath(direct: true, relay: "fra", latencyMs: 32)),
                       " · k bráně přímo, 32 ms")
        XCTAssertEqual(TailnetRules.connectionLine(TailnetGatewayPath(direct: true, relay: "fra")), " · k bráně přímo")
        XCTAssertEqual(TailnetRules.connectionLine(TailnetGatewayPath(direct: false, relay: "fra", latencyMs: 85)),
                       " · přes relé fra, 85 ms")
        // neither direct nor through a known relay (a peer relay, no path yet): nothing, as when unknown
        XCTAssertEqual(TailnetRules.connectionLine(TailnetGatewayPath(direct: false, relay: "", latencyMs: 12)), "")
        XCTAssertEqual(TailnetRules.connectionLine(nil), "")
        // only while running
        let connecting = TailnetInfo(state: "Starting", selfName: "televize-appletv",
                                     gateway: TailnetGatewayPath(direct: true, latencyMs: 5))
        XCTAssertEqual(TailnetRules.status(enabled: true, info: connecting, error: nil).line, TailnetRules.connectingLine)
    }

    func testGatewayPathOfAnOlderOrOddBridge() throws {
        XCTAssertNil(try XCTUnwrap(TailnetInfo.parse(#"{"state":"Running","self":"t"}"#)).gateway)
        XCTAssertNil(try XCTUnwrap(TailnetInfo.parse(#"{"state":"Running","gateway":"fra"}"#)).gateway)
        XCTAssertEqual(try XCTUnwrap(TailnetInfo.parse(#"{"state":"Running","gateway":{"relay":"fra"}}"#)).gateway,
                       TailnetGatewayPath(direct: false, relay: "fra", latencyMs: nil))
    }

    func testBridgeFailureIsOnlyTheBridgesOwn502() {
        let own = Data(#"{"error": "Brána není přes Tailscale dostupná."}"#.utf8)
        XCTAssertTrue(TailnetRules.isBridgeFailure(status: 502, body: own))
        // the gateway's own 502 (a stream it could not start) and other answers are the gateway speaking
        XCTAssertFalse(TailnetRules.isBridgeFailure(status: 502, body: Data(#"{"error": "Tuner neodpovídá"}"#.utf8)))
        XCTAssertFalse(TailnetRules.isBridgeFailure(status: 502, body: Data("bad gateway\n".utf8)))
        XCTAssertFalse(TailnetRules.isBridgeFailure(status: 503, body: own))
    }

    func testBridgeWatchTransport() async throws {
        let failures = Counter()
        let fake = FakeTransport()
        fake.on("/dvb/channels", HTTPResponse(status: 502, body: Data(#"{"error": "Brána není přes Tailscale dostupná."}"#.utf8)))
        fake.on("/dvb/epg", HTTPResponse(status: 502, body: Data(#"{"error": "Tuner neodpovídá"}"#.utf8)))
        fake.on("/health", HTTPResponse(status: 200, body: Data("ok\n".utf8)))
        fake.fail("/dvb/status", URLError(.timedOut))
        fake.fail("/dvb/profile", URLError(.cancelled))
        let transport = BridgeWatchTransport(fake) { failures.add() }
        func get(_ path: String) async -> Int? {
            try? await transport.send(URLRequest(url: URL(string: "http://127.0.0.1:5000\(path)")!)).status
        }
        let channels = await get("/dvb/channels")
        XCTAssertEqual(channels, 502, "the answer is passed on")
        XCTAssertEqual(failures.value, 1)
        _ = await get("/dvb/epg")
        _ = await get("/health")
        XCTAssertEqual(failures.value, 1)
        let status = await get("/dvb/status")
        XCTAssertNil(status)
        XCTAssertEqual(failures.value, 2, "no answer from the bridge")
        _ = await get("/dvb/profile")
        XCTAssertEqual(failures.value, 2, "a cancelled request is not a failure")
    }

    func testMissingAddressLine() {
        let status = TailnetRules.status(enabled: true, info: nil, error: TailnetRules.missingAddress)
        XCTAssertEqual(status.line, "Tailscale se nepodařilo spustit: chybí adresa brány.")
        XCTAssertFalse(status.usesBridge)
        XCTAssertFalse(status.logout)
    }

    func testLogoutFollowsTheIdentity() {
        let named = TailnetInfo(state: "NeedsLogin", selfName: "televize-appletv")
        XCTAssertTrue(TailnetRules.status(enabled: true, info: named, error: nil).logout)
        XCTAssertFalse(TailnetRules.status(enabled: true, info: TailnetInfo(state: "Running"), error: nil).logout)
    }

    func testInfoOfABridgeWithoutNode() throws {
        let info = try XCTUnwrap(TailnetInfo.parse(#"{"state":"Stopped"}"#))
        XCTAssertEqual(info, TailnetInfo(state: "Stopped"))
        XCTAssertNil(TailnetInfo.parse("not json"))
    }

    func testQRModules() throws {
        let modules = try XCTUnwrap(TailnetRules.qrModules("101\n010\n001"))
        XCTAssertEqual(modules, [[true, false, true], [false, true, false], [false, false, true]])
        XCTAssertNil(TailnetRules.qrModules(""))
        XCTAssertNil(TailnetRules.qrModules("10\n1"))
    }

    func testGatewayConfigStillParsesTheSameWay() {
        XCTAssertEqual(GatewayConfig(address: " pi.tail1234.ts.net ", key: " k ")?.baseURL.absoluteString,
                       "http://pi.tail1234.ts.net:8098")
        XCTAssertNil(GatewayConfig(address: "raspberrypi", key: " "))
        XCTAssertNil(GatewayConfig(address: "ftp://raspberrypi", key: "k"))
    }
}

private final class Counter: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0
    var value: Int { lock.withLock { count } }
    func add() { lock.withLock { count += 1 } }
}

/// "Back in front (T18)": when the bridge is checked and what follows.
@MainActor
final class TailnetRecoveryTests: XCTestCase {
    // XCTest makes a new instance for every test.
    private let clock = MutableClock(Date(timeIntervalSince1970: 1_000_000))
    private var log: [String] = []
    private lazy var recovery: TailnetRecovery = {
        let clock = clock
        return TailnetRecovery(clock: { clock.now }, sleep: { clock.now += $0 })
    }()

    /// `checks`: the answers of the checks in turn; `runningAfter`: seconds after the restart until `Running`.
    private func steps(checks: [Bool], restartWorks: Bool = true, runningAfter: TimeInterval? = 0) -> TailnetRecovery.Steps {
        var checks = checks
        var restartedAt: Date?
        return TailnetRecovery.Steps(
            check: { [self] in
                log.append("check")
                XCTAssertTrue(recovery.checking)
                return checks.isEmpty ? false : checks.removeFirst()
            },
            restart: { [self] in
                log.append("restart")
                restartedAt = clock.now
                return restartWorks
            },
            isRunning: { [self] in
                log.append("status")
                guard let restartedAt, let runningAfter else { return false }
                return clock.now.timeIntervalSince(restartedAt) >= runningAfter
            },
            reload: { [self] in log.append("reload") }
        )
    }

    func testAnsweredCheckDoesNothingElse() async {
        let outcome = await recovery.run(steps(checks: [true]))
        XCTAssertEqual(outcome, .healthy)
        XCTAssertEqual(log, ["check"])
        XCTAssertFalse(recovery.checking)
    }

    func testUnansweredCheckRestartsPollsEveryTwoSecondsChecksAgainAndReloads() async {
        let start = clock.now
        let outcome = await recovery.run(steps(checks: [false, true], runningAfter: 5))
        XCTAssertEqual(outcome, .recovered)
        // statuses at 0, 2, 4, 6 s: Running from 5 s on
        XCTAssertEqual(log, ["check", "restart", "status", "status", "status", "status", "check", "reload"])
        XCTAssertEqual(clock.now.timeIntervalSince(start), 6)
        XCTAssertFalse(recovery.checking)
    }

    func testWaitsAtMostTwentySecondsForRunning() async {
        let start = clock.now
        let outcome = await recovery.run(steps(checks: [false, false], runningAfter: nil))
        XCTAssertEqual(outcome, .failed)
        XCTAssertEqual(clock.now.timeIntervalSince(start), TailnetRecovery.runningWait)
        XCTAssertEqual(log.filter { $0 == "status" }.count, 11)  // 0, 2, … 20 s
        XCTAssertEqual(log.suffix(2), ["check", "reload"], "checked again and reloaded, so the screens show the error")
    }

    func testFailedRestartStillReloads() async {
        let outcome = await recovery.run(steps(checks: [false], restartWorks: false))
        XCTAssertEqual(outcome, .failed)
        XCTAssertEqual(log, ["check", "restart", "reload"])
    }

    func testOneCheckAtATime() async {
        var inner: TailnetRecovery.Outcome?
        var steps = steps(checks: [true])
        let check = steps.check
        steps.check = { [self] in
            inner = await recovery.run(self.steps(checks: [false]))
            return await check()
        }
        let outcome = await recovery.run(steps)
        XCTAssertEqual(inner, .skipped)
        XCTAssertEqual(outcome, .healthy)
        XCTAssertEqual(log, ["check"])
    }

    func testThirtySecondsQuietAfterARestart() async {
        _ = await recovery.run(steps(checks: [false, true]))
        log = []
        clock.now += TailnetRecovery.quietAfterStart - 1
        let early = await recovery.run(steps(checks: [false]))
        XCTAssertEqual(early, .skipped)
        XCTAssertEqual(log, [])
        clock.now += 1
        let later = await recovery.run(steps(checks: [true]))
        XCTAssertEqual(later, .healthy)
    }

    func testQuietAfterANodeStart() async {
        recovery.nodeStarted()
        clock.now += 29
        let early = await recovery.run(steps(checks: [false]))
        XCTAssertEqual(early, .skipped)
        clock.now += 1
        let later = await recovery.run(steps(checks: [true]))
        XCTAssertEqual(later, .healthy)
    }
}
