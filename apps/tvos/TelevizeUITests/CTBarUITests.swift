import XCTest

/// "ČT player bar" (spec/apps.md, T62): ČT start-over plays with the timeline bar and keys, pressed through XCUIRemote.
/// Opt-in: plays the airing programme of ČT1 from its start (`-play startover:CH_1`) through the real ČT API from the
/// simulator, like the app does (`TEST_RUNNER_CT_BAR=1`); `TEST_RUNNER_CT_BAR_SHOTS=<dir>` saves a screenshot per step.
///
///     TEST_RUNNER_CT_BAR=1 xcodebuild test -scheme Televize -destination 'platform=tvOS Simulator,name=Apple TV' \
///       -only-testing:TelevizeUITests/CTBarUITests
@MainActor
final class CTBarUITests: XCTestCase {
    private var app: XCUIApplication!
    private var remote: XCUIRemote { .shared }
    private var shots: String?
    private let env = ProcessInfo.processInfo.environment

    override func setUpWithError() throws {
        guard env["CT_BAR"] != nil else { throw XCTSkip("set TEST_RUNNER_CT_BAR (plays ČT1 start-over)") }
        shots = env["CT_BAR_SHOTS"]
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = ["-play", "startover:CH_1"]
        app.launch()
    }

    private var title: XCUIElement { app.staticTexts["timeline.title"] }
    private var clock: XCUIElement { app.staticTexts["timeline.clock"] }
    private var trick: XCUIElement { app.staticTexts["timeline.trick"] }

    /// Seconds of the playhead label "Dnes 21:14:30" (since the day's start; the test stays within today).
    private var clockSeconds: Int? {
        guard clock.exists, let time = clock.label.split(separator: " ").last else { return nil }
        let parts = time.split(separator: ":").compactMap { Int($0) }
        return parts.count == 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : nil
    }

    private func shot(_ name: String) {
        guard let shots else { return }
        try? XCUIScreen.main.screenshot().pngRepresentation.write(to: URL(fileURLWithPath: "\(shots)/\(name).png"))
    }

    /// Clock seconds per second over about `seconds`, timed from the reads themselves.
    private func rate(over seconds: Double) throws -> Double {
        let first = try XCTUnwrap(clockSeconds)
        let began = Date()
        usleep(useconds_t(seconds * 1_000_000))
        let second = try XCTUnwrap(clockSeconds)
        return Double(second - first) / Date().timeIntervalSince(began)
    }

    private func waitForLabel(_ element: XCUIElement, _ label: String, timeout: TimeInterval = 3) -> Bool {
        let predicate = NSPredicate(format: "label == %@", label)
        return XCTWaiter().wait(for: [expectation(for: predicate, evaluatedWith: element)], timeout: timeout) == .completed
    }

    func testStartOverBarAndRewind() throws {
        XCTAssertTrue(title.waitForExistence(timeout: 40), "start-over shows the timeline bar")
        sleep(2)
        shot("ct1-start")
        // A click = −10 s (exact), no movement.
        let beforeClick = try XCTUnwrap(clockSeconds)
        remote.press(.left)
        sleep(1)
        XCTAssertFalse(trick.exists, "a click does not start moving")
        let afterClick = try XCTUnwrap(clockSeconds)
        XCTAssertLessThanOrEqual(afterClick - beforeClick, -7, "← = −10 s (plus the second played)")
        // Held ←: rewind at speed 1, then clicks raise it to 2, 3, 4, 8 and 16.
        remote.press(.left, forDuration: 1)
        XCTAssertTrue(trick.waitForExistence(timeout: 2), "rewind shows its sign")
        XCTAssertEqual(trick.label, "◀◀")
        shot("ct2-rewind-1")
        let one = try rate(over: 1)
        print("T62 ČT speed 1: \(Int(one)) s per second")
        remote.press(.left)
        XCTAssertTrue(waitForLabel(trick, "◀◀ 2"))
        remote.press(.left)
        XCTAssertTrue(waitForLabel(trick, "◀◀ 3"))
        remote.press(.left)
        XCTAssertTrue(waitForLabel(trick, "◀◀ 4"))
        let four = try rate(over: 1)
        print("T62 ČT speed 4: \(Int(four)) s per second")
        shot("ct3-rewind-4")
        remote.press(.left)
        XCTAssertTrue(waitForLabel(trick, "◀◀ 8"), "8 after 4")
        shot("ct4-rewind-8")
        remote.press(.left)
        XCTAssertTrue(waitForLabel(trick, "◀◀ 16"), "16 after 8")
        shot("ct5-rewind-16")
        // OK: stop and play there.
        remote.press(.select)
        XCTAssertTrue(waitForGone(trick, timeout: 3), "OK stops it")
        let stopped = try XCTUnwrap(clockSeconds)
        sleep(1)  // the bar hides 5 s after the stop (XCUI reads take a second at times)
        let playing = try XCTUnwrap(clockSeconds)
        shot("ct6-playing-there")
        XCTAssertLessThan(one, -40, "speed 1 ≈ 60 s per second back")
        XCTAssertLessThan(four, one * 2, "speed 4 well faster than 1")
        XCTAssertGreaterThanOrEqual(playing - stopped, 0, "plays from where it stopped")
        XCTAssertLessThan(abs(playing - stopped), 8, "no jump after the stop")
        // Fast-forward at 16 reaches the live-edge limit (now − 10 s) and stops there, playing.
        remote.press(.right, forDuration: 1)
        XCTAssertTrue(waitForLabel(trick, "▶▶"))
        for _ in 0..<5 { remote.press(.right) }
        XCTAssertTrue(waitForLabel(trick, "▶▶ 16"))
        XCTAssertTrue(waitForGone(trick, timeout: 20), "stops at the limit")
        sleep(2)
        shot("ct7-live-limit")
        // …at the stream's live edge, which lags the wall clock: 184–205 s by its PROGRAM-DATE-TIME on 2026-11-14 (T62;
        // before the fix it stopped 18 min early, a seek past the seekable end never finished).
        var prague = Calendar(identifier: .gregorian)
        prague.timeZone = TimeZone(identifier: "Europe/Prague")!
        let parts = prague.dateComponents([.hour, .minute, .second], from: Date())
        let nowSeconds = parts.hour! * 3600 + parts.minute! * 60 + parts.second!
        let atEdge = try XCTUnwrap(clockSeconds)
        print("T62 ČT live limit: \(nowSeconds - atEdge) s behind now")
        XCTAssertLessThan(nowSeconds - atEdge, 300, "stopped at the live edge, not before it")
        XCTAssertGreaterThan(nowSeconds - atEdge, 5, "not past the live edge")
        // Long OK: audio (and subtitles when the stream has them).
        remote.press(.select, forDuration: 1.2)
        XCTAssertTrue(app.alerts.firstMatch.waitForExistence(timeout: 3) || app.sheets.firstMatch.exists, "track list")
        shot("ct8-tracks")
        remote.press(.menu)
        sleep(1)
        remote.press(.menu)
        let hidden = NSPredicate(format: "exists == false")
        wait(for: [expectation(for: hidden, evaluatedWith: title)], timeout: 5)
    }

    private func waitForGone(_ element: XCUIElement, timeout: TimeInterval) -> Bool {
        let predicate = NSPredicate(format: "exists == false")
        return XCTWaiter().wait(for: [expectation(for: predicate, evaluatedWith: element)], timeout: timeout) == .completed
    }
}
