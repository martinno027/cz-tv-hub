import Foundation
import XCTest
@testable import CTCore

/// spec/apps.md "Archiv" layout (T23): the menu on the left and what the grid shows. Golden: archive_cases.json "menu".
final class ArchiveMenuTests: XCTestCase {
    private func gridShape(_ grid: ArchiveGridContent) -> [String: AnyHashable] {
        switch grid {
        case .search: return ["show": "search"]
        case .continueWatching: return ["show": "continue"]
        case .recordings: return ["show": "recordings"]
        case .category(let slug, let genre):
            return ["show": "category", "category": slug, "genre": genre.map { AnyHashable($0) } ?? AnyHashable("<null>")]
        }
    }

    private func expectedGrid(_ object: [String: Any]) -> [String: AnyHashable] {
        var shape: [String: AnyHashable] = [:]
        for (key, value) in object {
            shape[key] = (value as? String).map { AnyHashable($0) } ?? AnyHashable("<null>")
        }
        return shape
    }

    func testGoldenMenu() throws {
        let cases = try Fixture.json("archive_cases")["menu"] as! [[String: Any]]
        XCTAssertGreaterThanOrEqual(cases.count, 6)
        var steps = 0
        for item in cases {
            let name = item["name"] as! String
            let categories = (item["categories"] as! [[String: String]]).map { ArchiveCategory(id: $0["id"]!, title: $0["title"]!) }
            let genres = (item["genres"] as? [String: [[String: String]]] ?? [:]).mapValues { list in
                list.map { ArchiveGenre(id: $0["id"]!, title: $0["title"]!) }
            }
            var menu = ArchiveMenu(categories: categories, continueEntries: item["continueEntries"] as! Int)
            for step in item["steps"] as! [[String: Any]] {
                steps += 1
                let action = step["do"] as! String
                var effect = ArchiveMenu.Effect.none
                switch action {
                case "open":
                    break
                case "ok":
                    let key = step["on"] as! String
                    guard let target = menu.items.first(where: { $0.key == key }) else {
                        XCTFail("\(name): no item \(key) in \(menu.items.map(\.key))")
                        continue
                    }
                    let slug: String? = if case .category(let slug) = target { slug } else { nil }
                    effect = menu.ok(target, genres: slug.flatMap { genres[$0] })
                case "back":
                    effect = menu.back()
                default:
                    XCTFail("\(name): unknown step \(action)")
                }
                let label = "\(name) / \(action) \(step["on"] as? String ?? "")"
                if step["leave"] as? Bool == true {
                    XCTAssertEqual(effect, .leave, label)
                    continue
                }
                XCTAssertNotEqual(effect, .leave, label)
                XCTAssertEqual(effect == .focusSearchField, step["focus"] as? String == "searchField", label)
                let state = step["state"] as! [String: Any]
                let level = state["level"] as! String
                switch menu.level {
                case .main:
                    XCTAssertEqual(level, "main", label)
                    XCTAssertNil(menu.title, label)
                case .sub(let slug):
                    XCTAssertEqual(level, "sub", label)
                    XCTAssertEqual(slug, state["category"] as? String, label)
                    XCTAssertEqual(menu.title, state["title"] as? String, label)
                    XCTAssertEqual(menu.title(of: .back), state["title"] as? String, label)
                }
                XCTAssertEqual(menu.items.map(\.key), state["items"] as! [String], label)
                XCTAssertEqual(menu.selected.key, state["selected"] as? String, label)
                XCTAssertEqual(gridShape(menu.grid), expectedGrid(state["grid"] as! [String: Any]), label)
            }
        }
        XCTAssertGreaterThanOrEqual(steps, 15)
    }

    private let categories = [ArchiveCategory(id: "3947-filmy", title: "Filmy"), ArchiveCategory(id: "3976-serialy", title: "Seriály")]
    private let filmGenres = [ArchiveGenre(id: "4012", title: "Krimi"), ArchiveGenre(id: "4013", title: "Komedie")]

    func testTitles() {
        var menu = ArchiveMenu(categories: categories, continueEntries: 0)
        XCTAssertEqual(menu.items.map(menu.title(of:)), ["Hledat", "Pokračovat ve sledování", "Naše nahrávky", "Filmy", "Seriály"])
        menu.ok(.category("3947-filmy"), genres: filmGenres)
        XCTAssertEqual(menu.items.map(menu.title(of:)), ["‹ Filmy", "Všechny", "Krimi", "Komedie"])
    }

