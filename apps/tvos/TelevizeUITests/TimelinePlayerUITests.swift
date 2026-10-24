import XCTest

/// "Anténa timeline player" (spec/apps.md): the own bar and keys of a `b…` recording, pressed through XCUIRemote.
/// Opt-in: needs a gateway whose channel 1 has a finished `b…` programme today (`-play dvbrec:1`) with another
/// programme after it in the window. `TEST_RUNNER_TIMELINE_SHOTS=<dir>` also saves a screenshot per step;
/// `TEST_RUNNER_TIMELINE_BREAK=1` when that next programme has an ad break 30–60 s after its start.
///
///     TEST_RUNNER_TIMELINE_GATEWAY=127.0.0.1:58093 TEST_RUNNER_TIMELINE_KEY=dev-key-dev-key-dev-key-dev-key-0000 \
///       xcodebuild test -scheme Televize -destination 'platform=tvOS Simulator,name=Apple TV' \
///       -only-testing:TelevizeUITests/TimelinePlayerUITests
@MainActor
final class TimelinePlayerUITests: XCTestCase {
    private var app: XCUIApplication!
    private var remote: XCUIRemote { .shared }
    private var shots: String?
    private let env = ProcessInfo.processInfo.environment

    override func setUpWithError() throws {
        guard let gateway = env["TIMELINE_GATEWAY"], let key = env["TIMELINE_KEY"] else {
            throw XCTSkip("set TEST_RUNNER_TIMELINE_GATEWAY and TEST_RUNNER_TIMELINE_KEY")
        }
        shots = env["TIMELINE_SHOTS"]
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = ["-gatewayAddress", gateway, "-gatewayKey", key, "-play", "dvbrec:1"]
        app.launch()
    }

    private var title: XCUIElement { app.staticTexts["timeline.title"] }
    private var clock: XCUIElement { app.staticTexts["timeline.clock"] }

    /// Seconds of the day of the playhead label "Dnes 21:14:30".
    private var clockSeconds: Int? {
        guard clock.exists, let time = clock.label.split(separator: " ").last else { return nil }
        let parts = time.split(separator: ":").compactMap { Int($0) }
        return parts.count == 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : nil
    }

    private func shot(_ name: String) {
        guard let shots else { return }
        try? XCUIScreen.main.screenshot().pngRepresentation.write(to: URL(fileURLWithPath: "\(shots)/\(name).png"))
    }

