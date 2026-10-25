import CTCore
import SwiftUI

/// What the Archiv tab pushes: a show page.
enum ArchiveRoute: Hashable {
    case show(id: String)
}

/// The Archiv tab (spec/apps.md "Archiv"): iVysílání and our recordings from the gateway's `/dvb/archive`, as a menu on
/// the left and a grid of what the selected item filters on the right (T23). Grid → show page → player; Back goes the
/// same way back (the player opens over the stack).
struct ArchiveView: View {
    /// Counts the openings of the tab: each one starts at the menu again.
    let opens: Int
    @Environment(AppModel.self) private var model
    @State private var path: [ArchiveRoute] = []

    var body: some View {
        NavigationStack(path: $path) {
            Group {
                if model.savedGateway == nil {
                    ArchiveMessage(text: ArchiveRules.noGatewayText)
                } else if let client = model.archiveClient {
                    ArchiveBrowser(client: client, opens: opens) { path.append($0) }
                } else {
                    // the Tailscale bridge is starting
                    ArchiveMessage(text: TailnetRules.connectingLine)
                }
            }
            .navigationDestination(for: ArchiveRoute.self) { route in
                if let client = model.archiveClient {
                    switch route {
                    case .show(let id):
                        ShowPageView(client: client, id: id)
                    }
                }
            }
        }
        // a new gateway or a new opening of the tab: back to the menu
        .onChange(of: model.gateway) { _, _ in path = [] }
        .onChange(of: opens) { _, _ in path = [] }
        #if DEBUG
        // `-archiveOpen show:ct:1` opens that show page, for simulator checks (more in ArchiveBrowser).
        .task {
            guard let open = UserDefaults.standard.string(forKey: "archiveOpen") else { return }
            if open.hasPrefix("show:") { path = [.show(id: String(open.dropFirst(5)))] }
        }
        #endif
    }
}

private struct ArchiveMessage: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.headline)
            .foregroundStyle(.secondary)
            .multilineTextAlignment(.center)
            .frame(maxWidth: 1100)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .accessibilityIdentifier("archive.message")
    }
}

private struct ArchiveFailure: View {
    let text: String
    /// Without the heading when "Archiv se nepodařilo načíst" is already above the grid.
    var titled = true
    let retry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            if titled { Text(ArchiveRules.loadFailed).font(.headline) }
            Text(text).font(.callout).foregroundStyle(.secondary)
            Button("Zkusit znovu", action: retry)
        }
        .accessibilityIdentifier("archive.failure")
    }
}

// MARK: - menu + grid

/// The categories first (the gateway's own list, no ČT request), then the menu with its grid. Without them the menu
/// has only Hledat / Pokračovat ve sledování / Naše nahrávky and says so above the grid; the next opening asks again.
private struct ArchiveBrowser: View {
    let client: DVBClient
    let opens: Int
    let open: (ArchiveRoute) -> Void

    @Environment(AppModel.self) private var model
    @State private var menu: ArchiveMenu?
    /// The categories could not be read (shown above the grid).
    @State private var categoriesError: String?
    /// A new menu screen (its focus, search and grid state) for every opening.
    @State private var generation = 0
    @State private var attempt = 0
    /// The key of the last finished load.
    @State private var loaded: LoadKey?

    private struct LoadKey: Equatable {
        let gateway: GatewayConfig?
        let reloads: Int
        let attempt: Int
    }

