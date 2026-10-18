import CTCore
import SwiftUI

/// One guide cell: a CT internet programme or an "Anténa" (DVB-T2) event.
enum GuideEntry: Identifiable, TimedItem {
    case ct(Programme)
    case antenna(DVBEvent, DVBChannel)

    var id: String {
        switch self {
        case .ct(let programme): return programme.id
        case .antenna(let event, let channel): return "dvb|\(channel.id)|\(event.id)"
        }
    }

    var start: Date {
        switch self {
        case .ct(let programme): return programme.start
        case .antenna(let event, _): return event.start
        }
    }

    var end: Date {
        switch self {
        case .ct(let programme): return programme.end
        case .antenna(let event, _): return event.end
        }
    }

    var title: String {
        switch self {
        case .ct(let programme): return programme.title
        case .antenna(let event, _): return event.title
        }
    }

    func isAiring(at now: Date) -> Bool { start <= now && now < end }

    func state(at now: Date) -> ProgrammeState {
        switch self {
        case .ct(let programme):
            return ProgrammeState(programme, now: now)
        case .antenna(let event, _):
            switch AntennaAction.decide(event, now: now) {
            case .future: return .future
            case .airing: return .airing
            case .recording: return .archive
            case .notRecorded: return .unavailable
            }
        }
    }
}

private struct GuideRow: Identifiable {
    let id: String
    let name: String
    let entries: [GuideEntry]
}

/// "TV průvodce": EPG grid with channels as rows and time on the horizontal axis.
/// CT internet channels first, then the "Anténa" channels when a gateway is configured.
struct GuideView: View {
    static let pointsPerSecond = 0.21  // ~2 h visible
    static let channelColumnWidth: CGFloat = 190
    static let headerHeight: CGFloat = 44
    static let rowHeight: CGFloat = 80
    static let rowSpacing: CGFloat = 8
    static let minFocusableGap: CGFloat = 60

    private struct ScrollRequest: Equatable {
        let id = UUID()
        let tick: Int
        let rowID: String?
    }

    @Environment(AppModel.self) private var model
    @State private var today = BroadcastDay(containing: Date())
    @State private var day = BroadcastDay(containing: Date())
    @State private var schedule: ScheduleStore.Schedule = [:]
    @State private var antennaChannels: [DVBChannel] = []
    @State private var antennaEPG: AntennaStore.EPG = [:]
    @State private var loadError: String?
    @State private var focusedEntry: GuideEntry?
    @State private var defaultFocusID: String?
    @State private var scrollRequest: ScrollRequest?
    @State private var airingChoice: GuideEntry?
    @FocusState private var focusedID: String?

