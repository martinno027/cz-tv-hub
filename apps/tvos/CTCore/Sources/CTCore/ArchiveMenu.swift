import Foundation

// The Archiv tab's menu (spec/apps.md "Archiv", layout T23): a menu on the left, a grid of what the selected item
// filters on the right. Golden: archive_cases.json "menu".

/// One of the 14 categories as the gateway lists them (`GET /dvb/archive/categories`).
public struct ArchiveCategory: Sendable, Hashable, Identifiable {
    /// The slug (`3947-filmy`), also the category route's id.
    public let id: String
    public let title: String

    public init(id: String, title: String) {
        self.id = id
        self.title = title
    }
}

/// What the grid shows.
public enum ArchiveGridContent: Sendable, Hashable {
    case search
    case continueWatching
    case recordings
    /// A category, `genre` nil = the whole category ("Všechny").
    case category(String, genre: String?)

    /// The sort chips above the grid, the first one the default.
    public var sorts: [ArchiveSort] {
        switch self {
        case .category: return [.popular, .newest, .az]
        case .recordings: return [.newest, .az]
        case .search, .continueWatching: return []
        }
    }

    /// The chip's label: "Naposledy vysílané" in a category, "Nejnovější" for our recordings.
    public func sortTitle(_ sort: ArchiveSort) -> String {
        if case .recordings = self, sort == .newest { return "Nejnovější" }
        return sort.title
    }

    /// The text of an empty grid once loaded.
    public var emptyText: String {
        switch self {
        case .continueWatching: return "Zatím nemáte nic rozkoukaného"
        case .recordings: return "Zatím žádné nahrávky"
        case .category, .search: return ArchiveRules.nothingFound
        }
    }

    /// The gateway's category slug the grid pages through (`recordings` = ours only); nil for search and the row.
    public var slug: String? {
        switch self {
        case .recordings: return "recordings"
        case .category(let slug, _): return slug
        case .search, .continueWatching: return nil
        }
    }
}

public struct ArchiveMenu: Sendable, Equatable {
    public enum Item: Sendable, Hashable {
        // main menu
        case search, continueWatching, recordings
        case category(String)
        // a category's sub-menu
        case back, all
        case genre(String)

        /// The golden case's name of the item ("search", "continue", "recordings", a slug; "back", "all", a genre id).
        public var key: String {
            switch self {
            case .search: return "search"
            case .continueWatching: return "continue"
            case .recordings: return "recordings"
            case .category(let slug): return slug
            case .back: return "back"
            case .all: return "all"
            case .genre(let id): return id
            }
        }
    }

    public enum Level: Sendable, Equatable {
        case main
        /// The sub-menu of this category.
        case sub(String)
    }

    /// What OK or Back asks the screen to do besides the new state.
    public enum Effect: Sendable, Equatable {
        case none
        /// Back in the main menu: the tab is left as elsewhere (the system's Back).
        case leave
        /// OK on "Hledat": the focus goes into the search field.
        case focusSearchField
    }

    /// Moving the focus changes the grid only after this long without moving (no request per item passed).
    public static let focusDebounce: Duration = .milliseconds(400)
    public static let allTitle = "Všechny"

    public let categories: [ArchiveCategory]
    public private(set) var level: Level = .main
    public private(set) var selected: Item
    /// The sub-genres of the open sub-menu's category (ČT's order).
    public private(set) var genres: [ArchiveGenre] = []
    /// The sort of the grid; reset when the grid shows something else than the same category.
    public private(set) var sort: ArchiveSort?

    /// The tab opens on "Pokračovat ve sledování" when the row has an entry, else on the first category (no
    /// category at all: on "Naše nahrávky").
    public init(categories: [ArchiveCategory], continueEntries: Int) {
        self.categories = categories
        if continueEntries > 0 {
            selected = .continueWatching
        } else if let first = categories.first {
            selected = .category(first.id)
        } else {
            selected = .recordings
        }
        sort = grid.sorts.first
    }

    public var items: [Item] {
        switch level {
        case .main:
            return [.search, .continueWatching, .recordings] + categories.map { .category($0.id) }
        case .sub:
            return [.back, .all] + genres.map { .genre($0.id) }
        }
    }

