import CTCore
import SwiftUI

/// What the Archiv tab pushes: a whole category or a show page.
enum ArchiveRoute: Hashable {
    case category(slug: String, title: String)
    case show(id: String)
}

/// The Archiv tab (spec/apps.md "Archiv (T13)"): iVysílání and our recordings from the gateway's `/dvb/archive`.
/// Home → category / show page → player; Back goes the same way back (the player opens over the stack).
struct ArchiveView: View {
    @Environment(AppModel.self) private var model
    @State private var path: [ArchiveRoute] = []

    var body: some View {
        NavigationStack(path: $path) {
            Group {
                if model.savedGateway == nil {
                    ArchiveMessage(text: ArchiveRules.noGatewayText)
                } else if let client = model.archiveClient {
                    ArchiveHomeView(client: client) { path.append($0) }
                } else {
                    // the Tailscale bridge is starting
                    ArchiveMessage(text: TailnetRules.connectingLine)
                }
            }
            .navigationDestination(for: ArchiveRoute.self) { route in
                if let client = model.archiveClient {
                    switch route {
                    case .category(let slug, let title):
                        ArchiveCategoryView(client: client, slug: slug, title: title) { path.append($0) }
                    case .show(let id):
                        ShowPageView(client: client, id: id)
                    }
                }
            }
        }
        // a new gateway: back to the home of its archive
        .onChange(of: model.gateway) { _, _ in path = [] }
        #if DEBUG
        // `-archiveOpen show:ct:1` / `-archiveOpen category:3947-filmy` opens that screen, for simulator checks.
        .task {
            guard let open = UserDefaults.standard.string(forKey: "archiveOpen") else { return }
            if open.hasPrefix("show:") { path = [.show(id: String(open.dropFirst(5)))] }
            if open.hasPrefix("category:") { path = [.category(slug: String(open.dropFirst(9)), title: "")] }
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

// MARK: - home

/// Search field, "Pokračovat ve sledování", then the gateway's rows ("Naše nahrávky" and the categories).
private struct ArchiveHomeView: View {
    let client: DVBClient
    let open: (ArchiveRoute) -> Void

    @Environment(AppModel.self) private var model
    @State private var home: ArchiveHome?
    @State private var error: String?
    @State private var searchText = ""
    @State private var search = ArchivePager()
    /// Bumped by "Odebrat z řady" so the row is read again.
    @State private var removals = 0

    private struct LoadKey: Equatable {
        let gateway: GatewayConfig?
        let reloads: Int
        let attempt: Int
    }

    @State private var attempt = 0

    var body: some View {
        let _ = model.playerCloses  // a closed player saved a new position
        let _ = removals
        let continueRow = model.resume.continueRow()
        let query = ArchiveRules.searchQuery(searchText)
        let tmdb = query == nil
            ? home?.rows.contains { $0.cards.contains(where: ArchiveRules.showsTMDb) } ?? false
            : search.cards.contains(where: ArchiveRules.showsTMDb)
        VStack(spacing: 0) {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 44) {
                    TextField(ArchiveRules.searchHint, text: $searchText)
                        .frame(width: 900)
                        .accessibilityIdentifier("archive.search")
                    if query != nil {
                        searchResults
                    } else {
                        if !continueRow.isEmpty {
                            ContinueRowView(entries: continueRow) { removals += 1 }
                        }
                        if let home {
                            if let error = home.error {
                                Text(error).font(.headline).foregroundStyle(.secondary)
                            }
                            if home.rows.isEmpty && continueRow.isEmpty && home.error == nil {
                                Text(ArchiveRules.emptyText).font(.headline).foregroundStyle(.secondary)
                            }
                            ForEach(home.rows) { row in
                                ArchiveRowView(row: row, open: open)
                            }
                        } else if let error {
                            failure(error)
                        } else {
                            ProgressView().frame(maxWidth: .infinity)
                        }
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 30)
            }
            if tmdb {
                ArchiveAttribution(text: home?.attribution ?? ProgrammeDetails.defaultAttribution)
            }
        }
        // Back on search results clears the search first (else the system's Back)
        .onExitCommand(perform: query == nil ? nil : { searchText = "" })
        .task(id: LoadKey(gateway: model.gateway, reloads: model.gatewayReloads, attempt: attempt)) {
            do {
                let loaded = try await client.archiveHome()
                home = loaded
                error = nil
            } catch is CancellationError {
            } catch {
                if home == nil { self.error = AppModel.message(for: error) }
            }
        }
        .task(id: query) {
            // results after 0.5 s without typing, at least 2 characters
            search.reset()
            guard let query else { return }
            do { try await Task.sleep(for: ArchiveRules.searchDebounce) } catch { return }
            await search.load { page in try await client.archiveSearch(query, page: page) }
        }
    }

    @ViewBuilder
    private var searchResults: some View {
        if let error = search.error {
            Text(error).font(.headline).foregroundStyle(.secondary)
        }
        if search.cards.isEmpty {
            if search.error != nil {
            } else if search.loaded {
                Text(ArchiveRules.nothingFound).font(.headline).foregroundStyle(.secondary)
            } else {
                ProgressView().frame(maxWidth: .infinity)
            }
        } else {
            ArchiveGrid(cards: search.cards, open: open) { search.loadMore() }
        }
    }

    private func failure(_ text: String) -> some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(ArchiveRules.loadFailed).font(.headline)
            Text(text).font(.callout).foregroundStyle(.secondary)
            Button("Zkusit znovu") { attempt += 1 }
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
    private(set) var title = ""
    private(set) var attribution = ProgrammeDetails.defaultAttribution
    private(set) var loaded = false
    private(set) var error: String?
    @ObservationIgnored private var nextPage: Int?
    @ObservationIgnored private var fetch: Fetch?
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var loadingMore = false

    func reset() {
        generation += 1
        cards = []
        genres = []
        title = ""
        loaded = false
        error = nil
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
            error = page.error
        } catch is CancellationError {
            return
        } catch {
            guard generation == self.generation else { return }
            self.error = AppModel.message(for: error)
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
        if !page.title.isEmpty { title = page.title }
        attribution = page.attribution
        nextPage = page.nextPage
    }
}

// MARK: - rows

/// One row of cards, built lazily; its end card opens the whole category.
private struct ArchiveRowView: View, Equatable {
    let row: ArchiveRow
    let open: (ArchiveRoute) -> Void

    nonisolated static func == (lhs: Self, rhs: Self) -> Bool { lhs.row == rhs.row }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(row.title).font(.title3.weight(.semibold))
            ScrollView(.horizontal) {
                LazyHStack(spacing: 40) {
                    ForEach(row.cards) { card in
                        Button { open(.show(id: card.id)) } label: { ArchiveCardView(card: card).equatable() }
                            .buttonStyle(.card)
                            .accessibilityIdentifier("archive.card.\(card.id)")
                    }
                    Button { open(.category(slug: row.id, title: row.title)) } label: {
                        VStack(spacing: 14) {
                            Image(systemName: "chevron.right").font(.system(size: 48, weight: .semibold))
                            Text(ArchiveRules.allGenres).font(.headline)
                        }
                        .frame(width: 200, height: ArchiveCardView.size.height)
                    }
                    .buttonStyle(.card)
                    .accessibilityIdentifier("archive.more.\(row.id)")
                }
                .padding(.vertical, 24)
                .padding(.horizontal, 4)
            }
            .scrollClipDisabled()
        }
        .focusSection()
    }
}

