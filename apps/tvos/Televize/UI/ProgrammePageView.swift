import CTCore
import SwiftUI

/// What the guide opens the page for: the entry and the guide day it was listed in (the CT schedule day the
/// gateway looks the programme up in).
struct ProgrammePageItem: Identifiable {
    let entry: GuideEntry
    let day: BroadcastDay

    var id: String { entry.id }
}

/// The programme page (spec/apps.md "Programme page (T15)"): image, title, facts, TMDb lines from the gateway,
/// the full description, the state line and the buttons (programme_page_cases.json). Shown at once; the TMDb lines
/// fill in when `/dvb/details` answers. The player opens over the page; when it closes the page is recomputed (a new
/// "Pokračovat" focused). Back returns to the guide.
struct ProgrammePageView: View {
    static let posterSize = CGSize(width: 340, height: 510)
    static let imageSize = CGSize(width: 560, height: 315)

    let item: ProgrammePageItem
    /// The day's CT schedule, for the ČT image of an Anténa ČT programme.
    let ctSchedule: ScheduleStore.Schedule
    /// Back to the guide.
    let close: () -> Void

    @Environment(AppModel.self) private var model
    @State private var details: ProgrammeDetails?
    @FocusState private var focus: Focus?

    private enum Focus: Hashable {
        case button(ProgrammeButton)
        case back
    }

    /// The buttons right now and what they need.
    private struct Plan {
        var buttons: [ProgrammeButton] = []
        var resumeAt: TimeInterval?
        /// Anténa: the recording "Pokračovat" / "Od začátku" play (growing while airing).
        var recordingID: String?
    }