    private struct LoadKey: Equatable {
        let day: BroadcastDay
        let gateway: GatewayConfig?
    }

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            VStack(alignment: .leading, spacing: 20) {
                EntryDetail(entry: focusedEntry, today: today, now: context.date) {
                    OfficialApp.open { model.alertMessage = OfficialApp.fallbackMessage }
                }
                    .frame(height: 203)
                dayStrip
                grid(now: context.date)
            }
        }
        .task(id: LoadKey(day: day, gateway: model.gateway)) { await load() }
        .onChange(of: focusedID) { _, id in
            guard let id else { return }
            focusedEntry = entry(withID: id)
        }
        .confirmationDialog(
            airingChoice?.title ?? "",
            isPresented: Binding(get: { airingChoice != nil }, set: { if !$0 { airingChoice = nil } }),
            titleVisibility: .visible,
            presenting: airingChoice
        ) { entry in
            switch entry {
            case .ct(let programme):
                if let channel = Channels.bySlug(programme.channel) {
                    Button("Od začátku") { model.play(.programme(programme, channel: channel)) }
                    Button("Živě") { model.play(.live(channel)) }
                    Button(OfficialApp.buttonTitle) {
                        OfficialApp.open { model.alertMessage = OfficialApp.fallbackMessage }
                    }
                }
            case .antenna(let event, let channel):
                if case .airing(let recordingID?) = AntennaAction.decide(event, now: Date()) {
                    Button("Od začátku") { model.playAntennaRecording(id: recordingID, title: event.title) }
                }
                Button("Živě") { model.playAntennaLive(channel) }
            }
            Button("Zrušit", role: .cancel) {}
        }
    }

    private var rows: [GuideRow] {
        let ct = Channels.all.map { channel in
            GuideRow(id: channel.slug, name: channel.name, entries: (schedule[channel.slug] ?? []).map(GuideEntry.ct))
        }
        let antenna = antennaChannels.map { channel in
            GuideRow(id: "dvb|\(channel.id)", name: channel.name,
                     entries: (antennaEPG[channel.id] ?? []).map { GuideEntry.antenna($0, channel) })
        }
        return ct + antenna
    }

    private func timeline(for rows: [GuideRow]) -> GuideTimeline {
        GuideTimeline(spans: rows.flatMap(\.entries), fallbackDay: day, pointsPerSecond: Self.pointsPerSecond)
    }

    // MARK: grid

    private func grid(now: Date) -> some View {
        let rows = rows
        let timeline = timeline(for: rows)
        return Group {
            if let loadError {
                VStack(spacing: 24) {
                    Text(loadError).font(.headline)
                    Button("Zkusit znovu") { Task { await load() } }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                // Vertical scrolling once the Anténa rows are added; the channel names scroll along.
                ScrollViewReader { verticalProxy in
                ScrollView(.vertical, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 0) {
                        VStack(alignment: .leading, spacing: Self.rowSpacing) {
                            Color.clear
                                .frame(width: Self.channelColumnWidth, height: Self.headerHeight)
                            ForEach(rows) { row in
                                Text(row.name)
                                    .font(.system(size: 28, weight: .bold))
                                    .lineLimit(1)
                                    .minimumScaleFactor(0.7)
                                    .frame(width: Self.channelColumnWidth - 12, height: Self.rowHeight, alignment: .leading)
                                    .padding(.trailing, 12)
                                    .id("row|\(row.id)")
                            }
                        }
                        .padding(.vertical, 6)
                        ScrollViewReader { proxy in
                            ScrollView(.horizontal, showsIndicators: false) {
                                VStack(alignment: .leading, spacing: Self.rowSpacing) {
                                    timeHeader(timeline)
                                    ForEach(rows) { row in
                                        self.row(row, timeline.segments(for: row.entries, splitGapsAtTicks: true), now: now)
                                            .frame(height: Self.rowHeight)
                                    }
                                }
                                .overlay(alignment: .topLeading) { nowLine(timeline, now: now) }
                                .padding(.vertical, 6)
                            }
                            .focusSection()
                            .defaultFocus($focusedID, defaultFocusID)
                            .onChange(of: scrollRequest) { _, request in
                                guard let request else { return }
                                proxy.scrollTo("tick-\(request.tick)", anchor: .leading)
                                if let rowID = request.rowID { verticalProxy.scrollTo("row|\(rowID)", anchor: .top) }
                            }
                        }
                    }
                }
                }
            }
        }
    }

    /// Day picker: 7 days back, 2 ahead. Moving up from the grid lands here.
    private var dayStrip: some View {
        HStack(spacing: 14) {
            ForEach(GuideRange.days(today: today), id: \.self) { option in
                Button(option.label(today: today)) { day = option }
                    .buttonStyle(DayChipStyle(isSelected: option == day))
            }
        }
        // Full width, so moving up from any column of the grid lands in the strip.
        .frame(maxWidth: .infinity, alignment: .leading)
        .focusSection()
    }

    private func timeHeader(_ timeline: GuideTimeline) -> some View {
        HStack(spacing: 0) {
            ForEach(Array(timeline.ticks.enumerated()), id: \.offset) { index, tick in
                Text(Formatting.time(tick))
                    .font(.system(size: 22, weight: .medium))
                    .foregroundStyle(.secondary)
                    .frame(width: GuideTimeline.tickInterval * Self.pointsPerSecond, alignment: .leading)
                    .id("tick-\(index)")
            }
        }
        .frame(height: Self.headerHeight)
    }

    private func row(_ row: GuideRow, _ segments: [GuideTimeline.Segment<GuideEntry>], now: Date) -> some View {
        HStack(spacing: 0) {
            ForEach(Array(segments.enumerated()), id: \.offset) { index, segment in
                switch segment {
                case .gap(let width) where width < Self.minFocusableGap:
                    Color.clear.frame(width: width)
                case .gap(let width):
                    // Focusable filler, so up/down never gets stuck on a row with a long gap.
                    Button {} label: {
                        Text("Mimo vysílání")
                            .font(.system(size: 22))
                            .lineLimit(1)
                            .padding(.horizontal, 12)
                            .frame(maxHeight: .infinity, alignment: .leading)
                            .frame(width: width - 6, alignment: .leading)
                            .stickyLeading()
                    }
                    .buttonStyle(GuideCellStyle(state: .offAir))
                    .focused($focusedID, equals: "gap|\(row.id)|\(index)")
                    .padding(.horizontal, 3)
                case .item(let entry, let width):
                    let state = entry.state(at: now)
                    Button {
                        select(entry)
                    } label: {
                        EntryCellLabel(start: entry.start, title: entry.title, state: state)
                            .frame(width: max(width - 6, 2))
                    }
                    .buttonStyle(GuideCellStyle(state: state))
                    .focused($focusedID, equals: entry.id)
                    .padding(.horizontal, 3)
                }
            }
        }
    }

    @ViewBuilder
    private func nowLine(_ timeline: GuideTimeline, now: Date) -> some View {
        if timeline.contains(now) {
            Rectangle()
                .fill(Color.red)
                .frame(width: 3)
                .padding(.top, Self.headerHeight - 8)
                .offset(x: timeline.x(for: now) - 1.5)
                .allowsHitTesting(false)
        }
    }

    // MARK: behaviour

    private func load() async {
        let now = Date()
        today = BroadcastDay(containing: now)
        loadError = nil
        await loadAntenna()
        do {
            let loaded = try await model.schedules.schedule(for: day)
            schedule = loaded
            positionAtAnchor(now: now)
        } catch is CancellationError {
            return
        } catch {
            schedule = [:]
            loadError = AppModel.message(for: error)
        }
    }

    /// Anténa rows; a day outside the gateway's range (or an unreachable gateway) just leaves them empty.
    private func loadAntenna() async {
        guard let antenna = model.antenna else {
            antennaChannels = []
            antennaEPG = [:]
            return
        }
        antennaChannels = (try? await antenna.channels()) ?? []
        antennaEPG = (try? await antenna.epg(for: day)) ?? [:]
    }

    /// Scroll to now (today) or to the evening (other days) and pick the cell focus lands on.
    private func positionAtAnchor(now: Date) {
        let anchor = day == today
            ? now
            : Calendar.prague.date(byAdding: .hour, value: 20, to: day.startDate()) ?? day.startDate()
        let rows = rows
        var anchorRow = rows.first
        #if DEBUG
        // `-guideRow 6` starts on that row (6 = first Anténa row), for simulator screenshots.
        let debugRow = UserDefaults.standard.integer(forKey: "guideRow")
        if debugRow > 0, debugRow < rows.count { anchorRow = rows[debugRow] }
        #endif
        let firstRow = anchorRow?.entries ?? []
        let target = firstRow.first { $0.isAiring(at: anchor) } ?? firstRow.first { $0.start >= anchor } ?? firstRow.last
        defaultFocusID = target?.id
        focusedEntry = target
        let tick = timeline(for: rows).tickIndex(for: anchor.addingTimeInterval(-30 * 60))
        // Let the new rows lay out before scrolling.
        let rowID = anchorRow?.id == rows.first?.id ? nil : anchorRow?.id
        Task { @MainActor in scrollRequest = ScrollRequest(tick: tick, rowID: rowID) }
    }

    private func entry(withID id: String) -> GuideEntry? {
        for row in rows {
            if let match = row.entries.first(where: { $0.id == id }) { return match }
        }
        return nil
    }

    private func select(_ entry: GuideEntry) {
        let now = Date()
        switch entry {
        case .ct(let programme):
            guard let channel = Channels.bySlug(programme.channel) else { return }
            switch DecisionRules.action(for: programme, now: now) {
            case .future:
                model.alertMessage = CTError.notStarted
            case .startOver where programme.isAiring(at: now):
                airingChoice = entry
            default:
                model.play(.programme(programme, channel: channel))
            }
        case .antenna(let event, _):
            switch AntennaAction.decide(event, now: now) {
            case .future:
                model.alertMessage = CTError.notStarted
            case .airing:
                airingChoice = entry
            case .recording(let id):
                model.playAntennaRecording(id: id, title: event.title)
            case .notRecorded:
                model.alertMessage = AntennaAction.notRecordedMessage
            }
        }
    }
}

