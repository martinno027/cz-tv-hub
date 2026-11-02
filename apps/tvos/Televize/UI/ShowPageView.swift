import CTCore
import SwiftUI

/// A show of the Archiv tab (spec/apps.md "Archiv": the programme page of T15 extended for shows): image, title,
/// facts, TMDb rating, description, creators; a film gets the T15 buttons, a series season chips, for a long list
/// year and month chips (T32), and its episodes,
/// each `Přehrát` / `Pokračovat od …`; then the group "Nahráno z antény". The player opens over the page and Back
/// returns here; the offers are read again when it closes.
struct ShowPageView: View {
    let client: DVBClient
    let id: String
    /// The card's title, shown at once while the page loads (T52); nil = only the loader.
    var knownTitle: String? = nil

    @Environment(AppModel.self) private var model
    @State private var show: ArchiveShow?
    @State private var error: String?
    /// What the viewer picked (season, year, month); nil = the gateway's first groups as they came. Kept while the
    /// page is open, also across the player.
    @State private var query: ArchiveEpisodeQuery?
    /// The year/month chips of the listed episodes (T32), as the gateway last answered; nil = no chips.
    @State private var periods: ArchivePeriods?
    @State private var seasonEpisodes: [ArchiveEpisode]?
    @State private var nextPage: Int?
    /// The page on its way (T52: the loader in place of the page).
    @State private var opening = LoadingState()
    @State private var openingLoader = false
    /// Page 1 of a season/year/month pick on its way: the loader over the episode list, the old list dimmed; a
    /// failing pick puts back the list shown (not a pick replaced while waited for).
    @State private var picking = ChoiceLoad<Listed>()
    @State private var pickingLoader = false
    /// A next page of episodes on its way: the small loader below the last episode.
    @State private var more = LoadingState()
    /// A show without season chips starts with the focus on the selected year (once per page).
    @FocusState private var focusedYear: Int?
    @State private var focusedFirstYear = false
    @State private var attempt = 0

    private struct Key: Equatable {
        let gateway: GatewayConfig?
        let attempt: Int
    }