    /// A focus resting on an item changes the grid but never opens a sub-menu; the back item filters nothing.
    func testFocusSelectsWithoutOpening() {
        var menu = ArchiveMenu(categories: categories, continueEntries: 0)
        menu.select(.category("3976-serialy"))
        XCTAssertEqual(menu.level, .main)
        XCTAssertEqual(menu.grid, .category("3976-serialy", genre: nil))
        menu.select(.recordings)
        XCTAssertEqual(menu.grid, .recordings)
        menu.select(.all)  // not in the main menu
        XCTAssertEqual(menu.grid, .recordings)
        menu.ok(.category("3947-filmy"), genres: filmGenres)
        menu.select(.genre("4013"))
        XCTAssertEqual(menu.grid, .category("3947-filmy", genre: "4013"))
        menu.select(.back)
        XCTAssertEqual(menu.grid, .category("3947-filmy", genre: "4013"))
        XCTAssertEqual(menu.level, .sub("3947-filmy"))
        XCTAssertEqual(ArchiveMenu.focusDebounce, .milliseconds(400))
    }

    /// Right before the 0.4 s: the focused item becomes the selection first; leaving from "‹ Filmy" selects nothing.
    func testFocusLeavingTheMenuSelectsFirst() {
        var menu = ArchiveMenu(categories: categories, continueEntries: 1)
        menu.focusLeaves(.category("3976-serialy"))
        XCTAssertEqual(menu.grid, .category("3976-serialy", genre: nil))
        XCTAssertEqual(menu.level, .main, "no sub-menu without OK")
        menu.ok(.category("3947-filmy"), genres: filmGenres)
        menu.focusLeaves(.back)
        XCTAssertEqual(menu.grid, .category("3947-filmy", genre: nil))
        XCTAssertEqual(menu.selected, .all)
        menu.focusLeaves(.genre("4012"))
        XCTAssertEqual(menu.grid, .category("3947-filmy", genre: "4012"))
    }

    /// The categories could not be read: only the three fixed items; Pokračovat with an entry, else Naše nahrávky.
    func testWithoutCategories() {
        XCTAssertEqual(ArchiveMenu(categories: [], continueEntries: 2).selected, .continueWatching)
        XCTAssertEqual(ArchiveMenu(categories: [], continueEntries: 0).grid, .recordings)
        XCTAssertEqual(ArchiveMenu(categories: [], continueEntries: 0).sort, .newest, "Naše nahrávky start with Nejnovější")
    }

    /// The sub-genres usually arrive with the category's first page, after OK.
    func testGenresArriveLater() {
        var menu = ArchiveMenu(categories: categories, continueEntries: 0)
        menu.ok(.category("3947-filmy"))
        XCTAssertEqual(menu.items.map(\.key), ["back", "all"])
        menu.setGenres(filmGenres, for: "3976-serialy")
        XCTAssertEqual(menu.items.map(\.key), ["back", "all"], "another category's answer")
        menu.setGenres(filmGenres, for: "3947-filmy")
        XCTAssertEqual(menu.items.map(\.key), ["back", "all", "4012", "4013"])
        menu.setGenres([ArchiveGenre(id: "1", title: "x")], for: "3947-filmy")
        XCTAssertEqual(menu.items.map(\.key), ["back", "all", "4012", "4013"], "the list does not change under the focus")
        menu.back()
        menu.setGenres(filmGenres, for: "3947-filmy")
        XCTAssertEqual(menu.items.count, 5, "main menu again")
    }

    /// Sort chips: three for a category, two for our recordings, none for the row and the search; the choice is
    /// kept within the category's sub-menu and reset for another category.
    func testSorts() {
        var menu = ArchiveMenu(categories: categories, continueEntries: 1)
        XCTAssertNil(menu.sort)
        XCTAssertEqual(menu.grid.sorts, [])
        menu.ok(.recordings)
        XCTAssertEqual(menu.grid.sorts, [.newest, .az])
        XCTAssertEqual(menu.grid.sorts.map(menu.grid.sortTitle), ["Nejnovější", "A–Z"])
        XCTAssertEqual(menu.sort, .newest)
        menu.setSort(.popular)
        XCTAssertEqual(menu.sort, .newest, "not offered for recordings")
        menu.ok(.category("3947-filmy"), genres: filmGenres)
        XCTAssertEqual(menu.grid.sorts.map(menu.grid.sortTitle), ["Nejsledovanější", "Naposledy vysílané", "A–Z"])
        XCTAssertEqual(menu.sort, .popular)
        menu.setSort(.az)
        menu.ok(.genre("4012"))
        XCTAssertEqual(menu.sort, .az, "kept within the sub-menu")
        menu.ok(.all)
        XCTAssertEqual(menu.sort, .az)
        menu.back()
        XCTAssertEqual(menu.sort, .az, "the whole category again")
        menu.select(.category("3976-serialy"))
        XCTAssertEqual(menu.sort, .popular, "reset for another category")
        menu.ok(.search)
        XCTAssertEqual(menu.grid.sorts, [])
        XCTAssertNil(menu.sort)
    }