// MARK: - cells

private struct DayChipStyle: ButtonStyle {
    let isSelected: Bool

    func makeBody(configuration: Configuration) -> some View {
        DayChip(configuration: configuration, isSelected: isSelected)
    }
}

private struct DayChip: View {
    let configuration: ButtonStyleConfiguration
    let isSelected: Bool
    @Environment(\.isFocused) private var isFocused

    var body: some View {
        configuration.label
            .font(.system(size: 24, weight: isSelected ? .bold : .medium))
            .padding(.horizontal, 20)
            .frame(height: 50)
            .foregroundStyle(isFocused ? Color.black : Color.white)
            .background(Capsule().fill(isFocused ? Color.white : (isSelected ? Color(white: 0.32) : Color(white: 0.14))))
            .scaleEffect(isFocused ? 1.06 : 1)
            .animation(.easeOut(duration: 0.15), value: isFocused)
    }
}

private struct EntryCellLabel: View {
    let start: Date
    let title: String
    let state: ProgrammeState

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                if state == .buffer || state == .archive || state == .airing {
                    Image(systemName: state.symbol).font(.system(size: 16))
                }
                Text(Formatting.time(start))
            }
            .font(.system(size: 20, weight: .medium))
            .opacity(0.8)
            Text(title)
                .font(.system(size: 25, weight: .semibold))
        }
        .lineLimit(1)
        .padding(.horizontal, 12)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .stickyLeading()
    }
}

