import CTCore
import SwiftUI

/// A show of the Archiv tab (spec/apps.md "Archiv": the programme page of T15 extended for shows): image, title,
/// facts, TMDb rating, description, creators; a film gets the T15 buttons, a series season chips and its episodes,
/// each `Přehrát` / `Pokračovat od …`; then the group "Nahráno z antény". The player opens over the page and Back
/// returns here; the offers are read again when it closes.
struct ShowPageView: View {
    let client: DVBClient
    let id: String

    @Environment(AppModel.self) private var model
    @State private var show: ArchiveShow?
    @State private var error: String?
    /// The season whose episodes are listed; nil = the gateway's first groups as they came.
    @State private var season: String?
    @State private var seasonEpisodes: [ArchiveEpisode]?
    @State private var nextPage: Int?
    @State private var loadingMore = false
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
            } else {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .task(id: Key(gateway: model.gateway, attempt: attempt)) {
            do {
                let loaded = try await client.archiveShow(id)
                show = loaded
                nextPage = loaded.groups.first { !$0.isRecordings }?.nextPage
                error = nil
            } catch is CancellationError {
            } catch {
                self.error = AppModel.message(for: error)
            }
        }
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
                        let episodes = seasonEpisodes ?? ctGroups.flatMap(\.episodes)
                        if !episodes.isEmpty {
                            episodeList(title: seasonEpisodes == nil ? ctGroups.first?.title ?? "" : "",
                                        episodes: episodes, show: card, pages: true)
                        }
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
        let selected = season ?? currentSeason(show)
        return ScrollView(.horizontal) {
            HStack(spacing: 20) {
                ForEach(show.seasons) { item in
                    Button { select(item.id, show: show) } label: {
                        Text(item.title).font(.callout.weight(selected == item.id ? .bold : .regular))
                    }
                    .overlay(alignment: .bottom) {
                        if selected == item.id {
                            Capsule().fill(.white).frame(height: 4).padding(.horizontal, 24).offset(y: 10)
                        }
                    }
                    .accessibilityIdentifier("show.season.\(item.id)")
                }
            }
            .padding(.vertical, 12)
        }
        .scrollClipDisabled()
        .focusSection()
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

    // MARK: seasons

    /// The season the show came with: its first group's, else the first chip.
    private func currentSeason(_ show: ArchiveShow) -> String? {
        show.groups.first { !$0.isRecordings }?.season ?? show.seasons.first?.id
    }

    private func select(_ id: String, show: ArchiveShow) {
        guard id != (season ?? currentSeason(show)) else { return }
        season = id
        seasonEpisodes = []
        nextPage = nil
        Task { await loadSeason(id, page: 1) }
    }

    private func loadMore() {
        let first = show?.groups.first { !$0.isRecordings }
        guard !loadingMore, let page = nextPage, let id = season ?? first?.season ?? show?.seasons.first?.id else { return }
        Task { await loadSeason(id, page: page) }
    }

    private func loadSeason(_ id: String, page: Int) async {
        loadingMore = true
        defer { loadingMore = false }
        guard let loaded = try? await client.archiveSeason(show: self.id, season: id, page: page),
              id == (season ?? show.flatMap(currentSeason)) else { return }
        // the first page of the gateway's first season extends what the show came with
        var episodes = seasonEpisodes ?? show?.groups.first(where: { !$0.isRecordings })?.episodes ?? []
        let known = Set(episodes.map(\.id))
        episodes += loaded.episodes.filter { !known.contains($0.id) }
        seasonEpisodes = episodes
        nextPage = loaded.nextPage
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
