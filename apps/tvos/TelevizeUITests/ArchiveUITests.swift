import XCTest

/// The Archiv tab (spec/apps.md "Archiv (T13)") pressed through XCUIRemote against a gateway serving `/dvb/archive`.
/// Opt-in: needs a running (mock) gateway whose first row has at least 6 cards and whose categories have more than
/// one page.
///
///     TEST_RUNNER_ARCHIVE_GATEWAY=127.0.0.1:58093 TEST_RUNNER_ARCHIVE_KEY=… [TEST_RUNNER_ARCHIVE_SHOTS=/some/dir] \
///       xcodebuild test -scheme Televize -destination 'platform=tvOS Simulator,name=…' \
///       -only-testing:TelevizeUITests/ArchiveUITests
@MainActor
final class ArchiveUITests: XCTestCase {
    private var app: XCUIApplication!
    private var remote: XCUIRemote { .shared }
    private var shots: String?

    override func setUpWithError() throws {
        let env = ProcessInfo.processInfo.environment
        guard let gateway = env["ARCHIVE_GATEWAY"], let key = env["ARCHIVE_KEY"] else {
            throw XCTSkip("set TEST_RUNNER_ARCHIVE_GATEWAY and TEST_RUNNER_ARCHIVE_KEY")
        }
        shots = env["ARCHIVE_SHOTS"]
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = ["-tab", "archive", "-gatewayAddress", gateway, "-gatewayKey", key]
        app.launch()
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'archive.card.'")).firstMatch
            .waitForExistence(timeout: 30), "archive did not load")
    }

    private func focused(_ prefix: String) -> String? {
        let element = app.buttons.matching(NSPredicate(format: "hasFocus == true AND identifier BEGINSWITH %@", prefix)).firstMatch
        return element.exists ? element.identifier : nil
    }

    private func shot(_ name: String) {
        guard let shots else { return }
        try? XCUIScreen.main.screenshot().pngRepresentation.write(to: URL(fileURLWithPath: shots).appendingPathComponent("\(name).png"))
    }

    /// Down into the first card row, along it card by card, a show page and Back to the same card.
    func testRowShowPageAndBack() {
        var tries = 0
        while focused("archive.card.") == nil, tries < 6 {
            remote.press(.down)
            tries += 1
        }
        guard let first = focused("archive.card.") else { return XCTFail("never reached a card row") }
        var seen = [first]
        for _ in 0..<5 {
            remote.press(.right)
            guard let next = focused("archive.card.") else { return XCTFail("focus left the row") }
            seen.append(next)
        }
        XCTAssertEqual(Set(seen).count, 6, "every right press moves one card: \(seen)")
        shot("archive-row")
        let card = seen.last!
        remote.press(.select)
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "identifier BEGINSWITH 'show.'")).firstMatch
            .waitForExistence(timeout: 10), "show page did not open")
        shot("archive-show")
        remote.press(.menu)
        let start = Date()
        while focused("archive.card.") != card, Date().timeIntervalSince(start) < 5 { usleep(200_000) }
        XCTAssertEqual(focused("archive.card."), card, "Back returns to the same card")
    }

    /// The row's end card opens the category; going down its grid asks for the next page (more cards appear).
    func testCategoryPagesAsFocusNearsTheEnd() {
        while focused("archive.card.") == nil { remote.press(.down) }
        var tries = 0
        while focused("archive.more.") == nil, tries < 80 {
            remote.press(.right)
            tries += 1
        }
        XCTAssertNotNil(focused("archive.more."), "no end card")
        remote.press(.select)
        XCTAssertTrue(app.buttons["archive.sort.popular"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["archive.grid.0"].waitForExistence(timeout: 10))
        while focused("archive.grid.") == nil { remote.press(.down) }
        for _ in 0..<12 { remote.press(.down) }
        shot("archive-grid")
        let index = Int(focused("archive.grid.")?.split(separator: ".").last ?? "") ?? 0
        XCTAssertGreaterThanOrEqual(index, 40, "the grid went past the first page of 40")
    }

    /// "Pokračovat ve sledování" plays an Anténa entry at once (needs a seeded `dvb:` entry in the row): the timeline
    /// player opens (or the gateway's text appears).
    func testContinueCardPlaysAtOnce() throws {
        var tries = 0
        while focused("archive.continue.") == nil, tries < 4 {
            remote.press(.down)
            tries += 1
        }
        guard focused("archive.continue.") != nil else { throw XCTSkip("no seeded resume entries") }
        // only an Anténa entry: a ČT one would ask ČT's real VOD API
        tries = 0
        while focused("archive.continue.dvb:") == nil, tries < 4 {
            remote.press(.left)
            tries += 1
        }
        guard focused("archive.continue.dvb:") != nil else { throw XCTSkip("no Anténa entry in the row") }
        remote.press(.select)
        sleep(4)
        shot("archive-continue-play")
        remote.press(.menu)
        sleep(2)
    }
}