extension View {
    /// Keeps a cell's text visible when the cell starts left of the visible area.
    func stickyLeading() -> some View {
        visualEffect { content, geometry in
            let hidden = -geometry.frame(in: .scrollView).minX
            return content.offset(x: max(0, min(hidden, geometry.size.width - 160)))
        }
        .clipped()
    }
}

private struct GuideCellStyle: ButtonStyle {
    let state: ProgrammeState

    func makeBody(configuration: Configuration) -> some View {
        GuideCell(configuration: configuration, state: state)
    }
}

private struct GuideCell: View {
    let configuration: ButtonStyleConfiguration
    let state: ProgrammeState
    @Environment(\.isFocused) private var isFocused

    var body: some View {
        configuration.label
            .foregroundStyle(isFocused ? Color.black : Color.white)
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(fill))
            .opacity((state == .unavailable || state == .offAir) && !isFocused ? 0.4 : 1)
            .scaleEffect(configuration.isPressed ? 0.96 : 1)
            .shadow(color: .black.opacity(isFocused ? 0.5 : 0), radius: 12, y: 6)
            .zIndex(isFocused ? 1 : 0)
            .animation(.easeOut(duration: 0.15), value: isFocused)
    }

    private var fill: Color {
        if isFocused { return .white }
        switch state {
        case .airing: return Color(red: 0.55, green: 0.12, blue: 0.14)
        case .buffer, .archive: return Color(white: 0.24)
        case .unavailable: return Color(white: 0.16)
        case .future: return Color(white: 0.12)
        case .offAir: return Color(white: 0.07)
        }
    }
}

