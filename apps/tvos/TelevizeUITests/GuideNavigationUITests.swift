import XCTest

/// Remote navigation in the TV guide against a fake gateway with many channels, pressed through
/// XCUIRemote (reliable, unlike simulated keyboard input). Opt-in: needs a running gateway.
///
///     cd server && PYTHONPATH=src python3 -m cztvhub dvb-fake --port 39981 --channels 60
///     # gateway with deploy/cztvhub-dev.conf pointing at it, listening on 127.0.0.1:38098
///     TEST_RUNNER_GUIDE_GATEWAY=127.0.0.1:38098 TEST_RUNNER_GUIDE_KEY=dev-key-dev-key-dev-key-dev-key-0000 \
///       xcodebuild test -scheme Televize -destination 'platform=tvOS Simulator,name=Apple TV' -only-testing:TelevizeUITests
@MainActor
final class GuideNavigationUITests: XCTestCase {
    private var app: XCUIApplication!
    private var remote: XCUIRemote { .shared }

    override func setUpWithError() throws {
        let env = ProcessInfo.processInfo.environment
        guard let gateway = env["GUIDE_GATEWAY"], let key = env["GUIDE_KEY"] else {
            throw XCTSkip("set TEST_RUNNER_GUIDE_GATEWAY and TEST_RUNNER_GUIDE_KEY")
        }
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = ["-tab", "guide", "-gatewayAddress", gateway, "-gatewayKey", key,
                               "-channelPrefs", ""]
        app.launch()
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'guide.'")).firstMatch
            .waitForExistence(timeout: 30), "guide did not load")
    }

    /// Guide cells carry the identifier "guide.<row>.<segment>"; the focused one says where focus is.
    private var focused: (row: Int, segment: Int)? {
        let cell = app.buttons.matching(NSPredicate(format: "hasFocus == true AND identifier BEGINSWITH 'guide.'")).firstMatch
        guard cell.exists else { return nil }
        let parts = cell.identifier.split(separator: ".")
        guard parts.count == 3, let row = Int(parts[1]), let segment = Int(parts[2]) else { return nil }
        return (row, segment)
    }

    /// Presses and waits until the focused cell changes; returns where it went and how long it took.
    private func press(_ button: XCUIRemote.Button) -> (row: Int, segment: Int, took: TimeInterval) {
        let before = focused
        let start = Date()
        remote.press(button)
        var now = focused
        while Date().timeIntervalSince(start) < 20, now == nil || (now?.row == before?.row && now?.segment == before?.segment) {
            usleep(50_000)
            now = focused
        }
        let took = Date().timeIntervalSince(start)
        return (now?.row ?? -1, now?.segment ?? -1, took)
    }

    /// From the tab bar down into the grid (via the day strip).
    private func enterGrid() {
        let start = Date()
        while focused == nil, Date().timeIntervalSince(start) < 20 {
            remote.press(.down)
            sleep(1)
        }
        XCTAssertNotNil(focused, "never reached the grid")
    }

    func testDownMovesOneRowAtATimeQuickly() {
        enterGrid()
        var rows = [focused?.row ?? -1]
        var slowest: TimeInterval = 0
        for _ in 0..<25 {
            let step = press(.down)
            rows.append(step.row)
            slowest = max(slowest, step.took)
        }
        print("GUIDE down rows: \(rows); slowest: \(String(format: "%.2f", slowest)) s")
        XCTAssertEqual(rows, Array(rows[0]..<(rows[0] + rows.count)), "each down must move exactly one row")
        XCTAssertLessThan(slowest, 2, "focus took \(slowest) s to move")
    }

    func testRightKeepsTheRowAndBuildsAhead() {
        enterGrid()
        let row = focused?.row ?? -1
        var segments = [focused?.segment ?? -1]
        var slowest: TimeInterval = 0
        for _ in 0..<30 {
            let step = press(.right)
            XCTAssertEqual(step.row, row, "right moved to another row (segment \(step.segment))")
            segments.append(step.segment)
            slowest = max(slowest, step.took)
        }
        print("GUIDE right segments: \(segments); slowest: \(String(format: "%.2f", slowest)) s")
        XCTAssertEqual(segments, segments.sorted(), "right must only move forward")
        XCTAssertLessThan(slowest, 2, "focus took \(slowest) s to move")
        XCTAssertEqual(press(.down).row, row + 1, "down after right must go to the next row")
    }

    /// "Across days": left past a row's first cell opens the previous day on the same row at that day's end (not at its
    /// default 20:00 anchor; Samsung had that bug). The day ends at 05:00, so the detail then says "Dnes H:MM" with H < 5.
    func testLeftPastTheFirstCellOpensThePreviousDayAtItsEnd() {
        enterGrid()
        let row = focused?.row ?? -1
        var segment = focused?.segment ?? -1
        let start = Date()
        var crossed = false
        while !crossed, Date().timeIntervalSince(start) < 120 {
            remote.press(.left)
            sleep(1)
            let now = focused?.segment ?? -1
            crossed = now > segment  // the previous day's last cell has a high index
            segment = now
        }
        sleep(2)
        XCTAssertTrue(crossed, "never reached the previous day")
        XCTAssertEqual(focused?.row, row, "the same row")
        let detail = app.staticTexts.matching(NSPredicate(format: "label CONTAINS '  ·  Dnes ' OR label CONTAINS '  ·  Včera '")).firstMatch
        let label = detail.label  // "ČT1  ·  Dnes 4:30–6:00  ·  …"
        let time = label.components(separatedBy: "  ·  ")[1].split(separator: " ")[1]
        let hour = Int(time.split(separator: ":")[0]) ?? -1
        print("GUIDE across days: \(label)")
        XCTAssertTrue(label.contains("Dnes") && (0...5).contains(hour), "landed at \(label), not at the day's end")
    }
}
