import XCTest

/// The programme page (spec/apps.md "Programme page (T15)") against a gateway that answers `/dvb/details`, pressed
/// through XCUIRemote. Opt-in: needs a running gateway whose first Anténa channel is airing a programme with a TMDb
/// match and a growing recording (the T15 mock gateway does).
///
///     TEST_RUNNER_PAGE_GATEWAY=127.0.0.1:58093 TEST_RUNNER_PAGE_KEY=… [TEST_RUNNER_PAGE_SHOTS=/some/dir] \
///       xcodebuild test -scheme Televize -destination 'platform=tvOS Simulator,name=…' \
///       -only-testing:TelevizeUITests/ProgrammePageUITests
@MainActor
final class ProgrammePageUITests: XCTestCase {
    private var app: XCUIApplication!
    private var remote: XCUIRemote { .shared }
    private var shots: String?

    override func setUpWithError() throws {
        let env = ProcessInfo.processInfo.environment
        guard let gateway = env["PAGE_GATEWAY"], let key = env["PAGE_KEY"] else {
            throw XCTSkip("set TEST_RUNNER_PAGE_GATEWAY and TEST_RUNNER_PAGE_KEY")
        }
        shots = env["PAGE_SHOTS"]
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = ["-tab", "guide", "-gatewayAddress", gateway, "-gatewayKey", key, "-channelPrefs", ""]
        app.launch()
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'guide.'")).firstMatch
            .waitForExistence(timeout: 30), "guide did not load")
    }

    private var focusedCell: String? {
        let cell = app.buttons.matching(NSPredicate(format: "hasFocus == true AND identifier BEGINSWITH 'guide.'")).firstMatch
        return cell.exists ? cell.identifier : nil
    }

    private var focusedRow: Int? {
        focusedCell.flatMap { Int($0.split(separator: ".")[1]) }
    }

    private var focusedPageButton: String? {
        let button = app.buttons.matching(NSPredicate(format: "hasFocus == true AND identifier BEGINSWITH 'page.'")).firstMatch
        return button.exists ? button.identifier : nil
    }

    private func element(_ id: String) -> XCUIElement {
        app.descendants(matching: .any).matching(identifier: id).firstMatch
    }

    private func shot(_ name: String) {
        guard let shots else { return }
        let data = XCUIScreen.main.screenshot().pngRepresentation
        try? data.write(to: URL(fileURLWithPath: shots).appendingPathComponent("\(name).png"))
    }

    func testSelectOpensThePageAndBackReturnsToTheSameCell() {
        let start = Date()
        while focusedCell == nil, Date().timeIntervalSince(start) < 20 {
            remote.press(.down)
            sleep(1)
        }
        XCTAssertNotNil(focusedCell, "never reached the grid")
        // row 6 = the first Anténa row in the default order; up/down follows the airing programmes
        while let row = focusedRow, row < 6, Date().timeIntervalSince(start) < 60 {
            remote.press(.down)
            usleep(700_000)
        }
        XCTAssertEqual(focusedRow, 6)
        let cell = focusedCell
        remote.press(.select)
        XCTAssertTrue(app.buttons["page.back"].waitForExistence(timeout: 10), "the page did not open")
        XCTAssertTrue(element("page.rating").waitForExistence(timeout: 10), "no TMDb rating line")
        XCTAssertTrue(element("page.rating").label.contains("Hodnocení TMDB 8,1 (1 234 hlasů)"), element("page.rating").label)
        XCTAssertTrue(element("page.poster").waitForExistence(timeout: 10), "no poster")
        XCTAssertTrue(element("page.attribution").exists, "TMDb data without the attribution")
        sleep(1)
        XCTAssertEqual(focusedPageButton, "page.live", "an airing programme starts on Živě (T26)")
        XCTAssertTrue(app.buttons["page.startOver"].exists)
        XCTAssertFalse(app.buttons["page.openOfficialApp"].exists, "Anténa programmes have no iVysílání button")
        shot("page-antenna-airing")
        remote.press(.menu)
        XCTAssertTrue(app.buttons["page.back"].waitForNonExistence(timeout: 10), "Back did not close the page")
        sleep(1)
        XCTAssertEqual(focusedCell, cell, "Back returns to the cell that opened the page")

        // the page stays and presents what a button leads to: "Živě" on the mock (no live stream) ends in the
        // gateway's 404 text, shown over the page
        remote.press(.select)
        XCTAssertTrue(app.buttons["page.back"].waitForExistence(timeout: 10))
        sleep(1)
        XCTAssertEqual(focusedPageButton, "page.live")
        remote.press(.select)
        XCTAssertTrue(app.alerts.firstMatch.waitForExistence(timeout: 15), "no message over the page")
        XCTAssertTrue(app.alerts.firstMatch.label.contains("Nenalezeno"), app.alerts.firstMatch.label)
        remote.press(.select)
        XCTAssertTrue(app.alerts.firstMatch.waitForNonExistence(timeout: 10))
        XCTAssertTrue(app.buttons["page.back"].exists, "the page stays after the message")
        remote.press(.menu)
        XCTAssertTrue(app.buttons["page.back"].waitForNonExistence(timeout: 10))
        sleep(1)
        XCTAssertEqual(focusedCell, cell)

        // the programme before it: ended, recorded, no TMDb match
        remote.press(.left)
        sleep(1)
        remote.press(.select)
        XCTAssertTrue(app.buttons["page.back"].waitForExistence(timeout: 10))
        sleep(2)
        XCTAssertFalse(app.buttons["page.live"].exists, "an ended programme has no Živě")
        XCTAssertEqual(focusedPageButton, "page.startOver")
        XCTAssertFalse(element("page.attribution").exists, "no attribution without a match")
        shot("page-antenna-ended")
        remote.press(.menu)
        XCTAssertTrue(app.buttons["page.back"].waitForNonExistence(timeout: 10))

        // up to ČT2 (programmes all evening): a CT programme gets "Otevřít v iVysílání", no TMDb lines (no match)
        while let row = focusedRow, row > 1 {
            remote.press(.up)
            usleep(700_000)
        }
        remote.press(.select)
        XCTAssertTrue(app.buttons["page.back"].waitForExistence(timeout: 10))
        sleep(2)
        XCTAssertTrue(app.buttons["page.openOfficialApp"].exists)
        XCTAssertFalse(element("page.attribution").exists, "no attribution without TMDb data")
        shot("page-ct")
        remote.press(.menu)
        XCTAssertTrue(app.buttons["page.back"].waitForNonExistence(timeout: 10))
    }

    /// The player opens over the page and, when it closes, the page offers "Pokračovat" focused. Plays an airing ČT
    /// programme from ČT (network); opt-in with TEST_RUNNER_PAGE_PLAY_CT=1.
    func testPlayerOverThePageThenResumeIsFocused() throws {
        guard ProcessInfo.processInfo.environment["PAGE_PLAY_CT"] == "1" else { throw XCTSkip("set TEST_RUNNER_PAGE_PLAY_CT=1") }
        let start = Date()
        while focusedCell == nil, Date().timeIntervalSince(start) < 20 {
            remote.press(.down)
            sleep(1)
        }
        sleep(3)  // the guide settles on the airing programme
        // the first ČT row with a programme on air (a row can be between programmes)
        var opened = false
        for _ in 0..<4 where !opened {
            remote.press(.select)
            opened = app.buttons["page.back"].waitForExistence(timeout: 4)
            if !opened {
                remote.press(.down)
                sleep(1)
            }
        }
        XCTAssertTrue(opened, "no ČT programme on air in the first rows")
        sleep(1)
        // "Pokračovat" from an earlier run, else "Od začátku"
        XCTAssertTrue(["page.resume", "page.startOver"].contains(focusedPageButton ?? ""), focusedPageButton ?? "none")
        remote.press(.select)
        sleep(100)  // "Pokračovat" is stored from 60 s into the programme; start-over begins 30 s early
        shot("player-over-page")
        remote.press(.menu)
        XCTAssertTrue(app.buttons["page.resume"].waitForExistence(timeout: 15), "no Pokračovat after the player")
        sleep(1)
        XCTAssertEqual(focusedPageButton, "page.resume", "the new Pokračovat is focused")
        shot("page-after-player")
        remote.press(.menu)
        XCTAssertTrue(app.buttons["page.back"].waitForNonExistence(timeout: 10))
    }
}