// MARK: - detail

private struct EntryDetail: View {
    let entry: GuideEntry?
    let today: BroadcastDay
    let now: Date
    let openOfficialApp: () -> Void

    var body: some View {
        if let entry {
            let state = entry.state(at: now)
            HStack(alignment: .top, spacing: 36) {
                artwork(entry)
                    .frame(width: 360, height: 203)
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))

                VStack(alignment: .leading, spacing: 8) {
                    Text(entry.title)
                        .font(.title3.weight(.bold))
                        .lineLimit(1)
                    if !episode(entry).isEmpty {
                        Text(episode(entry))
                            .font(.subheadline)
                            .lineLimit(1)
                    }
                    Text(meta(entry))
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    Label(statusText(entry, state: state), systemImage: state.symbol)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(state == .airing ? .red : (state == .buffer || state == .archive ? .green : .secondary))
                    Text(description(entry))
                        .font(.system(size: 25))
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
                Spacer(minLength: 0)
                // For CT programmes we cannot play (DRM-only ones show CT's slate in our stream).
                if case .ct(let programme) = entry, programme.start <= now {
                    Button(OfficialApp.buttonTitle, action: openOfficialApp)
                        .font(.system(size: 24, weight: .semibold))
                }
            }
        } else {
            Text("Žádný pořad")
                .font(.title3)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        }
    }

    @ViewBuilder
    private func artwork(_ entry: GuideEntry) -> some View {
        switch entry {
        case .ct(let programme):
            AsyncImage(url: programme.imageURL) { phase in
                if let image = phase.image {
                    image.resizable().aspectRatio(contentMode: .fill)
                } else {
                    Color(white: 0.15)
                }
            }
        case .antenna(_, let channel):
            ZStack {
                Color(white: 0.15)
                VStack(spacing: 8) {
                    Image(systemName: "antenna.radiowaves.left.and.right").font(.system(size: 44))
                    Text(channel.name).font(.headline)
                }
                .foregroundStyle(.secondary)
            }
        }
    }

    private func episode(_ entry: GuideEntry) -> String {
        switch entry {
        case .ct(let programme): return programme.episodeTitle
        case .antenna(let event, _): return event.subtitle
        }
    }

    private func description(_ entry: GuideEntry) -> String {
        switch entry {
        case .ct(let programme): return programme.description
        case .antenna(let event, _): return event.description
        }
    }

    private func statusText(_ entry: GuideEntry, state: ProgrammeState) -> String {
        guard case .antenna(let event, _) = entry else { return state.statusText }
        switch AntennaAction.decide(event, now: now) {
        case .future: return state.statusText
        case .airing(let recording): return recording == nil ? "Právě běží – živě" : "Právě běží – od začátku nebo živě"
        case .recording: return "Přehrát záznam"
        case .notRecorded: return AntennaAction.notRecordedMessage
        }
    }

    private func meta(_ entry: GuideEntry) -> String {
        let channel: String
        let genre: String
        switch entry {
        case .ct(let programme):
            channel = Channels.bySlug(programme.channel)?.name ?? programme.channel
            genre = programme.genre
        case .antenna(let event, let dvbChannel):
            channel = "\(dvbChannel.name) (anténa)"
            genre = event.genre
        }
        let day = BroadcastDay(containing: entry.start).label(today: today)
        let range = "\(Formatting.time(entry.start))–\(Formatting.time(entry.end))"
        return [channel, "\(day) \(range)", genre]
            .filter { !$0.isEmpty }
            .joined(separator: "  ·  ")
    }
}