    func testEmptyTexts() {
        XCTAssertEqual(ArchiveGridContent.continueWatching.emptyText, "Zatím nemáte nic rozkoukaného")
        XCTAssertEqual(ArchiveGridContent.recordings.emptyText, "Zatím žádné nahrávky")
        XCTAssertEqual(ArchiveGridContent.category("x", genre: nil).emptyText, "Nic nenalezeno")
        XCTAssertEqual(ArchiveGridContent.category("x", genre: "1").emptyText, "Nic nenalezeno")
        XCTAssertEqual(ArchiveGridContent.recordings.slug, "recordings")
        XCTAssertNil(ArchiveGridContent.continueWatching.slug)
    }

    /// No categories at all (an empty gateway list): opens on our recordings, never on nothing.
    func testNoCategories() {
        let menu = ArchiveMenu(categories: [], continueEntries: 0)
        XCTAssertEqual(menu.selected, .recordings)
        XCTAssertEqual(menu.items.map(\.key), ["search", "continue", "recordings"])
    }

    func testSearchBack() {
        XCTAssertEqual(ArchiveRules.searchBack(text: "pel"), .clear)
        XCTAssertEqual(ArchiveRules.searchBack(text: ""), .toMenu)
    }
}

final class ArchiveCategoriesParserTests: XCTestCase {
    private func json(_ object: Any) -> Data { try! JSONSerialization.data(withJSONObject: object) }

    func testShape() throws {
        let body = json(["categories": [
            ["id": "3947-filmy", "title": "Filmy"],
            ["id": "3976-serialy", "title": "Seriály"],
            ["id": "", "title": "bez id"],
            ["id": "x"],
            ["id": "3947-filmy", "title": "Filmy znovu"],
            ["id": "recordings", "title": "Naše nahrávky"],
            "not an object",
            ["id": "4732-archiv", "title": "Archiv"],
        ]])
        let categories = try ArchiveParser.categories(status: 200, body: body)
        XCTAssertEqual(categories, [ArchiveCategory(id: "3947-filmy", title: "Filmy"),
                                    ArchiveCategory(id: "3976-serialy", title: "Seriály"),
                                    ArchiveCategory(id: "4732-archiv", title: "Archiv")])
        XCTAssertEqual(try ArchiveParser.categories(status: 200, body: json([:])), [])
        XCTAssertThrowsError(try ArchiveParser.categories(status: 404, body: json(["error": "Nenalezeno"])))
    }

    /// The gateway's sample (spec/fixtures/dvb/dvb_archive_categories.json): 14 categories, ČT's "Archiv" last.
    func testGatewaySample() throws {
        let response: HTTPResponse
        do { response = try Fixture.response("dvb/dvb_archive_categories") } catch {
            throw XCTSkip("spec/fixtures/dvb/dvb_archive_categories.json not there yet")
        }
        let categories = try ArchiveParser.categories(status: response.status, body: response.body)
        XCTAssertEqual(categories.count, 14)
        XCTAssertEqual(categories.first?.id, "3947-filmy")
        XCTAssertEqual(categories.first?.title, "Filmy")
        XCTAssertEqual(categories.last?.title, "Archiv")
    }

    func testClientPath() async throws {
        let transport = FakeTransport()
        transport.on("/dvb/archive/categories", HTTPResponse(status: 200, body: json(["categories": [["id": "a", "title": "A"]]])))
        let client = DVBClient(config: GatewayConfig(baseURL: URL(string: "http://gw.local:8098")!, key: "k3y"), transport: transport)
        let categories = try await client.archiveCategories()
        XCTAssertEqual(categories.map(\.id), ["a"])
        XCTAssertEqual(transport.requests.map { $0.url!.absoluteString }, ["http://gw.local:8098/dvb/archive/categories"])
        XCTAssertEqual(transport.requests[0].value(forHTTPHeaderField: "Authorization"), "Bearer k3y")
    }
}