    /// The sub-menu's first item: the category's title with a back arrow ("‹ Filmy"); nil in the main menu.
    public var title: String? {
        guard case .sub(let slug) = level else { return nil }
        return "‹ " + categoryTitle(slug)
    }

    public func title(of item: Item) -> String {
        switch item {
        case .search: return "Hledat"
        case .continueWatching: return ArchiveRules.continueRowTitle
        case .recordings: return "Naše nahrávky"
        case .category(let slug): return categoryTitle(slug)
        case .back: return title ?? "‹"
        case .all: return Self.allTitle
        case .genre(let id): return genres.first { $0.id == id }?.title ?? id
        }
    }

    public var grid: ArchiveGridContent {
        switch (level, selected) {
        case (_, .search): return .search
        case (_, .continueWatching): return .continueWatching
        case (_, .recordings): return .recordings
        case (_, .category(let slug)): return .category(slug, genre: nil)
        case (.sub(let slug), .genre(let id)): return .category(slug, genre: id)
        case (.sub(let slug), _): return .category(slug, genre: nil)
        case (.main, _): return .recordings
        }
    }

    /// The focus rested on an item for `focusDebounce`: the grid shows it, the sub-menu does not open. The back item
    /// filters nothing (the grid stays).
    public mutating func select(_ item: Item) {
        guard items.contains(item), item != .back else { return }
        setSelected(item)
    }

    /// The focus leaves the menu (Right into the grid) before the 0.4 s: the item it was on becomes the selection
    /// first, so the grid and "Left back to the selected item" agree. The back item selects nothing.
    public mutating func focusLeaves(_ item: Item) {
        select(item)
    }

    /// OK on an item: selects it at once; on a category opens its sub-menu with "Všechny" selected (`genres` = the
    /// category's sub-genres when already known, else filled in later by `setGenres`); on the back item = Back.
    @discardableResult
    public mutating func ok(_ item: Item, genres: [ArchiveGenre]? = nil) -> Effect {
        guard items.contains(item) else { return .none }
        switch item {
        case .back:
            return back()
        case .category(let slug):
            let keepSort = grid.slug == slug ? sort : nil
            level = .sub(slug)
            self.genres = genres ?? []
            selected = .all
            sort = keepSort ?? grid.sorts.first
            return .none
        case .search:
            setSelected(item)
            return .focusSearchField
        default:
            setSelected(item)
            return .none
        }
    }

    /// Back/Menu/Return in the menu: the sub-menu returns to the main menu on its category (the whole category in the
    /// grid, the sort kept); the main menu leaves the tab.
    @discardableResult
    public mutating func back() -> Effect {
        guard case .sub(let slug) = level else { return .leave }
        level = .main
        genres = []
        selected = .category(slug)
        return .none
    }

    /// The sub-genres arrived with the category's first page (they are listed only while its sub-menu is open).
    public mutating func setGenres(_ genres: [ArchiveGenre], for slug: String) {
        guard level == .sub(slug), self.genres.isEmpty else { return }
        self.genres = genres
    }

    /// A sort chip: only one the grid offers.
    public mutating func setSort(_ sort: ArchiveSort) {
        guard grid.sorts.contains(sort) else { return }
        self.sort = sort
    }

    private mutating func setSelected(_ item: Item) {
        let before = grid
        selected = item
        // the sort is kept within one category (its sub-genres), reset for anything else
        if before.slug != grid.slug || before.sorts != grid.sorts {
            sort = grid.sorts.first
        }
    }

    private func categoryTitle(_ slug: String) -> String {
        categories.first { $0.id == slug }?.title ?? slug
    }
}

public enum ArchiveSearchBack: Sendable, Equatable {
    /// The search had text: it is cleared, the focus stays.
    case clear
    /// Nothing to clear: back to the menu.
    case toMenu
}

extension ArchiveRules {
    /// Back in the search field or its results first clears the search, then returns to the menu.
    public static func searchBack(text: String) -> ArchiveSearchBack {
        text.isEmpty ? .toMenu : .clear
    }
}