    var body: some View {
        Group {
            if let menu = Binding($menu) {
                ArchiveMenuScreen(client: client, menu: menu, categoriesError: categoriesError, open: open)
                    .id(generation)
            } else {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .onChange(of: opens) { _, _ in
            if categoriesError != nil {
                attempt += 1  // asked again; the menu is rebuilt with the answer
            } else if let categories = menu?.categories {
                start(categories)
            }
        }
        .task(id: LoadKey(gateway: model.gateway, reloads: model.gatewayReloads, attempt: attempt)) {
            // the task runs again when the show page closes: the menu stays as it was
            let key = LoadKey(gateway: model.gateway, reloads: model.gatewayReloads, attempt: attempt)
            guard key != loaded else { return }
            defer { if !Task.isCancelled { loaded = key } }
            do {
                let categories = try await client.archiveCategories()
                categoriesError = nil
                // the same list again (a reload): the menu stays where the viewer is
                if menu?.categories != categories { start(categories) }
            } catch is CancellationError {
            } catch {
                // a reload failing under a working menu leaves it alone
                guard menu?.categories.isEmpty ?? true else { return }
                categoriesError = AppModel.message(for: error)
                start([])
            }
        }
    }

    private func start(_ categories: [ArchiveCategory]) {
        var opened = ArchiveMenu(categories: categories, continueEntries: model.resume.continueRow().count)
        #if DEBUG
        Self.debugOpen(&opened)
        #endif
        menu = opened
        generation += 1
    }

    #if DEBUG
    /// `-archiveOpen category:3947-filmy` opens that sub-menu, `-archiveOpen recordings` / `search` selects that item.
    private static func debugOpen(_ menu: inout ArchiveMenu) {
        guard let open = UserDefaults.standard.string(forKey: "archiveOpen") else { return }
        if open.hasPrefix("category:") { menu.ok(.category(String(open.dropFirst(9)))) }
        if open == "recordings" { menu.ok(.recordings) }
        if open == "search" { menu.select(.search) }
    }
    #endif
}

/// Where the focus is in the tab: a menu item, the search field, the grid (its cards and chips); nil = outside the tab's
/// content (the tab bar).
enum ArchiveFocus: Hashable {
    case menu(ArchiveMenu.Item)
    case searchField
    /// A card or a chip, each its own value: views sharing one value pass the focus through nil between them.
    case grid(String)

    var isGrid: Bool {
        if case .grid = self { return true }
        return false
    }
}

/// The menu on the left, the grid of the selected item on the right, the TMDB line at the bottom when a card in the
/// grid carries TMDb data.
private struct ArchiveMenuScreen: View {
    let client: DVBClient
    @Binding var menu: ArchiveMenu
    /// The categories could not be read: "Archiv se nepodařilo načíst" above the grid.
    let categoriesError: String?
    let open: (ArchiveRoute) -> Void

    @Environment(AppModel.self) private var model
    @FocusState private var focus: ArchiveFocus?
    @State private var paging = ArchivePager()
    @State private var searchText = ""
    /// The sub-genres of each category whose first page came (OK on it lists them at once next time).
    @State private var genres: [String: [ArchiveGenre]] = [:]
    @State private var attempt = 0
    /// Bumped by "Odebrat z řady" so the row is read again.
    @State private var removals = 0
    /// The grid's last finished load.
    @State private var loadedGrid: GridKey?
    /// A show page or the player was opened from the grid.
    @State private var away = false
    /// Where `focusSoon` is moving the focus.
    @State private var pendingFocus: ArchiveFocus?
    /// The last arrow press inside the tab's content (the tab bar's presses do not come here).
    @State private var lastMove: ContinuousClock.Instant?

    private struct GridKey: Equatable {
        let grid: ArchiveGridContent
        let sort: ArchiveSort?
        let query: String?
        let gateway: GatewayConfig?
        let reloads: Int
        let attempt: Int
    }

    var body: some View {
        let grid = menu.grid
        let query = grid == .search ? ArchiveRules.searchQuery(searchText) : nil
        let tmdb = grid != .continueWatching && paging.cards.contains(where: ArchiveRules.showsTMDb)
        VStack(spacing: 0) {
            HStack(alignment: .top, spacing: 20) {
                menuColumn
                gridArea(grid, query: query)
                    .frame(maxWidth: .infinity, alignment: .topLeading)
            }
            if tmdb {
                ArchiveAttribution(text: paging.attribution)
            }
        }
        .onExitCommand(perform: exitAction(grid))
        .onMoveCommand { _ in lastMove = .now }
        .onChange(of: focus) { old, new in
            // nil also while a just-built card takes the focus: only without a recent press inside the tab is it
            // the tab bar; back from the show page or the player the focus returns to its card
            let fromTabBar = old == nil && !away && lastMove.map { ContinuousClock.now - $0 > .seconds(1) } ?? true
            if new != nil { away = false }
            // out of the menu before the 0.4 s (Right into the grid): the item it was on is the selection first
            if case .menu(let item) = old, new?.isGrid == true || new == .searchField {
                menu.focusLeaves(item)
            }
            if fromTabBar, new?.isGrid == true || new == .searchField {
                // down from the tab bar: the tab's focus starts in the menu, on the selected item
                focus = .menu(menu.selected)
            } else if case .menu(let item) = new, item != menu.selected, fromTabBar || old?.isGrid == true || old == .searchField,
                      menu.items.contains(menu.selected) {
                // into the menu from the grid or the tab bar: onto the selected item
                focus = .menu(menu.selected)
            }
        }
        // the grid follows the focus once it rests for 0.4 s (no request per item passed)
        .task(id: focus) {
            guard case .menu(let item) = focus, item != menu.selected else { return }
            do { try await Task.sleep(for: ArchiveMenu.focusDebounce) } catch { return }
            guard pendingFocus == nil else { return }
            menu.select(item)
        }
        .task(id: GridKey(grid: grid, sort: menu.sort, query: query, gateway: model.gateway,
                          reloads: model.gatewayReloads, attempt: attempt)) {
            // the task runs again when the show page or the player closes: the grid and its focus stay
            let key = GridKey(grid: grid, sort: menu.sort, query: query, gateway: model.gateway,
                              reloads: model.gatewayReloads, attempt: attempt)
            guard key != loadedGrid else { return }
            await load(grid, sort: menu.sort, query: query)
            if !Task.isCancelled { loadedGrid = key }
        }
    }

    /// Back: the search clears first, the grid and the field return to the menu, the sub-menu to the main menu; in the
    /// main menu the system's Back (the tab is left as elsewhere).
    private func exitAction(_ grid: ArchiveGridContent) -> (() -> Void)? {
        switch focus {
        case .menu:
            guard case .sub = menu.level else { return nil }
            return {
                menu.back()
                focusSoon(.menu(menu.selected))
            }
        case nil:
            return nil
        case .searchField, .grid:
            return {
                if grid == .search, ArchiveRules.searchBack(text: searchText) == .clear {
                    searchText = ""
                } else {
                    focus = .menu(menu.selected)
                }
            }
        }
    }

    /// After the menu's items changed: once the new item views exist (asked again for a moment, the focus engine may
    /// first put it on the menu's first item; that item is not selected meanwhile).
    private func focusSoon(_ target: ArchiveFocus) {
        pendingFocus = target
        Task { @MainActor in
            for _ in 0..<6 {
                await Task.yield()
                focus = target
                try? await Task.sleep(for: .milliseconds(50))
                if focus == target { break }
            }
            if pendingFocus == target { pendingFocus = nil }
        }
    }

    // MARK: menu

    private var menuColumn: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                ForEach(menu.items, id: \.self) { item in
                    Button { ok(item) } label: {
                        HStack(spacing: 16) {
                            Capsule()
                                .fill(item == menu.selected ? Color.white : Color.clear)
                                .frame(width: 6, height: 34)
                            Text(menu.title(of: item))
                                .font(.callout.weight(item == menu.selected || item == .back ? .bold : .regular))
                                .lineLimit(1)
                            Spacer(minLength: 0)
                        }
                        .frame(width: 300)
                    }
                    .focused($focus, equals: .menu(item))
                    .accessibilityIdentifier("archive.menu.\(item.key)")
                    .accessibilityValue(item == menu.selected ? "selected" : "")
                }
            }
            // room for the focused item's lift inside the clipped scroll view
            .padding(.vertical, 20)
            .padding(.horizontal, 16)
        }
        .frame(width: 380)
        .focusSection()
    }

    private func ok(_ item: ArchiveMenu.Item) {
        let slug: String? = if case .category(let slug) = item { slug } else { nil }
        let level = menu.level
        switch menu.ok(item, genres: slug.flatMap { genres[$0] }) {
        case .focusSearchField:
            focusSoon(.searchField)
        case .leave, .none:
            if menu.level != level { focusSoon(.menu(menu.selected)) }
        }
    }

    // MARK: grid

    @ViewBuilder
    private func gridArea(_ grid: ArchiveGridContent, query: String?) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 30) {
                if categoriesError != nil {
                    Text(ArchiveRules.loadFailed).font(.headline).foregroundStyle(.secondary)
                        .accessibilityIdentifier("archive.categoriesFailed")
                }
                switch grid {
                case .continueWatching:
                    continueGrid
                case .search:
                    TextField(ArchiveRules.searchHint, text: $searchText)
                        .frame(width: 900)
                        .focused($focus, equals: .searchField)
                        .accessibilityIdentifier("archive.search")
                    if query != nil { pagedCards(grid) }
                case .recordings, .category:
                    sortChips(grid)
                    pagedCards(grid)
                }
            }
            // room for the focused card's lift inside the clipped scroll view (the TMDB line stays readable)
            .padding(.vertical, 24)
            .padding(.horizontal, 20)
        }
        .focusSection()
    }

    private func sortChips(_ grid: ArchiveGridContent) -> some View {
        HStack(spacing: 20) {
            ForEach(grid.sorts, id: \.self) { sort in
                let selected = menu.sort == sort
                Button { menu.setSort(sort) } label: {
                    Text(grid.sortTitle(sort)).font(.callout.weight(selected ? .bold : .regular))
                }
                .overlay(alignment: .bottom) {
                    if selected { Capsule().fill(.white).frame(height: 4).padding(.horizontal, 24).offset(y: 10) }
                }
                .focused($focus, equals: .grid("sort.\(sort.rawValue)"))
                .accessibilityIdentifier("archive.sort.\(sort.rawValue)")
            }
        }
        .padding(.vertical, 8)
        .focusSection()
    }

    @ViewBuilder
    private func pagedCards(_ grid: ArchiveGridContent) -> some View {
        if let notice = paging.notice {
            Text(notice).font(.headline).foregroundStyle(.secondary).accessibilityIdentifier("archive.notice")
        }
        if let failure = paging.failure {
            ArchiveFailure(text: failure, titled: categoriesError == nil) { attempt += 1 }
        } else if paging.cards.isEmpty {
            if paging.loaded {
                Text(grid.emptyText).font(.headline).foregroundStyle(.secondary).accessibilityIdentifier("archive.empty")
            } else {
                ProgressView().frame(maxWidth: .infinity)
            }
        } else {
            ArchiveGrid(cards: paging.cards, focus: $focus, open: { away = true; open($0) }, nearEnd: paging.loadMore).equatable()
        }
    }

    /// "Pokračovat ve sledování": this TV's resume entries; OK plays from the saved position, long OK removes.
    @ViewBuilder
    private var continueGrid: some View {
        let _ = model.playerCloses  // a closed player saved a new position
        let _ = removals
        let entries = model.resume.continueRow()
        if entries.isEmpty {
            Text(ArchiveGridContent.continueWatching.emptyText).font(.headline).foregroundStyle(.secondary)
                .accessibilityIdentifier("archive.empty")
        } else {
            LazyVGrid(columns: ArchiveGrid.columns, alignment: .leading, spacing: ArchiveGrid.spacing) {
                ForEach(entries, id: \.key) { item in
                    Button {
                        away = true
                        model.playContinue(key: item.key, entry: item.entry)
                    } label: {
                        ContinueCardView(entry: item.entry).equatable()
                    }
                    .buttonStyle(.card)
                    .focused($focus, equals: .grid(item.key))
                    .contextMenu {
                        Button(ArchiveRules.removeFromRow, role: .destructive) {
                            model.resume.remove(item.key)
                            removals += 1
                        }
                    }
                    .accessibilityIdentifier("archive.continue.\(item.key)")
                }
            }
        }
    }

    private func load(_ grid: ArchiveGridContent, sort: ArchiveSort?, query: String?) async {
        switch grid {
        case .continueWatching:
            paging.reset()
        case .search:
            // results after 0.5 s without typing, at least 2 characters
            paging.reset()
            guard let query else { return }
            do { try await Task.sleep(for: ArchiveRules.searchDebounce) } catch { return }
            let client = client
            await paging.load { page in try await client.archiveSearch(query, page: page) }
        case .recordings, .category:
            guard let slug = grid.slug else { return }
            let genre: String? = if case .category(_, let genre) = grid { genre } else { nil }
            let client = client, sort = sort ?? grid.sorts.first ?? .popular
            await paging.load { page in try await client.archiveCategory(slug, sort: sort, genre: genre, page: page) }
            if case .category = grid, genre == nil, !paging.genres.isEmpty {
                genres[slug] = paging.genres
                menu.setGenres(paging.genres, for: slug)
            }
        }
    }
}

