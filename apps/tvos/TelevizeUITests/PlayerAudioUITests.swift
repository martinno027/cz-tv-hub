import XCTest

/// "Audio choice for Anténa" (spec/apps.md): a gateway channel recorded with several audio tracks plays from a
/// master playlist and AVPlayerViewController's audio menu lists every track, Czech selected. Opt-in: needs a
/// running gateway whose channel 1 is recorded continuously with a Czech and an English track (live, the airing
/// programme and an ended one).
///
///     TEST_RUNNER_AUDIO_GATEWAY=127.0.0.1:8097 TEST_RUNNER_AUDIO_KEY=dev-key-dev-key-dev-key-dev-key-0000 \
///       xcodebuild test -scheme Televize -destination 'platform=tvOS Simulator,name=Apple TV' \
///       -only-testing:TelevizeUITests/PlayerAudioUITests
@MainActor
final class PlayerAudioUITests: XCTestCase {
    /// The app is Czech only (CFBundleLocalizations), so AVKit names the tracks in Czech whatever the TV's
    /// language, and Czech stays selected also on an English Apple TV (AVPlayer would otherwise follow it).
    func testLiveAudioMenuCzechTV() throws {
        assertCzechMenu(try openAudioMenu(language: "cs"))
    }

    func testLiveAudioMenuEnglishTV() throws {
        assertCzechMenu(try openAudioMenu(language: "en"))
    }

    /// Start-over of the airing programme (a growing `b…` recording): the timeline player's own audio list
    /// (long OK, spec/apps.md "Anténa timeline player"), Czech focused.
    func testStartOverAudioMenu() throws {
        try assertCzechTimelineList(play: "dvbstartover", language: "en")
    }

    /// The latest finished programme of the channel, also in the timeline player.
    func testRecordingAudioMenu() throws {
        try assertCzechTimelineList(play: "dvbrec", language: "en")
    }

    private func assertCzechTimelineList(play: String, language: String) throws {
        let env = ProcessInfo.processInfo.environment
        guard let gateway = env["AUDIO_GATEWAY"], let key = env["AUDIO_KEY"] else {
            throw XCTSkip("set TEST_RUNNER_AUDIO_GATEWAY and TEST_RUNNER_AUDIO_KEY")
        }
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-gatewayAddress", gateway, "-gatewayKey", key,
                               "-play", "\(play):\(env["AUDIO_CHANNEL"] ?? "1")",
                               "-AppleLanguages", "(\(language))", "-AppleLocale", language == "cs" ? "cs_CZ" : "en_US"]
        app.launch()
        XCTAssertTrue(app.staticTexts["timeline.title"].waitForExistence(timeout: 30), "player did not open")
        sleep(3)
        XCUIRemote.shared.press(.select, forDuration: 1.2)
        let czech = app.buttons["Čeština"].firstMatch
        XCTAssertTrue(czech.waitForExistence(timeout: 3), "Čeština missing")
        XCTAssertTrue(app.buttons["Angličtina"].firstMatch.exists, "Angličtina missing")
        XCTAssertTrue(czech.hasFocus, "Čeština not selected")
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "audio-list-\(play)-\(language)"
        shot.lifetime = .keepAlways
        add(shot)
    }

    /// Picking the other track in the menu switches to it (English AC-3 rendition) and playback goes on.
    func testSwitchToEnglish() throws {
        let app = try openAudioMenu(language: "cs")
        let english = app.cells.matching(NSPredicate(format: "label CONTAINS 'Angličtina'")).firstMatch
        for _ in 0..<4 where !english.hasFocus {
            XCUIRemote.shared.press(.down)
            sleep(1)
        }
        XCTAssertTrue(english.hasFocus, "could not focus Angličtina")
        XCUIRemote.shared.press(.select)
        sleep(8)
        _ = try openAudioMenu(language: "cs", app: app)
        XCTAssertTrue(app.cells.matching(NSPredicate(format: "label CONTAINS 'vybráno, Angličtina'")).firstMatch.exists,
                      "Angličtina not selected after switching")
    }

    /// AVKit labels the tracks by language; the selected row reads "vybráno, Čeština".
    private func assertCzechMenu(_ app: XCUIApplication) {
        func row(_ text: String) -> XCUIElement {
            app.cells.matching(NSPredicate(format: "label CONTAINS %@", text)).firstMatch
        }
        XCTAssertTrue(row("Čeština").exists, "Čeština missing")
        XCTAssertTrue(row("Angličtina").exists, "Angličtina missing")
        XCTAssertTrue(row("vybráno, Čeština").exists, "Čeština not selected")
        XCTAssertFalse(row("vybráno, Angličtina").exists, "Angličtina selected")
    }

    private func openAudioMenu(play: String = "dvblive", language: String, app running: XCUIApplication? = nil) throws -> XCUIApplication {
        let env = ProcessInfo.processInfo.environment
        guard let gateway = env["AUDIO_GATEWAY"], let key = env["AUDIO_KEY"] else {
            throw XCTSkip("set TEST_RUNNER_AUDIO_GATEWAY and TEST_RUNNER_AUDIO_KEY")
        }
        continueAfterFailure = false
        let app = running ?? XCUIApplication()
        if running == nil {
        app.launchArguments = ["-gatewayAddress", gateway, "-gatewayKey", key,
                               "-play", "\(play):\(env["AUDIO_CHANNEL"] ?? "1")",
                               "-AppleLanguages", "(\(language))", "-AppleLocale", language == "cs" ? "cs_CZ" : "en_US"]
        app.launch()
        }
        let remote = XCUIRemote.shared
        let audio = app.cells["AVAudibleSettings"]
        XCTAssertTrue(audio.waitForExistence(timeout: 30), "player did not open")
        if running == nil { sleep(5) }  // playing
        remote.press(.select)  // transport bar, focus on the progress bar
        sleep(2)
        remote.press(.up)  // subtitles / audio buttons
        for _ in 0..<3 where !audio.hasFocus {
            sleep(1)
            remote.press(.right)
        }
        sleep(1)
        XCTAssertTrue(audio.hasFocus, "could not focus the audio button")
        remote.press(.select)
        sleep(2)
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "audio-menu-\(play)-\(language)"
        shot.lifetime = .keepAlways
        add(shot)
        return app
    }
}