/// "Pokračovat ve sledování": this TV's resume entries; OK plays from the saved position, long OK removes.
private struct ContinueRowView: View {
    let entries: [(key: String, entry: ResumeEntry)]
    let removed: () -> Void
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(ArchiveRules.continueRowTitle).font(.title3.weight(.semibold))
            ScrollView(.horizontal) {
                LazyHStack(spacing: 40) {
                    ForEach(entries, id: \.key) { item in
                        Button { model.playContinue(key: item.key, entry: item.entry) } label: {
                            ContinueCardView(entry: item.entry).equatable()
                        }
                        .buttonStyle(.card)
                        .contextMenu {
                            Button(ArchiveRules.removeFromRow, role: .destructive) {
                                model.resume.remove(item.key)
                                removed()
                            }
                        }
                        .accessibilityIdentifier("archive.continue.\(item.key)")
                    }
                }
                .padding(.vertical, 24)
                .padding(.horizontal, 4)
            }
            .scrollClipDisabled()
        }
        .focusSection()
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

/// A grid of cards, built lazily; `nearEnd` asks for the next page when the last cards are built.
struct ArchiveGrid: View {
    let cards: [ArchiveCard]
    let open: (ArchiveRoute) -> Void
    let nearEnd: () -> Void

    private let columns = Array(repeating: GridItem(.fixed(ArchiveCardView.size.width), spacing: 40), count: 4)