/// Pages of cards (a search or a category): the first page, then more as the focus nears the end. A new `load`
/// drops answers still on their way for the previous one.
@MainActor
@Observable
final class ArchivePager {
    typealias Fetch = (Int) async throws -> ArchivePage

    private(set) var cards: [ArchiveCard] = []
    private(set) var genres: [ArchiveGenre] = []
    private(set) var attribution = ProgrammeDetails.defaultAttribution
    private(set) var loaded = false
    /// The gateway's own text in an answer ("iVysílání teď neodpovídá"; our recordings still listed).
    private(set) var notice: String?
    /// The first page could not be loaded.
    private(set) var failure: String?
    @ObservationIgnored private var nextPage: Int?
    @ObservationIgnored private var fetch: Fetch?
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var loadingMore = false

    func reset() {
        generation += 1
        cards = []
        genres = []
        loaded = false
        notice = nil
        failure = nil
        nextPage = nil
        fetch = nil
        loadingMore = false
    }

    func load(_ fetch: @escaping Fetch) async {
        reset()
        self.fetch = fetch
        let generation = generation
        do {
            let page = try await fetch(1)
            guard generation == self.generation else { return }
            apply(page)
            notice = page.error
        } catch is CancellationError {
            return
        } catch {
            guard generation == self.generation else { return }
            failure = AppModel.message(for: error)
        }
        loaded = true
    }