    func testBarAndKeys() throws {
        XCTAssertTrue(title.waitForExistence(timeout: 30), "the bar is shown at the start")
        sleep(1)
        shot("1-start")
        // Hidden after 5 s while playing; the first arrow only shows it again.
        let hidden = NSPredicate(format: "exists == false")
        wait(for: [expectation(for: hidden, evaluatedWith: title)], timeout: 10)
        shot("2-hidden")
        // A short OK with the bar hidden only shows it: playback goes on.
        remote.press(.select)
        XCTAssertTrue(title.waitForExistence(timeout: 3))
        let shownAt = try XCTUnwrap(clockSeconds)
        sleep(2)
        XCTAssertGreaterThanOrEqual(try XCTUnwrap(clockSeconds) - shownAt, 1, "OK with the bar hidden does not pause")
        let before = try XCTUnwrap(clockSeconds)
        remote.press(.right)
        sleep(1)
        let after = try XCTUnwrap(clockSeconds)
        XCTAssertGreaterThanOrEqual(after - before, 10, "→ = +10 s")
        XCTAssertLessThanOrEqual(after - before, 13)
        shot("3-plus10")
        // ▼ = start of the next programme: the title changes.
        let first = title.label
        remote.press(.down)
        sleep(2)
        shot("4-down")
        XCTAssertNotEqual(title.label, first, "▼ goes to the next programme")
        // With stored breaks 30–60 s into that programme the button shows there; OK skips.
        if env["TIMELINE_BREAK"] != nil {
            for _ in 0..<3 { remote.press(.right); usleep(400_000) }
            let skip = app.descendants(matching: .any)["timeline.skip"]
            let shown = skip.waitForExistence(timeout: 3)
            shot("4b-skip")
            XCTAssertTrue(shown, "Přeskočit reklamu")
            let inBreak = try XCTUnwrap(clockSeconds)
            remote.press(.select)
            sleep(1)
            XCTAssertGreaterThan(try XCTUnwrap(clockSeconds), inBreak + 15, "OK skips the break")
            XCTAssertFalse(skip.exists)
            shot("4c-skipped")
        }
        remote.press(.up)
        sleep(2)
        shot("5-up")
        // Held ←: scrubs back by about 60 s per second, seeks on release.
        let beforeHold = try XCTUnwrap(clockSeconds)
        remote.press(.left, forDuration: 1.5)
        sleep(2)
        shot("6-held-left")
        let afterHold = try XCTUnwrap(clockSeconds)
        XCTAssertLessThan(afterHold, beforeHold - 30, "held ← scrubs back")
        // Play/pause keeps the bar on screen while paused.
        remote.press(.playPause)
        sleep(7)
        XCTAssertTrue(title.exists, "the bar stays while paused")
        shot("7-paused")
        remote.press(.playPause)
        // Long OK: the audio list.
        remote.press(.select, forDuration: 1.2)
        sleep(1)
        XCTAssertTrue(app.alerts.firstMatch.waitForExistence(timeout: 3) || app.sheets.firstMatch.exists, "audio list")
        shot("8-audio")
        remote.press(.menu)
        sleep(1)
        // Back closes the player.
        remote.press(.menu)
        wait(for: [expectation(for: hidden, evaluatedWith: title)], timeout: 5)
        shot("9-closed")
    }

    /// Long OK lists the audio renditions (Czech selected) and picking one keeps playing. Needs a programme
    /// recorded with several tracks; skipped otherwise (`TEST_RUNNER_TIMELINE_AUDIO=1`).
    func testAudioList() throws {
        guard env["TIMELINE_AUDIO"] != nil else { throw XCTSkip("set TEST_RUNNER_TIMELINE_AUDIO for a multi-track programme") }
        XCTAssertTrue(title.waitForExistence(timeout: 30))
        sleep(2)
        remote.press(.select, forDuration: 1.2)
        let english = app.buttons["Angličtina"].firstMatch
        XCTAssertTrue(english.waitForExistence(timeout: 3), "the list has every rendition")
        XCTAssertTrue(app.buttons["Čeština"].firstMatch.exists)
        shot("a1-audio-list")
        for _ in 0..<3 where !english.hasFocus {
            remote.press(.down)
            usleep(500_000)
        }
        remote.press(.select)
        sleep(2)
        XCTAssertFalse(english.exists, "the list closes")
        remote.press(.right)
        XCTAssertTrue(clock.waitForExistence(timeout: 3), "still playing with the bar")
        shot("a2-after")
    }

    /// A failed `previous` keeps the old window playing and shows the gateway's text in the bar. Needs a gateway
    /// whose `previous` window answers an error (`TEST_RUNNER_TIMELINE_BADPREV=1`).
    func testFailedSwitchKeepsWindow() throws {
        guard env["TIMELINE_BADPREV"] != nil else { throw XCTSkip("set TEST_RUNNER_TIMELINE_BADPREV") }
        XCTAssertTrue(title.waitForExistence(timeout: 30))
        sleep(1)
        remote.press(.left, forDuration: 1.5)
        let message = app.staticTexts["timeline.message"]
        XCTAssertTrue(message.waitForExistence(timeout: 5), "the gateway's text in the bar")
        shot("b1-failed-previous")
        let at = try XCTUnwrap(clockSeconds)
        sleep(2)
        XCTAssertGreaterThanOrEqual(try XCTUnwrap(clockSeconds) - at, 1, "the old window keeps playing")
    }
}
