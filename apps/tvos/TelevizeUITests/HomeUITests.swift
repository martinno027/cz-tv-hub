import XCTest

/// Domů (spec/apps.md "Domů", T58) pressed through XCUIRemote: the app starts on it with the first card focused,
/// up/down keep the column as far as the next row allows, up from the first row reaches the tab bar, back from the
/// player the played card has the focus again. Opt-in: needs a running (fake) gateway with the finished recording
/// `TEST_RUNNER_HOME_RECORDING` (a `dvb:` resume entry for it and a ČT one are seeded through the launch arguments).
///
/// Never selects the ČT entry or a channel card: that would ask ČT's real API.
///
///     TEST_RUNNER_HOME_GATEWAY=127.0.0.1:58091 TEST_RUNNER_HOME_KEY=dev-key-dev-key-dev-key-dev-key-0000 \
///     TEST_RUNNER_HOME_RECORDING=<recording id> \
///       xcodebuild test -scheme Televize -destination 'platform=tvOS Simulator,name=…' \
///       -only-testing:TelevizeUITests/HomeUITests
@MainActor
final class HomeUITests: XCTestCase {
    private var app: XCUIApplication!
    private var remote: XCUIRemote { .shared }
    private var recording = ""

    override func setUpWithError() throws {
        let env = ProcessInfo.processInfo.environment
        guard let gateway = env["HOME_GATEWAY"], let key = env["HOME_KEY"], let recording = env["HOME_RECORDING"] else {
            throw XCTSkip("set TEST_RUNNER_HOME_GATEWAY, TEST_RUNNER_HOME_KEY and TEST_RUNNER_HOME_RECORDING")
        }
        self.recording = recording
        continueAfterFailure = false
        app = XCUIApplication()
        // no -tab: every start opens Domů
        app.launchArguments = ["-gatewayAddress", gateway, "-gatewayKey", key, "-resume.v1", Self.seed(recording: recording)]
        app.launch()
    }

    /// Six resume entries, the recording the newest (first in the row), then five ČT ones, as the old-style plist
    /// data the argument domain reads.
    private nonisolated static func seed(recording: String) -> String {
        let now = Date().timeIntervalSinceReferenceDate
        let start = now - 6 * 3600
        var entries = [#""dvb:\#(recording)": {"progressSeconds": 900, "lengthSeconds": 2700, "savedAt": \#(now - 60), "card": {"title": "Nahrávka", "source": "dvb", "start": \#(start), "showTitle": ""}}"#]
        for index in 1...5 {
            entries.append(#""ct:00000000000000\#(index)": {"progressSeconds": 1500, "lengthSeconds": 3000, "savedAt": \#(now - 600 * Double(index)), "card": {"title": "Pořad ČT \#(index)", "source": "ct", "start": \#(start), "showTitle": "", "channel": "CH_1"}}"#)
        }
        let json = "{" + entries.joined(separator: ", ") + "}"
        return "<" + Data(json.utf8).map { String(format: "%02x", $0) }.joined() + ">"
    }

    private func focused(_ prefix: String) -> String? {
        let element = app.buttons.matching(NSPredicate(format: "hasFocus == true AND identifier BEGINSWITH %@", prefix)).firstMatch
        return element.exists ? element.identifier : nil
    }

    private func waitFocus(_ identifier: String, timeout: TimeInterval = 5) -> Bool {
        let start = Date()
        while focused(identifier) != identifier, Date().timeIntervalSince(start) < timeout { usleep(200_000) }
        return focused(identifier) == identifier
    }

    private func press(_ button: XCUIRemote.Button) {
        remote.press(button)
        usleep(600_000)
    }

    /// The identifier of the n-th card (0-based) of a row.
    private func card(_ row: String, _ index: Int) -> String {
        app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "home.\(row).")).element(boundBy: index).identifier
    }

    private func move(_ button: XCUIRemote.Button, times: Int) {
        for _ in 0..<times { press(button) }
    }

    /// Back from the player (the first press may only hide the transport bar).
    private func back() {
        for _ in 0..<3 where focused("home.") == nil {
            press(.menu)
            sleep(2)
        }
    }

    func testFocusAndRows() throws {
        let first = "home.resume.dvb:\(recording)"
        XCTAssertTrue(app.staticTexts["home.date"].waitForExistence(timeout: 20), "Domů is not the first tab")
        XCTAssertTrue(waitFocus(first, timeout: 15), "start: \(focused("") ?? "nothing") focused, not the first card")
        // the Anténa channels are in the row (they arrive after ČT's and move the cards after them)
        let antennaCard = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'home.live.dvb:'")).firstMatch
        XCTAssertTrue(antennaCard.waitForExistence(timeout: 15), "no Anténa channel among the first ten")
        sleep(2)

        // up/down keep the column (card widths differ, so it is not where the card is on screen)
        move(.right, times: 4)
        XCTAssertEqual(focused("home."), card("resume", 4))
        press(.down)
        XCTAssertEqual(focused("home."), card("live", 4), "down keeps the column")
        press(.up)
        XCTAssertEqual(focused("home."), card("resume", 4), "up keeps the column")
        move(.left, times: 3)
        press(.down)
        move(.right, times: 6)
        XCTAssertEqual(focused("home."), card("live", 7))
        press(.up)
        XCTAssertEqual(focused("home."), card("resume", 5), "up: as far as the row allows")

        // up from the first row: the tab bar
        move(.left, times: 5)
        XCTAssertTrue(waitFocus(first))
        press(.up)
        XCTAssertNil(focused("home."), "up from the first row leaves the content")
        press(.down)
        XCTAssertNotNil(focused("home.resume."), "down from the tab bar: back into the first row")

        // the recording plays; back: the played card again
        XCTAssertTrue(waitFocus(first))
        press(.select)
        sleep(8)
        XCTAssertNil(focused("home."), "the player did not open")
        back()
        XCTAssertTrue(waitFocus(first), "back from the player: \(focused("") ?? "nothing") focused")

        // an Anténa channel of "Teď běží" plays live; back: that card, not the row's first
        press(.down)
        var steps = 0
        while let now = focused("home.live."), !now.hasPrefix("home.live.dvb"), steps < 10 {
            press(.right)
            steps += 1
        }
        let antenna = try XCTUnwrap(focused("home.live.dvb"), "no Anténa channel among the first ten")
        press(.select)
        sleep(8)
        XCTAssertNil(focused("home."), "the player did not open")
        back()
        XCTAssertTrue(waitFocus(antenna), "back from the live player: \(focused("") ?? "nothing") focused")
    }
}