    /// The next page, if there is one and none is on its way.
    func loadMore() {
        guard !loadingMore, let next = nextPage, let fetch else { return }
        loadingMore = true
        let generation = generation
        Task {
            let page = try? await fetch(next)
            guard generation == self.generation else { return }
            loadingMore = false
            if let page { apply(page) }
        }
    }

    private func apply(_ page: ArchivePage) {
        let known = Set(cards.map(\.id))
        cards += page.cards.filter { !known.contains($0.id) }
        if genres.isEmpty { genres = page.genres }
        attribution = page.attribution
        nextPage = page.nextPage
    }
}

// MARK: - cards

/// A card: landscape image, title, "2021 · film", "★ 7,4", source icons. Equatable: a focus move or another card's
/// image does not redraw it.
struct ArchiveCardView: View, Equatable {
    static let size = CGSize(width: 400, height: 225 + 104)
    let card: ArchiveCard

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ArchiveImageView(image: card.image, symbol: card.source == .rec ? "antenna.radiowaves.left.and.right" : "film")
                .frame(width: Self.size.width, height: 225)
                .clipped()
            VStack(alignment: .leading, spacing: 6) {
                Text(card.title).font(.headline).lineLimit(1)
                HStack(spacing: 14) {
                    Text(ArchiveRules.metaLine(card))
                    if let badge = ArchiveRules.ratingBadge(card.rating) {
                        Text(badge).accessibilityIdentifier("archive.rating")
                    }
                    Spacer(minLength: 0)
                    ForEach(ArchiveRules.icons(card.source), id: \.self) { SourceIcon(source: $0) }
                }
                .font(.system(size: 24))
                .foregroundStyle(.secondary)
                .lineLimit(1)
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 12)
        }
        .frame(width: Self.size.width, height: Self.size.height, alignment: .topLeading)
    }
}

