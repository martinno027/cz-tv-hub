import XCTest

/// The Archiv tab (spec/apps.md "Archiv", menu + grid of T23) pressed through XCUIRemote against a gateway serving
/// `/dvb/archive*`. Opt-in: needs a running (mock) gateway with the category `3947-filmy` (sub-genres, more than one
/// page of cards) and "Naše nahrávky".
///
/// Never selects an iVysílání (`ct:`) card or entry: that would ask ČT's real VOD API. Only `rec:` cards and `dvb:`
/// entries are opened.
///
///     TEST_RUNNER_ARCHIVE_GATEWAY=127.0.0.1:58093 TEST_RUNNER_ARCHIVE_KEY=… [TEST_RUNNER_ARCHIVE_SHOTS=/some/dir] \
///       xcodebuild test -scheme Televize -destination 'platform=tvOS Simulator,name=…' \
///       -only-testing:TelevizeUITests/ArchiveUITests
@MainActor
final class ArchiveUITests: XCTestCase {
    private var app: XCUIApplication!
    private var remote: XCUIRemote { .shared }
    private var shots: String?
    private let films = "archive.menu.3947-filmy"

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
        XCTAssertTrue(app.buttons[films].waitForExistence(timeout: 30), "the menu did not load")
    }

    private func focused(_ prefix: String) -> String? {
        let element = app.buttons.matching(NSPredicate(format: "hasFocus == true AND identifier BEGINSWITH %@", prefix)).firstMatch
        return element.exists ? element.identifier : nil
    }

    private var focusedElement: XCUIElement {
        app.buttons.matching(NSPredicate(format: "hasFocus == true")).firstMatch
    }

    private func waitFocus(_ identifier: String, timeout: TimeInterval = 5) -> Bool {
        let start = Date()
        while focused(identifier) != identifier, Date().timeIntervalSince(start) < timeout { usleep(200_000) }
        return focused(identifier) == identifier
    }

    private func shot(_ name: String) {
        guard let shots else { return }
        try? XCUIScreen.main.screenshot().pngRepresentation.write(to: URL(fileURLWithPath: shots).appendingPathComponent("\(name).png"))
    }

    /// From the tab bar down into the menu: the focus lands on the selected item.
    private func intoMenu() -> String? {
        var tries = 0
        while focused("archive.menu.") == nil, tries < 4 {
            remote.press(.down)
            usleep(300_000)
            tries += 1
        }
        return focused("archive.menu.")
    }

    /// Up or down the menu to the item.
    private func menu(to identifier: String) {
        for direction in [XCUIRemote.Button.up, .down] {
            var last: String?
            while let now = focused("archive.menu."), now != identifier, now != last {
                last = now
                remote.press(direction)
                usleep(250_000)
            }
            if focused("archive.menu.") == identifier { return }
        }
        XCTFail("never reached \(identifier)")
    }

    private func selectedMenuItem() -> String? {
        let element = app.buttons.matching(NSPredicate(format: "value == 'selected' AND identifier BEGINSWITH 'archive.menu.'")).firstMatch
        return element.exists ? element.identifier : nil
    }

    /// Opens on the selected item; OK on Filmy opens "‹ Filmy" / Všechny (focused) / sub-genres; a sub-genre filters
    /// once the focus rests; Back returns to the main menu on Filmy.
    func testSubMenuAndBack() {
        let selected = selectedMenuItem()
        XCTAssertNotNil(selected)
        XCTAssertEqual(intoMenu(), selected, "into the menu onto the selected item")
        menu(to: films)
        remote.press(.select)
        XCTAssertTrue(waitFocus("archive.menu.all"), "Všechny focused in the sub-menu")
        XCTAssertEqual(app.buttons["archive.menu.back"].label, "‹ Filmy")
        XCTAssertEqual(app.buttons["archive.menu.all"].label, "Všechny")
        XCTAssertEqual(selectedMenuItem(), "archive.menu.all")
        // the sub-genres come with the category's answer
        let genres = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'archive.menu.' AND NOT identifier IN %@",
                                                      ["archive.menu.back", "archive.menu.all"]))
        XCTAssertTrue(genres.firstMatch.waitForExistence(timeout: 10), "no sub-genres")
        remote.press(.down)
        let genre = focused("archive.menu.")
        XCTAssertNotEqual(genre, "archive.menu.all")
        sleep(1)  // past the 0.4 s
        XCTAssertEqual(selectedMenuItem(), genre, "the grid follows the resting focus")
        shot("archive-submenu")
        remote.press(.menu)
        XCTAssertTrue(waitFocus(films), "Back returns to the category")
        XCTAssertFalse(app.buttons["archive.menu.all"].exists)
        XCTAssertEqual(selectedMenuItem(), films)
        shot("archive-main-menu")
    }

    /// Right into the grid, down it until the next page came, Back to the menu, Right and Left back to the item.
    func testGridPagesBackAndLeft() {
        _ = intoMenu()
        menu(to: films)
        sleep(1)
        remote.press(.right)
        XCTAssertNotNil(focused("archive.card."), "Right moves into the grid")
        XCTAssertEqual(selectedMenuItem(), films)
        for _ in 0..<15 { remote.press(.down) }
        shot("archive-grid")
        let index = Int(focusedElement.value as? String ?? "") ?? 0
        XCTAssertGreaterThanOrEqual(index, 40, "the grid went past the first page of 40")
        remote.press(.menu)
        XCTAssertTrue(waitFocus(films), "Back in the grid returns to the menu")
        remote.press(.right)
        XCTAssertNotNil(focused("archive.card."))
        remote.press(.left)
        XCTAssertTrue(waitFocus(films), "Left from the first column returns to the selected item")
    }

    /// Naše nahrávky: sort Nejnovější · A–Z; a recording's show page and Back to the same card. Only `rec:` cards.
    func testRecordingShowPageAndBack() throws {
        _ = intoMenu()
        menu(to: "archive.menu.recordings")
        remote.press(.select)
        XCTAssertTrue(app.buttons["archive.sort.newest"].waitForExistence(timeout: 10))
        XCTAssertEqual(app.buttons["archive.sort.newest"].label, "Nejnovější")
        XCTAssertFalse(app.buttons["archive.sort.popular"].exists)
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'archive.card.'")).firstMatch
            .waitForExistence(timeout: 10))
        remote.press(.right)
        guard let card = focused("archive.card.rec:") else { throw XCTSkip("the focused card is not a recording") }
        remote.press(.select)
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "identifier BEGINSWITH 'show.'")).firstMatch
            .waitForExistence(timeout: 10), "show page did not open")
        shot("archive-show")
        remote.press(.menu)
        XCTAssertTrue(waitFocus(card), "Back returns to the same card")
        XCTAssertEqual(selectedMenuItem(), "archive.menu.recordings", "the menu kept")
    }

    /// Every opening of the tab starts the menu afresh: Naše nahrávky chosen, to Nastavení and back, the initial item
    /// again.
    func testTabOpeningResetsTheMenu() {
        let initial = selectedMenuItem()
        XCTAssertNotEqual(initial, "archive.menu.recordings")
        _ = intoMenu()
        menu(to: "archive.menu.recordings")
        remote.press(.select)
        XCTAssertEqual(selectedMenuItem(), "archive.menu.recordings")
        remote.press(.menu)  // the main menu: the system's Back, up to the tab bar
        sleep(1)
        XCTAssertNil(focused("archive."), "Back in the main menu leaves the tab's content")
        remote.press(.right)
        sleep(1)
        remote.press(.left)
        sleep(2)
        XCTAssertEqual(selectedMenuItem(), initial, "a new opening starts on the initial item")
        XCTAssertEqual(intoMenu(), initial)
    }

    /// OK on Hledat puts the focus into the search field (in the grid area); Back with nothing typed returns to the menu.
    func testSearchFieldAndBack() {
        _ = intoMenu()
        menu(to: "archive.menu.search")
        remote.press(.select)
        let field = app.textFields["archive.search"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        let start = Date()
        while !field.hasFocus, Date().timeIntervalSince(start) < 5 { usleep(200_000) }
        XCTAssertTrue(field.hasFocus, "OK on Hledat focuses the field")
        XCTAssertEqual(field.placeholderValue, "Hledat v archivu")
        shot("archive-search")
        remote.press(.menu)
        XCTAssertTrue(waitFocus("archive.menu.search"), "Back in an empty field returns to the menu")
    }

    /// "Pokračovat ve sledování" plays an Anténa entry at once (needs a seeded `dvb:` entry): the timeline player
    /// opens (or the gateway's text appears).
    func testContinueCardPlaysAtOnce() throws {
        _ = intoMenu()
        menu(to: "archive.menu.continue")
        remote.press(.select)
        sleep(1)
        remote.press(.right)
        guard focused("archive.continue.") != nil else { throw XCTSkip("no seeded resume entries") }
        // only an Anténa entry: a ČT one would ask ČT's real VOD API
        var tries = 0
        while focused("archive.continue.dvb:") == nil, tries < 4 {
            remote.press(.right)
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