    private var entry: GuideEntry { item.entry }
    private var match: ProgrammeDetails.Match? { details?.match }

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            let now = context.date
            let _ = model.playerCloses  // recomputed once the player has saved its position
            let plan = plan(now: now)
            let state = entry.state(at: now)
            ZStack(alignment: .topLeading) {
                Color.black.ignoresSafeArea()
                HStack(alignment: .top, spacing: 60) {
                    artwork
                    VStack(alignment: .leading, spacing: 14) {
                        Text(entry.title)
                            .font(.title2.weight(.bold))
                            .lineLimit(2)
                        if !entry.episode.isEmpty {
                            Text(entry.episode).font(.title3).lineLimit(1)
                        }
                        facts(today: BroadcastDay(containing: now))
                            .font(.headline)
                            .foregroundStyle(.secondary)
                        if let rating = match?.rating, let line = ProgrammePage.ratingLine(value: rating.value, votes: rating.votes) {
                            Label(line, systemImage: "star.fill")
                                .font(.headline)
                                .accessibilityLabel(line)
                                .accessibilityIdentifier("page.rating")
                        }
                        if let match, let line = ProgrammePage.originalTitleLine(original: match.originalTitle, title: match.title,
                                                                                 guideTitle: entry.title) {
                            Text(line).font(.headline).foregroundStyle(.secondary).lineLimit(1)
                        }
                        Label(entry.statusText(state: state, now: now), systemImage: state.symbol)
                            .font(.headline.weight(.semibold))
                            .foregroundStyle(state.statusColor)
                        if !description.isEmpty {
                            Text(description)
                                .font(.system(size: 28))
                                .foregroundStyle(.secondary)
                                .lineLimit(9)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        buttons(plan)
                            .padding(.top, 16)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(.top, 40)
                if let details, match != nil {
                    attribution(details.attribution)
                        .frame(maxHeight: .infinity, alignment: .bottomLeading)
                }
            }
            .defaultFocus($focus, plan.buttons.first.map(Focus.button) ?? .back)
        }
        .onExitCommand(perform: close)
        .task(id: item.id) { details = await model.programmeDetails(query) }
        .onChange(of: model.playerCloses) { _, _ in
            // after tvOS has put focus back where it was before the player
            Task {
                try? await Task.sleep(for: .milliseconds(400))
                focus = plan(now: Date()).buttons.first.map(Focus.button) ?? .back
            }
        }
        .modifier(PlaybackPresentations(active: true))
    }

    // MARK: content

    private var query: ProgrammeDetailsQuery {
        switch entry {
        case .ct(let programme): return .ct(channel: programme.channel, day: item.day, idec: programme.idec)
        case .antenna(let event, _): return .dvb(eventID: event.id)
        case .parliament(let stream, _, _): return .dvb(eventID: stream.key)  // never opened for these
        }
    }

    /// The guide's description, else TMDb's overview.
    private var description: String {
        entry.summary.isEmpty ? match?.overview ?? "" : entry.summary
    }

    /// Channel (with its source icon) · Dnes 20:15–22:05 · 110 min · genre · year.
    private func facts(today: BroadcastDay) -> some View {
        let genre = entry.genre.isEmpty ? (match?.genres.prefix(2).joined(separator: ", ") ?? "") : entry.genre
        let rest = [entry.dayAndTime(today: today), ProgrammePage.minutesText(start: entry.start, end: entry.end) ?? "",
                    genre, match?.year.map(String.init) ?? ""]
            .filter { !$0.isEmpty }
        return HStack(spacing: 10) {
            SourceIcon(source: entry.source)
            Text(([entry.channelName] + rest).joined(separator: "  ·  "))
                .lineLimit(1)
        }
    }

    /// The TMDb poster when the gateway has one (and it loaded), else the guide's image.
    @ViewBuilder
    private var artwork: some View {
        let poster = match?.posterPath.map { GuideImageStore.Source.poster(path: $0) }
        Group {
            if let poster, let image = model.images.image(poster) {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .frame(width: Self.posterSize.width, height: Self.posterSize.height)
                    .accessibilityIdentifier("page.poster")
            } else {
                EntryArtwork(entry: entry, ctSchedule: ctSchedule)
                    .frame(width: Self.imageSize.width, height: Self.imageSize.height)
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .onChange(of: poster, initial: true) { _, poster in
            if let poster { model.images.want(poster) }
        }
    }

    /// TMDb's terms: their sentence and their name with any TMDb data, small, at the bottom.
    private func attribution(_ text: String) -> some View {
        HStack(spacing: 14) {
            TMDBLogo()
            Text(text)
                .font(.system(size: 18))
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("page.attribution")
    }

    // MARK: buttons

    private func buttons(_ plan: Plan) -> some View {
        // one line each: five buttons (CT airing with "Pokračovat") just fit beside the image
        HStack(spacing: 24) {
            ForEach(plan.buttons, id: \.self) { button in
                Button { perform(button, plan: plan) } label: {
                    Text(title(button, plan: plan)).lineLimit(1).minimumScaleFactor(0.75)
                }
                    .focused($focus, equals: .button(button))
                    .accessibilityIdentifier("page.\(button.rawValue)")
            }
            Button("Zpět", action: close)
                .focused($focus, equals: .back)
                .accessibilityIdentifier("page.back")
        }
        .focusSection()
    }

    private func title(_ button: ProgrammeButton, plan: Plan) -> String {
        switch button {
        case .resume: return ResumeRules.buttonTitle(plan.resumeAt ?? 0)
        case .startOver: return "Od začátku"
        case .live: return "Živě"
        case .openOfficialApp: return OfficialApp.buttonTitle
        }
    }

    private func plan(now: Date) -> Plan {
        switch entry {
        case .ct(let programme):
            // An ended CT programme is always offered (CT's own text when it cannot be played, e.g. DRM).
            let phase: ProgrammePage.Phase
            switch ProgrammeState(programme, now: now) {
            case .future, .offAir: phase = .future
            case .airing: phase = .airing
            case .buffer, .archive, .unavailable: phase = .ended
            }
            let resumeAt = model.resumeOffer(for: programme)
            var buttons = ProgrammePage.buttons(source: .ct, phase: phase, startable: phase == .airing,
                                                playable: phase == .ended, resume: resumeAt != nil)
            if Channels.bySlug(programme.channel) == nil { buttons = buttons.filter { $0 == .openOfficialApp } }
            return Plan(buttons: buttons, resumeAt: resumeAt)
        case .antenna(let event, _):
            let phase: ProgrammePage.Phase
            var recordingID: String?
            switch AntennaAction.decide(event, now: now) {
            case .future: phase = .future
            case .airing(let id): phase = .airing; recordingID = id
            case .recording(let id): phase = .ended; recordingID = id
            case .notRecorded: phase = .ended
            }
            let resumeAt = recordingID.flatMap { model.resumeOffer(recordingID: $0, event: event) }
            let buttons = ProgrammePage.buttons(source: .dvb, phase: phase, startable: phase == .airing && recordingID != nil,
                                                playable: phase == .ended && recordingID != nil, resume: resumeAt != nil)
            return Plan(buttons: buttons, resumeAt: resumeAt, recordingID: recordingID)
        case .parliament:
            return Plan()
        }
    }

    private func perform(_ button: ProgrammeButton, plan: Plan) {
        switch entry {
        case .ct(let programme):
            if button == .openOfficialApp {
                OfficialApp.open { model.alertMessage = OfficialApp.fallbackMessage }
                return
            }
            guard let channel = Channels.bySlug(programme.channel) else { return }
            switch button {
            case .resume: model.play(.programme(programme, channel: channel), resumeAt: plan.resumeAt)
            case .startOver: model.play(.programme(programme, channel: channel))
            case .live: model.play(.live(channel))
            case .openOfficialApp: break
            }
        case .antenna(let event, let channel):
            switch button {
            case .resume, .startOver:
                guard let id = plan.recordingID else { return }
                model.playAntennaRecording(id: id, event: event, finished: Date() >= event.end,
                                           resumeAt: button == .resume ? plan.resumeAt : nil, channelName: channel.name)
            case .live: model.playAntennaLive(channel)
            case .openOfficialApp: break
            }
        case .parliament:
            break
        }
    }
}

/// TMDb's logo (spec/assets/tmdb-logo-short.svg, copied into the asset catalog), small: less prominent than the app.
private struct TMDBLogo: View {
    var body: some View {
        Image("TMDBLogo")
            .resizable()
            .scaledToFit()
            .frame(height: 16)
            .accessibilityLabel("TMDB")
    }
}