    var body: some View {
        LazyVGrid(columns: columns, alignment: .leading, spacing: 50) {
            ForEach(Array(cards.enumerated()), id: \.element.id) { index, card in
                Button { open(.show(id: card.id)) } label: { ArchiveCardView(card: card).equatable() }
                    .buttonStyle(.card)
                    .accessibilityIdentifier("archive.grid.\(index)")
                    .onAppear { if index >= cards.count - 8 { nearEnd() } }
            }
        }
        .focusSection()
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

// MARK: - category

/// A whole category: sub-genre chips and the sort on top, a paged grid below.
private struct ArchiveCategoryView: View {
    let client: DVBClient
    let slug: String
    let title: String
    let open: (ArchiveRoute) -> Void

    @State private var sort: ArchiveSort = .popular
    @State private var genre: String?
    @State private var paging = ArchivePager()
    /// The sub-genres of the whole category (from its first page without a genre).
    @State private var genres: [ArchiveGenre] = []

    private struct Key: Equatable {
        let sort: ArchiveSort
        let genre: String?
    }

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 30) {
                    Text(paging.title.isEmpty ? title : paging.title).font(.title2.weight(.bold))
                    if !genres.isEmpty {
                        ScrollView(.horizontal) {
                            HStack(spacing: 20) {
                                chip(ArchiveRules.allGenres, selected: genre == nil) { genre = nil }
                                ForEach(genres) { item in
                                    chip(item.title, selected: genre == item.id) { genre = item.id }
                                }
                            }
                            .padding(.vertical, 12)
                        }
                        .scrollClipDisabled()
                        .focusSection()
                    }
                    HStack(spacing: 20) {
                        ForEach(ArchiveSort.allCases, id: \.self) { item in
                            chip(item.title, selected: sort == item) { sort = item }
                                .accessibilityIdentifier("archive.sort.\(item.rawValue)")
                        }
                    }
                    .focusSection()
                    if let error = paging.error {
                        Text(error).font(.headline).foregroundStyle(.secondary)
                    }
                    if paging.cards.isEmpty {
                        if paging.error != nil {
                        } else if paging.loaded {
                            Text(ArchiveRules.nothingFound).font(.headline).foregroundStyle(.secondary)
                        } else {
                            ProgressView().frame(maxWidth: .infinity)
                        }
                    } else {
                        ArchiveGrid(cards: paging.cards, open: open) { paging.loadMore() }
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 30)
            }
            if paging.cards.contains(where: ArchiveRules.showsTMDb) {
                ArchiveAttribution(text: paging.attribution)
            }
        }
        .task(id: Key(sort: sort, genre: genre)) {
            let sort = sort, genre = genre, client = client, slug = slug
            await paging.load { page in try await client.archiveCategory(slug, sort: sort, genre: genre, page: page) }
            if genre == nil, !paging.genres.isEmpty { genres = paging.genres }
        }
    }

    private func chip(_ text: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(text).font(.callout.weight(selected ? .bold : .regular))
        }
        .overlay(alignment: .bottom) {
            if selected { Capsule().fill(.white).frame(height: 4).padding(.horizontal, 24).offset(y: 10) }
        }
    }
}