    var body: some View {
        let _ = model.playerCloses  // a closed player saved a new position
        Group {
            if let show {
                content(show)
            } else if let error {
                VStack(spacing: 24) {
                    Text(error).font(.headline).foregroundStyle(.secondary)
                    Button("Zkusit znovu") { attempt += 1 }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .dimmedWhileLoading(openingLoader)
                .overlay { if openingLoader { LoaderRing() } }
            } else {
                openingPlaceholder
            }
        }
        .loaderDelay(opening, shown: $openingLoader)
        .loaderDelay(picking.loading, shown: $pickingLoader)
        .task(id: Key(gateway: model.gateway, attempt: attempt)) {
            let ticket = opening.begin()
            do {
                let loaded = try await client.archiveShow(id)
                guard opening.finish(ticket) else { return }
                show = loaded
                if query == nil {
                    let first = loaded.groups.first { !$0.isRecordings }
                    nextPage = first?.nextPage
                    periods = first?.periods
                }
                if !focusedFirstYear, loaded.seasons.count <= 1, loaded.card.kind != .movie, let year = periods?.year {
                    focusedFirstYear = true
                    // after the chips are laid out
                    try? await Task.sleep(for: .milliseconds(150))
                    focusedYear = year
                }
                error = nil
            } catch {
                guard !(error is CancellationError || Task.isCancelled), opening.finish(ticket) else { return }
                self.error = AppModel.message(for: error)
            }
        }
    }

    /// Opening the page: the card's title where the page's stands, the loader in place of the rest (after 150 ms).
    private var openingPlaceholder: some View {
        VStack(alignment: .leading, spacing: 40) {
            if let knownTitle, !knownTitle.isEmpty {
                Text(knownTitle).font(.title2.weight(.bold)).lineLimit(2)
                    .accessibilityIdentifier("show.loadingTitle")
            }
            ZStack { if openingLoader { LoaderRing() } }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 40)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    // MARK: content

    private func content(_ show: ArchiveShow) -> some View {
        let card = show.card
        let ctGroups = show.groups.filter { !$0.isRecordings }
        let recGroups = show.groups.filter(\.isRecordings)
        return VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 40) {
                    HStack(alignment: .top, spacing: 60) {
                        ArchiveImageView(image: card.image, symbol: card.source == .rec ? "antenna.radiowaves.left.and.right" : "film")
                            .frame(width: ProgrammePageView.imageSize.width, height: ProgrammePageView.imageSize.height)
                            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                        VStack(alignment: .leading, spacing: 14) {
                            Text(card.title).font(.title2.weight(.bold)).lineLimit(2)
                            HStack(spacing: 10) {
                                ForEach(ArchiveRules.icons(card.source), id: \.self) { SourceIcon(source: $0) }
                                Text(ArchiveRules.showFacts(show)).lineLimit(1)
                            }
                            .font(.headline)
                            .foregroundStyle(.secondary)
                            if let rating = card.rating,
                               let line = ProgrammePage.ratingLine(value: rating.value, votes: rating.votes) {
                                Label(line, systemImage: "star.fill")
                                    .font(.headline)
                                    .accessibilityIdentifier("show.rating")
                            }
                            if !show.description.isEmpty {
                                Text(show.description)
                                    .font(.system(size: 28))
                                    .foregroundStyle(.secondary)
                                    .lineLimit(7)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            if let creators = ArchiveRules.creatorsLine(show.creators) {
                                Text(creators)
                                    .font(.callout)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(2)
                            }
                            if card.kind == .movie {
                                filmButtons(show, ctGroups: ctGroups, recGroups: recGroups)
                                    .padding(.top, 16)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    if let error = show.error {
                        Text(error).font(.headline).foregroundStyle(.secondary)
                    }
                    if card.kind != .movie {
                        if show.seasons.count > 1 { seasonChips(show) }
                        periodChips(show)
                        let episodes = seasonEpisodes ?? ctGroups.flatMap(\.episodes)
                        // a pick on its way: the list shown stays, dimmed, the loader over its top (T52)
                        Group {
                            if !episodes.isEmpty {
                                episodeList(title: seasonEpisodes == nil ? ctGroups.first?.title ?? "" : "",
                                            episodes: episodes, show: card, pages: true)
                            } else if picking.loading.isWaiting {
                                Color.clear.frame(height: 200)
                            }
                        }
                        .dimmedWhileLoading(pickingLoader)
                        .overlay(alignment: .top) {
                            if pickingLoader { LoaderRing().padding(.top, 60) }
                        }
                        NextPageLoader(state: more)
                    }
                    ForEach(recGroups, id: \.title) { group in
                        episodeList(title: group.title.isEmpty ? ArchiveRules.recordingsGroupTitle : group.title,
                                    episodes: group.episodes, show: card, pages: false)
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 40)
            }
            if ArchiveRules.showsTMDb(card) {
                ArchiveAttribution(text: show.attribution)
            }
        }
    }

    /// A film: [Pokračovat od …] Přehrát, Zpět (as T15), for its iVysílání item first, else our newest recording.
    @ViewBuilder
    private func filmButtons(_ show: ArchiveShow, ctGroups: [ArchiveEpisodeGroup], recGroups: [ArchiveEpisodeGroup]) -> some View {
        let episode = ctGroups.first?.episodes.first ?? recGroups.first?.episodes.first
        let resumeAt = episode.flatMap { model.resumeOffer(key: $0.resumeKey, length: $0.durationSeconds) }
        HStack(spacing: 24) {
            if let episode {
                ForEach(ArchiveRules.filmButtons(resume: resumeAt != nil), id: \.self) { button in
                    Button(button == .resume ? ResumeRules.buttonTitle(resumeAt ?? 0) : "Přehrát") {
                        model.playArchive(episode, show: show.card, resumeAt: button == .resume ? resumeAt : nil)
                    }
                    .opacity(episode.playable ? 1 : 0.5)
                    .accessibilityIdentifier("show.\(button.rawValue)")
                }
            }
            BackButton()
        }
        .focusSection()
    }

    private func seasonChips(_ show: ArchiveShow) -> some View {
        let selected = baseQuery(show).season
        return chipRow {
            ForEach(show.seasons) { item in
                chip(item.title, selected: selected == item.id, identifier: "show.season.\(item.id)") {
                    select(item.id, show: show)
                }
            }
        }
    }

    /// T32: the year row and, under it, the months of the selected year; the selection shows at once, the month
    /// row of another year when the gateway answered with its months. A year shown whole (T51: `months` empty,
    /// at most 100 episodes) has no month row.
    @ViewBuilder
    private func periodChips(_ show: ArchiveShow) -> some View {
        if let periods {
            let year = query?.year ?? periods.year
            let shown = ArchivePeriods(years: periods.years, year: year, months: periods.months,
                                       month: query?.month ?? periods.month)
            let chips = ArchivePeriodRules.chips(shown)
            VStack(alignment: .leading, spacing: 4) {
                chipRow {
                    ForEach(chips.years) { item in
                        chip(item.label, selected: item.selected, identifier: "show.year.\(item.value)") {
                            selectYear(item.value, show: show)
                        }
                        .focused($focusedYear, equals: item.value)
                    }
                }
                if periods.year == year, !chips.months.isEmpty {
                    chipRow {
                        ForEach(chips.months) { item in
                            chip(item.label, selected: item.selected, identifier: "show.month.\(item.value)") {
                                selectMonth(item.value, show: show)
                            }
                        }
                    }
                }
            }
        }
    }

    private func chipRow(@ViewBuilder _ content: () -> some View) -> some View {
        ScrollView(.horizontal) {
            HStack(spacing: 20) { content() }
                .padding(.vertical, 12)
        }
        .scrollClipDisabled()
        .focusSection()
    }

    private func chip(_ title: String, selected: Bool, identifier: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).font(.callout.weight(selected ? .bold : .regular))
        }
        .overlay(alignment: .bottom) {
            if selected {
                Capsule().fill(.white).frame(height: 4).padding(.horizontal, 24).offset(y: 10)
            }
        }
        .accessibilityIdentifier(identifier)
    }

    private func episodeList(title: String, episodes: [ArchiveEpisode], show: ArchiveCard, pages: Bool) -> some View {
        let now = Date()
        return VStack(alignment: .leading, spacing: 16) {
            if !title.isEmpty { Text(title).font(.title3.weight(.semibold)) }
            LazyVStack(alignment: .leading, spacing: 14) {
                ForEach(Array(episodes.enumerated()), id: \.element.id) { index, episode in
                    let resumeAt = model.resumeOffer(key: episode.resumeKey, length: episode.durationSeconds)
                    Button {
                        model.playArchive(episode, show: show, resumeAt: resumeAt)
                    } label: {
                        HStack(spacing: 24) {
                            if case .rec = episode.source {
                                SourceIcon(source: .dvb).foregroundStyle(.secondary)
                            }
                            Text(ArchiveRules.episodeLine(episode, now: now)).lineLimit(1)
                            Spacer(minLength: 20)
                            Text(ArchiveRules.episodeButton(resumeAt: resumeAt)).foregroundStyle(.secondary)
                        }
                        .font(.headline)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    // not playable: dimmed; playing it shows ČT's text
                    .opacity(episode.playable ? 1 : 0.45)
                    .contextMenu {
                        if resumeAt != nil {
                            Button(ArchiveRules.fromStart) { model.playArchive(episode, show: show, resumeAt: nil) }
                        }
                    }
                    .accessibilityIdentifier("show.episode.\(episode.resumeKey)")
                    .onAppear { if pages, index >= episodes.count - 5 { loadMore() } }
                }
            }
        }
        .focusSection()
    }

    // MARK: seasons, years and months

    /// The season the show came with: its first group's, else the first chip (nil: a show without seasons).
    private func currentSeason(_ show: ArchiveShow) -> String? {
        show.groups.first { !$0.isRecordings }?.season ?? show.seasons.first?.id
    }

    /// What is listed now: the viewer's pick, else the show as it came.
    private func baseQuery(_ show: ArchiveShow) -> ArchiveEpisodeQuery {
        query ?? ArchiveEpisodeQuery(season: currentSeason(show))
    }

    private func select(_ id: String, show: ArchiveShow) {
        let base = baseQuery(show)
        guard id != base.season else { return }
        reload(base.selectingSeason(id), keepPeriods: false)  // another season has its own years
    }

    private func selectYear(_ year: Int, show: ArchiveShow) {
        let base = baseQuery(show)
        guard year != (base.year ?? periods?.year) else { return }
        reload(base.selectingYear(year))
    }

    private func selectMonth(_ month: Int, show: ArchiveShow) {
        guard let periods, periods.year == (query?.year ?? periods.year),
              month != (query?.month ?? periods.month) else { return }
        reload(baseQuery(show).selectingMonth(month, of: periods))
    }

    /// What the list showed before a new pick, put back when the pick fails to load.
    private struct Listed {
        let query: ArchiveEpisodeQuery?
        let periods: ArchivePeriods?
        let episodes: [ArchiveEpisode]?
        let nextPage: Int?
    }

    /// Page 1 of a new pick replaces the list once it comes; until then the list shown stays, dimmed under the
    /// loader (T52), and does not page. A newer pick replaces the waited one (its answer is dropped).
    private func reload(_ next: ArchiveEpisodeQuery, keepPeriods: Bool = true) {
        let ticket = picking.begin(over: Listed(query: query, periods: periods, episodes: seasonEpisodes, nextPage: nextPage))
        query = next
        if !keepPeriods { periods = nil }
        nextPage = nil
        more.cancel()
        Task { await loadPick(next, ticket: ticket) }
    }

    private func loadPick(_ request: ArchiveEpisodeQuery, ticket: Int) async {
        let loaded: ArchiveEpisodePage
        do {
            loaded = try await client.archiveEpisodes(show: id, query: request, page: 1)
        } catch {
            // a failed pick puts the season/year/month whose list is shown back
            if let previous = picking.fail(ticket) {
                query = previous.query
                periods = previous.periods
                seasonEpisodes = previous.episodes
                nextPage = previous.nextPage
            }
            return
        }
        guard picking.succeed(ticket) else { return }
        seasonEpisodes = loaded.episodes
        nextPage = loaded.nextPage
        periods = loaded.periods
    }

    /// The next page of what is listed; a failed one is asked again when the end of the list shows again.
    private func loadMore() {
        guard !more.isWaiting, !picking.loading.isWaiting, let page = nextPage, let show else { return }
        let request = baseQuery(show).paging(periods)
        let ticket = more.begin()
        Task {
            let loaded = try? await client.archiveEpisodes(show: id, query: request, page: page)
            guard more.finish(ticket), let loaded else { return }
            // the next page of what the show came with extends it
            var episodes = seasonEpisodes ?? self.show?.groups.first(where: { !$0.isRecordings })?.episodes ?? []
            let known = Set(episodes.map(\.id))
            episodes += loaded.episodes.filter { !known.contains($0.id) }
            seasonEpisodes = episodes
            nextPage = loaded.nextPage
            if let more = loaded.periods { periods = more }
        }
    }
}

/// "Zpět": pops the show page like the remote's Back.
private struct BackButton: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        Button("Zpět") { dismiss() }
            .accessibilityIdentifier("show.back")
    }
}