private struct ContinueCardView: View, Equatable {
    let entry: ResumeEntry

    var body: some View {
        let card = entry.card
        let main = (card?.showTitle.isEmpty ?? true) ? card?.title ?? "" : card?.showTitle ?? ""
        let second = (card?.showTitle.isEmpty ?? true) ? "" : card?.title ?? ""
        VStack(alignment: .leading, spacing: 0) {
            ZStack(alignment: .bottom) {
                ArchiveImageView(image: card?.image.flatMap(ArchiveImage.init),
                                 symbol: card?.source == .dvb ? "antenna.radiowaves.left.and.right" : "film")
                    .frame(width: ArchiveCardView.size.width, height: 225)
                    .clipped()
                ProgressView(value: entry.lengthSeconds > 0 ? min(1, entry.progressSeconds / entry.lengthSeconds) : 0)
                    .tint(.white)
                    .padding(.horizontal, 12)
                    .padding(.bottom, 10)
            }
            VStack(alignment: .leading, spacing: 6) {
                Text(main).font(.headline).lineLimit(1)
                HStack(spacing: 14) {
                    Text(second.isEmpty ? ResumeRules.buttonTitle(entry.progressSeconds) : second)
                    Spacer(minLength: 0)
                    SourceIcon(source: card?.source == .dvb ? .dvb : .ct)
                }
                .font(.system(size: 24))
                .foregroundStyle(.secondary)
                .lineLimit(1)
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 12)
        }
        .frame(width: ArchiveCardView.size.width, height: ArchiveCardView.size.height, alignment: .topLeading)
    }
}

/// A card image from the archive's memory-only store; the placeholder until it is there.
struct ArchiveImageView: View {
    let image: ArchiveImage?
    let symbol: String
    @Environment(AppModel.self) private var model
    @State private var loaded: UIImage?

    private struct Key: Equatable {
        let image: ArchiveImage?
        let thumbnails: Bool
    }

    var body: some View {
        ZStack {
            if let shown = loaded ?? image.flatMap(model.archiveImages.cached) {
                Image(uiImage: shown).resizable().aspectRatio(contentMode: .fill)
            } else {
                Color(white: 0.15)
                Image(systemName: symbol).font(.system(size: 44)).foregroundStyle(.secondary)
            }
        }
        // a recording's still waits for the gateway's and the TV's thumbnail switches (asked again when they change)
        .task(id: Key(image: image, thumbnails: model.images.gatewayAllows && model.images.tvAllows)) {
            loaded = nil
            guard let image else { return }
            loaded = await model.archiveImages.image(image)
        }
    }
}

/// The grid of cards, 3 columns, built lazily; `nearEnd` asks for the next page when the last cards are built.
/// Equatable on the cards: a focus move in the menu or the grid does not rebuild it.
struct ArchiveGrid: View, Equatable {
    static let spacing: CGFloat = 40
    static let columns = Array(repeating: GridItem(.fixed(ArchiveCardView.size.width), spacing: spacing), count: 3)

    let cards: [ArchiveCard]
    /// The tab's focus: the cards report `.grid(id)` (Back to the menu, the tab bar told apart from the grid).
    let focus: FocusState<ArchiveFocus?>.Binding
    let open: (ArchiveRoute) -> Void
    let nearEnd: () -> Void

    nonisolated static func == (lhs: Self, rhs: Self) -> Bool { lhs.cards == rhs.cards }

    var body: some View {
        LazyVGrid(columns: Self.columns, alignment: .leading, spacing: 50) {
            ForEach(Array(cards.enumerated()), id: \.element.id) { index, card in
                Button { open(.show(id: card.id)) } label: { ArchiveCardView(card: card).equatable() }
                    .buttonStyle(.card)
                    .focused(focus, equals: .grid(card.id))
                    .accessibilityIdentifier("archive.card.\(card.id)")
                    .accessibilityValue(String(index))
                    .onAppear { if index >= cards.count - 9 { nearEnd() } }
            }
        }
    }
}

/// TMDb's terms on the archive screens: their logo and sentence, small, at the bottom.
struct ArchiveAttribution: View {
    let text: String

    var body: some View {
        HStack(spacing: 14) {
            TMDBLogo()
            Text(text)
                .font(.system(size: 18))
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 20)
        .padding(.vertical, 10)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("archive.attribution")
    }
}
