import CTCore
import SwiftUI
import UIKit

/// One guide cell: a CT internet programme, an "Anténa" (DVB-T2) event or a Poslanecká sněmovna stream (one cell
/// over the whole day, no programme data).
enum GuideEntry: Identifiable, TimedItem {
    case ct(Programme)
    case antenna(DVBEvent, DVBChannel)
    case parliament(PSPStream, start: Date, end: Date)

    var id: String {
        switch self {
        case .ct(let programme): return programme.id
        case .antenna(let event, let channel): return "dvb|\(channel.id)|\(event.id)"
        case .parliament(let stream, _, _): return stream.key
        }
    }

    var start: Date {
        switch self {
        case .ct(let programme): return programme.start
        case .antenna(let event, _): return event.start
        case .parliament(_, let start, _): return start
        }
    }

    var end: Date {
        switch self {
        case .ct(let programme): return programme.end
        case .antenna(let event, _): return event.end
        case .parliament(_, _, let end): return end
        }
    }

    var title: String {
        switch self {
        case .ct(let programme): return programme.title
        case .antenna(let event, _): return event.title
        case .parliament(let stream, _, _): return stream.name
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
        case .parliament:
            return .offAir  // the live state comes from the monitor: `ProgrammeState(parliament:)`
        }
    }
}

extension ProgrammeState {
    /// The look of a Poslanecká sněmovna cell: on = airing, off = dimmed, not checked yet = future.
    init(parliament: PSPLiveState) {
        switch parliament {
        case .live: self = .airing
        case .off: self = .offAir
        case .unknown: self = .future
        }
    }
}

private struct GuideRow: Identifiable {
    let id: String
    let name: String
    let source: ChannelPrefs.Source
    let entries: [GuideEntry]
}

/// Rows, time axis and cell segments, computed once per data change (not on every focus move:
/// rebuilding thousands of cells per D-pad press made the guide lag by seconds).
private struct GuideLayout {
    var version = 0
    var rows: [GuideRow] = []
    var timeline = GuideTimeline(start: .distantPast, end: .distantPast, pointsPerSecond: GuideView.pointsPerSecond)
    var segments: [[GuideTimeline.Segment<GuideEntry>]] = []
    var entries: [String: GuideEntry] = [:]
    /// Row index of every focusable cell id (programmes and "Mimo vysílání" gaps).
    var rowOf: [String: Int] = [:]
    /// Segment index of every focusable cell id within its row.
    var segmentOf: [String: Int] = [:]
    /// x of each segment's leading edge, per row (`segments` start at the timeline's start).
    var starts: [[Double]] = []
    /// The Poslanecká sněmovna streams among the rows ("is it on now?" is checked for these).
    var parliamentStreams: [PSPStream] = []

    /// Horizontal extent of a focusable cell.
    func span(of id: String) -> (row: Int, from: Double, to: Double)? {
        guard let row = rowOf[id], let index = segmentOf[id] else { return nil }
        let from = starts[row][index]
        return (row, from, from + Self.width(segments[row][index]))
    }

    /// Start of a focusable cell: the programme's own start (also when clipped at the grid's edge), else the gap's.
    func startTime(of id: String) -> Date? {
        if let entry = entries[id] { return entry.start }
        guard let span = span(of: id) else { return nil }
        return timeline.start.addingTimeInterval(span.from / timeline.pointsPerSecond)
    }

    static func width(_ segment: GuideTimeline.Segment<GuideEntry>) -> Double {
        switch segment {
        case .gap(let width): return width
        case .item(_, let width): return width
        }
    }

    static func gapID(row: GuideRow, index: Int) -> String { "gap|\(row.id)|\(index)" }

    /// "Across days": the focusable "‹ Včera" / "Zítra ›" cell before the first / after the last cell of a row.
    static let dayEdgeWidth: Double = 170
    static func edgeID(next: Bool, rowID: String) -> String { "\(next ? "next" : "prev")|\(rowID)" }

    /// Focus id of the cell under the now line in `row` ("Live column first", spec/apps.md).
    func cellID(row: Int, at date: Date) -> String? {
        guard rows.indices.contains(row),
              let index = timeline.focusableSegment(at: date, in: segments[row], minGapWidth: GuideView.minFocusableGap)
        else { return nil }
        switch segments[row][index] {
        case .item(let entry, _): return entry.id
        case .gap: return Self.gapID(row: rows[row], index: index)
        }
    }
}

/// "TV průvodce": EPG grid with channels as rows and time on the horizontal axis.
/// One mixed list of CT internet and "Anténa" channels in this TV's order, without hidden channels and
/// switched-off sources (Nastavení → Kanály, `ChannelPrefs`).
struct GuideView: View {
    nonisolated static let pointsPerSecond = 0.21  // ~2 h visible
    static let channelColumnWidth: CGFloat = 220
    static let headerHeight: CGFloat = 44
    static let rowHeight: CGFloat = 80
    static let rowSpacing: CGFloat = 8
    nonisolated static let minFocusableGap: CGFloat = 60

    /// The part of the grid that is actually built (rows × x range in points). Building every cell of
    /// a day for dozens of Anténa channels (thousands of buttons) made a real Apple TV lag by a minute;
    /// the rest is empty space of the same size, so scrolling and positions are unchanged.
    fileprivate struct Window: Equatable {
        var rows: Range<Int> = 0..<0
        var x: Range<Double> = 0..<0

        // ~7 rows and ~2 h are on screen; a little more around them is enough to move into.
        // Rows are cheap to add per press; time needs a wider margin (long programmes, quick presses):
        // the next cell must already exist, else tvOS moves focus into another row.
        static let rowsBefore = 5, rowsAfter = 10, rowMargin = 3
        static let before: Double = 3 * 3600 * GuideView.pointsPerSecond
        static let after: Double = 5 * 3600 * GuideView.pointsPerSecond
        static let margin: Double = 2 * 3600 * GuideView.pointsPerSecond

        static func around(row: Int, from: Double, to: Double, rowCount: Int) -> Window {
            Window(rows: max(0, row - rowsBefore)..<min(rowCount, row + rowsAfter + 1),
                   x: max(0, from - before)..<(to + after))
        }

        /// Still fine for a focused cell: enough rows and time around it to move into.
        func covers(row: Int, from: Double, to: Double, rowCount: Int) -> Bool {
            let top = rows.lowerBound == 0 || row >= rows.lowerBound + Self.rowMargin
            let bottom = rows.upperBound == rowCount || row < rows.upperBound - Self.rowMargin
            let left = x.lowerBound == 0 || from >= x.lowerBound + Self.margin
            return top && bottom && left && to <= x.upperBound - Self.margin
        }
    }

    fileprivate struct ScrollRequest: Equatable {
        let id = UUID()
        let tick: Int
        let rowID: String?
    }

    /// Bumped by RootView on every open of the guide (tab chosen, app back in front): the guide shows now again.
    let opens: Int

    @Environment(AppModel.self) private var model
    /// The grid's broadcast day (05:00–05:00); "today" for labels is the calendar day of the minute's `now`.
    @State private var day = GuideDays.openDay(now: Date())
    @State private var schedule: ScheduleStore.Schedule = [:]
    @State private var antennaChannels: [DVBChannel] = []
    @State private var antennaEPG: AntennaStore.EPG = [:]
    @State private var loadError: String?
    @State private var layout = GuideLayout()
    @State private var window = Window()
    @State private var focusedEntry: GuideEntry?
    /// Start of the focused cell (programme or gap): the day strip highlights its calendar day (`chipDay`).
    @State private var focusTime: Date?
    /// Where the images around the focus were last queued (spec/apps.md "Image prefetch").
    @State private var prefetchPoint: ImagePrefetch.Point?
    /// "Live column first": up/down follows what is airing until the first left/right (today only).
    @State private var liveColumn = false
    /// The cell focus is being moved to by the live column (its focus change is not a user move).
    @State private var redirectID: String?
    @State private var defaultFocusID: String?
    @State private var scrollRequest: ScrollRequest?
    @State private var choice: Choice?
    @FocusState private var focusedID: String?
    @FocusState private var focusedChip: BroadcastDay?
    @FocusState private var officialAppFocused: Bool
    /// The last focus move ended on a chip: only then may focus go up to "Otevřít v iVysílání" (grid ↔ strip ↔
    /// button); reached from the tab bar it passes on to the highlighted chip.
    @State private var cameFromStrip = false
    /// "Across days": where focus goes once the next/previous day is loaded.
    @State private var dayAnchor: DayAnchor?
    /// A chip was selected (or the guide reopened): where the grid opens once its day is loaded.
    @State private var selection: Selection?

    private struct Selection {
        let anchor: GuideDays.Anchor
        /// A chip was selected: focus moves to the anchor cell (on a reopen it stays where it is).
        let moveFocus: Bool
    }

    private struct DayAnchor {
        let rowID: String
        /// Left past the first cell: land on the row's cell at the new day's end, else at its start.
        let atEnd: Bool
    }

    /// The "Pokračovat / Od začátku / Živě" choice for a selected programme.
    private struct Choice {
        let entry: GuideEntry
        /// "Pokračovat" offer in seconds from the programme's start, if any.
        let resumeAt: TimeInterval?
        /// The programme is on air, so "Živě" is offered too.
        let isAiring: Bool
    }

    private struct LoadKey: Equatable {
        let day: BroadcastDay
        let gateway: GatewayConfig?
    }

    var body: some View {
        // `today` follows the clock: recomputed with `now` every minute (after midnight "Dnes" moves on).
        TimelineView(.periodic(from: .now, by: 60)) { context in
            let today = BroadcastDay(containing: context.date)
            VStack(alignment: .leading, spacing: 20) {
                EntryDetail(entry: focusedEntry, today: today, now: context.date, ctSchedule: schedule,
                            officialAppFocused: $officialAppFocused) {
                    OfficialApp.open { model.alertMessage = OfficialApp.fallbackMessage }
                }
                    .frame(height: EntryDetail.height)
                dayStrip(today: today)
                grid(now: context.date, today: today)
            }
        }
        .task(id: LoadKey(day: day, gateway: model.gateway)) { await load() }
        .onChange(of: model.channelPrefs.prefs) { _, _ in rebuildLayout() }
        // checked only while the grid shows today (other days have no live state)
        .watchesParliament(layout.timeline.contains(Date()) ? layout.parliamentStreams : [])
        .onChange(of: opens) { _, _ in reopen() }
        .onChange(of: focusedChip) { _, chip in
            if chip != nil { cameFromStrip = true }
        }
        .onChange(of: officialAppFocused) { _, focused in
            guard focused else { return }
            if cameFromStrip {
                cameFromStrip = false
            } else {
                focusedChip = highlightedChip(today: BroadcastDay(containing: Date()))
            }
        }
        .onChange(of: focusedID) { old, id in
            if id != nil { cameFromStrip = false }
            guard let id else { return }
            if crossDay(from: old, to: id) { return }
            followLiveColumn(from: old, to: id)
            let current = focusedID ?? id
            focusedEntry = layout.entries[current]
            focusTime = layout.startTime(of: current)
            keepWindow(around: current)
            prefetchImages(around: current)
        }
        .confirmationDialog(
            choice?.entry.title ?? "",
            isPresented: Binding(get: { choice != nil }, set: { if !$0 { choice = nil } }),
            titleVisibility: .visible,
            presenting: choice
        ) { choice in
            // "Pokračovat" comes first, so it has the focus.
            switch choice.entry {
            case .ct(let programme):
                if let channel = Channels.bySlug(programme.channel) {
                    if let resumeAt = choice.resumeAt {
                        Button(ResumeRules.buttonTitle(resumeAt)) {
                            model.play(.programme(programme, channel: channel), resumeAt: resumeAt)
                        }
                    }
                    Button("Od začátku") { model.play(.programme(programme, channel: channel)) }
                    if choice.isAiring {
                        Button("Živě") { model.play(.live(channel)) }
                        Button(OfficialApp.buttonTitle) {
                            OfficialApp.open { model.alertMessage = OfficialApp.fallbackMessage }
                        }
                    }
                }
            case .antenna(let event, let channel):
                if let recordingID = Self.playableRecordingID(event) {
                    let finished = !choice.isAiring
                    if let resumeAt = choice.resumeAt {
                        Button(ResumeRules.buttonTitle(resumeAt)) {
                            model.playAntennaRecording(id: recordingID, event: event, finished: finished, resumeAt: resumeAt,
                                                   channelName: channel.name)
                        }
                    }
                    Button("Od začátku") {
                        model.playAntennaRecording(id: recordingID, event: event, finished: finished, channelName: channel.name)
                    }
                }
                if choice.isAiring {
                    Button("Živě") { model.playAntennaLive(channel) }
                }
            case .parliament:
                EmptyView()  // selected directly, never a choice
            }
            Button("Zrušit", role: .cancel) {}
        }
    }

    private func buildRows() -> [GuideRow] {
        let bounds = Self.bounds(of: day)
        return model.channelPrefs.channels(antenna: antennaChannels).visible.map { channel in
            switch channel {
            case .ct(let ct):
                return GuideRow(id: ct.slug, name: ct.name, source: .ct,
                                entries: (schedule[ct.slug] ?? []).map(GuideEntry.ct))
            case .antenna(let dvb):
                return GuideRow(id: "dvb|\(dvb.id)", name: dvb.name, source: .dvb,
                                entries: (antennaEPG[dvb.id] ?? []).map { GuideEntry.antenna($0, dvb) })
            case .parliament(let stream):
                return GuideRow(id: stream.key, name: stream.name, source: .psp,
                                entries: [.parliament(stream, start: bounds.start, end: bounds.end)])
            }
        }
    }

    /// Version 0 = nothing loaded yet: the grid stays mounted (empty) until then, so focus lands on
    /// the anchor cell instead of wherever a re-created grid puts it.
    private func rebuildLayout() {
        let rows = buildRows()
        // Exactly the broadcast day (05:00–05:00): programmes crossing its edges are clipped, and the next or
        // previous day is reached by "Across days", so the day strip always matches what the grid shows.
        let bounds = Self.bounds(of: day)
        let timeline = GuideTimeline(start: bounds.start, end: bounds.end, pointsPerSecond: Self.pointsPerSecond)
        let segments = rows.map { timeline.segments(for: $0.entries, splitGapsAtTicks: true) }
        var entries: [String: GuideEntry] = [:]
        var rowOf: [String: Int] = [:]
        var segmentOf: [String: Int] = [:]
        var starts: [[Double]] = []
        for (index, row) in rows.enumerated() {
            var x = 0.0
            var rowStarts: [Double] = []
            for (position, segment) in segments[index].enumerated() {
                rowStarts.append(x)
                x += GuideLayout.width(segment)
                let id: String
                switch segment {
                case .item(let entry, _):
                    entries[entry.id] = entry
                    id = entry.id
                case .gap:
                    id = GuideLayout.gapID(row: row, index: position)
                }
                rowOf[id] = index
                segmentOf[id] = position
            }
            starts.append(rowStarts)
        }
        let streams = rows.flatMap(\.entries).compactMap { entry -> PSPStream? in
            if case .parliament(let stream, _, _) = entry { return stream }
            return nil
        }
        layout = GuideLayout(version: layout.version + 1, rows: rows, timeline: timeline, segments: segments,
                             entries: entries, rowOf: rowOf, segmentOf: segmentOf, starts: starts,
                             parliamentStreams: streams)
    }

    // MARK: grid

    private func grid(now: Date, today: BroadcastDay) -> some View {
        Group {
            if let loadError {
                VStack(spacing: 24) {
                    Text(loadError).font(.headline)
                    Button("Zkusit znovu") { Task { await load() } }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if layout.version > 0, layout.rows.isEmpty,
                      model.antenna == nil || !antennaChannels.isEmpty || !model.channelPrefs.prefs.isOn(.dvb) {
                // every channel hidden in Nastavení → Kanály
                Text(ChannelPrefsUI.allHiddenMessage)
                    .font(.headline)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                // Equatable: a focus move re-renders GuideView (detail), not the thousands of cells.
                GuideGrid(layout: layout, window: window, now: now, parliament: model.parliament.states,
                          focusedID: $focusedID, defaultFocusID: defaultFocusID,
                          scrollRequest: scrollRequest, previousDay: edgeLabel(next: false, today: today),
                          nextDay: edgeLabel(next: true, today: today), select: select)
                    .equatable()
            }
        }
    }

    /// Day picker: 7 days back, 2 ahead. It highlights the calendar day of the focused programme's start
    /// (`chipDay`: 22:15 "Dnes", the next cell at 0:10 "Zítra" in the same grid; the grid's day when that day has
    /// no chip); moving up from the grid lands on that chip.
    private func dayStrip(today: BroadcastDay) -> some View {
        let days = GuideRange.days(today: today)
        let highlighted = highlightedChip(today: today)
        return HStack(spacing: 14) {
            ForEach(days, id: \.self) { option in
                Button(option.label(today: today)) { selectDay(option) }
                    .buttonStyle(DayChipStyle(isSelected: option == highlighted))
                    .focused($focusedChip, equals: option)
            }
        }
        // Full width, so moving up from any column of the grid lands in the strip, on the highlighted chip
        // (not whichever chip is above the focused cell).
        .frame(maxWidth: .infinity, alignment: .leading)
        .focusSection()
        .defaultFocus($focusedChip, highlighted, priority: .userInitiated)
    }

    /// The calendar day of the focused programme's start, or the grid's day when that day has no chip.
    private func highlightedChip(today: BroadcastDay) -> BroadcastDay {
        GuideDays.highlightedChip(focusStart: focusTime ?? Self.bounds(of: day).start, gridDay: day,
                                  chips: GuideRange.days(today: today))
    }

    // MARK: behaviour

    private func load() async {
        let now = Date()
        loadError = nil
        await loadAntenna()
        await model.images.refreshStatus()  // at most every 5 min
        do {
            let loaded = try await model.schedules.schedule(for: day)
            schedule = loaded
            rebuildLayout()
            positionAtAnchor(now: now)
        } catch is CancellationError {
            return
        } catch {
            schedule = [:]
            dayAnchor = nil
            selection = nil
            rebuildLayout()
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

    /// Every open of the guide shows now: the grid containing now (`openDay`, also between 00:00 and 05:00),
    /// focus on the programme airing now in the first row.
    private func reopen() {
        model.images.guideOpened()
        dayAnchor = nil
        selection = Selection(anchor: .now, moveFocus: false)
        let target = GuideDays.openDay(now: Date())
        if target != day {
            day = target  // loads it, then positions
        } else if layout.version > 0 {
            positionAtAnchor(now: Date())
        }
    }

    /// A chip: "Dnes" = the grid containing now at now (as on open), any other day = that day at 20:00.
    private func selectDay(_ chip: BroadcastDay) {
        let now = Date()
        let selected = GuideDays.select(chip: chip, now: now)
        dayAnchor = nil
        selection = Selection(anchor: selected.anchor, moveFocus: true)
        if selected.day != day {
            liveColumn = false
            day = selected.day
        } else if layout.version > 0 {
            positionAtAnchor(now: now)
        }
    }

    /// Scroll to now (the grid containing now) or to the evening (other days), or where a chip selection or
    /// a day crossing asks for, and pick the cell focus lands on.
    private func positionAtAnchor(now: Date) {
        let crossing = dayAnchor
        dayAnchor = nil
        let selected = selection
        selection = nil
        let anchor: Date
        if let crossing {
            let bounds = Self.bounds(of: day)
            anchor = crossing.atEnd ? bounds.end.addingTimeInterval(-60) : bounds.start
        } else {
            let opening = day == GuideDays.openDay(now: now) ? GuideDays.Anchor.now : .time(GuideDays.evening(of: day))
            switch selected?.anchor ?? opening {
            case .now where day == GuideDays.openDay(now: now): anchor = now
            case .now: anchor = Self.bounds(of: day).start  // the day moved on meanwhile
            case .time(let time): anchor = time
            }
        }
        let rows = layout.rows
        var anchorRow = rows.first
        if let crossing, let row = rows.first(where: { $0.id == crossing.rowID }) { anchorRow = row }
        #if DEBUG
        // `-guideRow 6` starts on that row (6 = first Anténa row in the default order), for simulator screenshots.
        let debugRow = UserDefaults.standard.integer(forKey: "guideRow")
        if debugRow > 0, debugRow < rows.count { anchorRow = rows[debugRow] }
        #endif
        let firstRow = anchorRow?.entries ?? []
        let anchorIndex = anchorRow.flatMap { row in rows.firstIndex { $0.id == row.id } } ?? 0
        var targetID: String?
        if crossing != nil {
            // the row's cell (programme or "Mimo vysílání") at the new day's edge
            targetID = layout.cellID(row: anchorIndex, at: anchor)
        } else {
            targetID = (firstRow.first { $0.isAiring(at: anchor) } ?? firstRow.first { $0.start >= anchor } ?? firstRow.last)?.id
        }
        defaultFocusID = targetID
        focusedEntry = targetID.flatMap { layout.entries[$0] }
        focusTime = targetID.flatMap { layout.startTime(of: $0) } ?? anchor
        if let id = targetID, let span = layout.span(of: id) {
            window = Window.around(row: span.row, from: span.from, to: span.to, rowCount: rows.count)
        } else {
            let x = layout.timeline.x(for: anchor)
            window = Window.around(row: anchorIndex, from: x, to: x, rowCount: rows.count)
        }
        // "Live column first" on the grid that contains now (after midnight the previous broadcast day).
        liveColumn = crossing == nil && anchor == now
        prefetchPoint = nil
        if let targetID { prefetchImages(around: targetID) }
        let tick = layout.timeline.tickIndex(for: anchor.addingTimeInterval(crossing?.atEnd == true ? -90 * 60 : -30 * 60))
        // Let the new rows lay out before scrolling.
        let rowID = anchorRow?.id == rows.first?.id ? nil : anchorRow?.id
        Task { @MainActor in
            scrollRequest = ScrollRequest(tick: tick, rowID: rowID)
            // Focus is still in the grid (on a cell of the old day) or on the selected chip: move it to the anchor cell.
            guard crossing != nil || selected?.moveFocus == true, let targetID else { return }
            try? await Task.sleep(for: .milliseconds(150))
            focusedID = targetID
        }
    }

    /// The broadcast day shown by the grid: 05:00 to 05:00 Prague time (local 05:00, also on DST days).
    static func bounds(of day: BroadcastDay) -> (start: Date, end: Date) { GuideDays.bounds(of: day) }

    /// Label of the "Across days" cell, nil at the ends of the guide's range.
    private func edgeLabel(next: Bool, today: BroadcastDay) -> String? {
        let target = day.adding(days: next ? 1 : -1)
        guard GuideRange.days(today: today).contains(target) else { return nil }
        let label = target.label(today: today)
        return next ? "\(label) ›" : "‹ \(label)"
    }

    /// Focus reached a "‹ Včera" / "Zítra ›" cell. From its own row (left/right past the day's edge) that
    /// opens the previous/next day, focused on the same row at the new day's end/start; reached any other
    /// way (up/down) it hands focus back to the row's cell at the edge. True when `id` was such a cell.
    private func crossDay(from old: String?, to id: String) -> Bool {
        let next = id.hasPrefix("next|")
        guard next || id.hasPrefix("prev|") else { return false }
        let rowID = String(id.dropFirst(5))
        guard let row = layout.rows.firstIndex(where: { $0.id == rowID }) else { return true }
        if let old, layout.rowOf[old] == row, dayAnchor == nil {
            dayAnchor = DayAnchor(rowID: rowID, atEnd: !next)
            liveColumn = false
            focusedEntry = nil
            day = day.adding(days: next ? 1 : -1)
        } else if dayAnchor == nil {
            let bounds = Self.bounds(of: day)
            let edge = next ? bounds.end.addingTimeInterval(-60) : bounds.start
            if let target = layout.cellID(row: row, at: edge) {
                redirectID = target
                focusedID = target
            }
        }
        return true
    }

    /// Up/down (or coming down from the day strip) lands on the programme airing now in that row
    /// until the viewer first moves left or right, and afterwards whenever focus comes from an airing programme.
    private func followLiveColumn(from old: String?, to id: String) {
        if id == redirectID {
            redirectID = nil
            return
        }
        let now = Date()
        guard day == GuideDays.openDay(now: now), let row = layout.rowOf[id] else { return }
        if let old, layout.rowOf[old] == row {
            liveColumn = false  // a move within the row: normal grid navigation from now on
            return
        }
        // Up/down follows the airing programmes in the opening mode, and always from an airing programme
        // (spec/apps.md "Live column first"); from any other programme the engine keeps the time.
        let fromAiring = old.flatMap { layout.entries[$0] }?.isAiring(at: now) ?? false
        guard liveColumn || fromAiring else { return }
        if let target = layout.cellID(row: row, at: now), target != id {
            redirectID = target
            focusedID = target
        }
    }

    /// Moves the built window along when focus gets near its edge (a cheap rebuild of ~20 rows).
    private func keepWindow(around id: String) {
        guard let span = layout.span(of: id) else { return }
        let count = layout.rows.count
        if !window.covers(row: span.row, from: span.from, to: span.to, rowCount: count) {
            window = Window.around(row: span.row, from: span.from, to: span.to, rowCount: count)
        }
    }

    /// Queues the images around the focused cell once it has moved more than 1 h or 3 rows from the last point
    /// (spec/apps.md "Image prefetch"); the focused image itself is asked for by the detail, ahead of the queue.
    private func prefetchImages(around id: String) {
        guard let span = layout.span(of: id) else { return }
        let time = layout.timeline.start.addingTimeInterval((span.from + span.to) / 2 / layout.timeline.pointsPerSecond)
        let point = ImagePrefetch.Point(row: span.row, time: time)
        guard ImagePrefetch.needsRefresh(from: prefetchPoint, to: point) else { return }
        prefetchPoint = point
        let images = model.images
        let sources = ImagePrefetch.window(rows: layout.rows.map(\.entries), around: point)
            .compactMap { Self.imageSource($0.item, images: images, ctSchedule: schedule) }
        images.prefetch(sources)
    }

    /// The detail image of an entry: the ČT image; for Anténa the matched ČT image, else the gateway thumbnail.
    static func imageSource(_ entry: GuideEntry, images: GuideImageStore,
                            ctSchedule: ScheduleStore.Schedule) -> GuideImageStore.Source? {
        switch entry {
        case .ct(let programme):
            return programme.imageURL.map { .ct($0) }
        case .antenna(let event, let channel):
            if let url = AntennaCTMatch.programme(for: event, channel: channel, schedule: ctSchedule)?.imageURL {
                return .ct(url)
            }
            return images.thumbnail(for: event)
        case .parliament:
            return nil  // no thumbnails for these streams
        }
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
                choice = Choice(entry: entry, resumeAt: model.resumeOffer(for: programme), isAiring: true)
            default:
                if let resumeAt = model.resumeOffer(for: programme) {
                    choice = Choice(entry: entry, resumeAt: resumeAt, isAiring: false)
                } else {
                    model.play(.programme(programme, channel: channel))
                }
            }
        case .antenna(let event, let channel):
            switch AntennaAction.decide(event, now: now) {
            case .future:
                model.alertMessage = CTError.notStarted
            case .airing(let recordingID):
                let resumeAt = recordingID.flatMap { model.resumeOffer(recordingID: $0, event: event) }
                choice = Choice(entry: entry, resumeAt: resumeAt, isAiring: true)
            case .recording(let id):
                if let resumeAt = model.resumeOffer(recordingID: id, event: event) {
                    choice = Choice(entry: entry, resumeAt: resumeAt, isAiring: false)
                } else {
                    model.playAntennaRecording(id: id, event: event, finished: true, channelName: channel.name)
                }
            case .notRecorded:
                model.alertMessage = AntennaAction.notRecordedMessage
            }
        case .parliament(let stream, _, _):
            // Today: live, or "Poslanecká sněmovna teď nevysílá." (no choice, no resume, no start-over).
            if entry.isAiring(at: now) {
                model.playLive(.parliament(stream))
            } else {
                model.alertMessage = PSPStreams.onlyLiveMessage
            }
        }
    }

    /// The recording behind "Pokračovat" / "Od začátku" of an Anténa programme (growing or finished).
    private static func playableRecordingID(_ event: DVBEvent) -> String? {
        switch AntennaAction.decide(event, now: Date()) {
        case .airing(let id): return id
        case .recording(let id): return id
        case .future, .notRecorded: return nil
        }
    }
}

/// The EPG grid itself. Compared by layout version, minute and focus/scroll inputs, so a focus move
/// elsewhere (the detail above) does not rebuild it.
private struct GuideGrid: View, Equatable {
    let layout: GuideLayout
    let window: GuideView.Window
    let now: Date
    /// Live state of the Poslanecká sněmovna rows: the grid redraws when one changes (rarely), not on focus moves.
    let parliament: [String: PSPLiveState]
    let focusedID: FocusState<String?>.Binding
    let defaultFocusID: String?
    let scrollRequest: GuideView.ScrollRequest?
    /// "‹ Včera" / "Zítra ›" labels of the "Across days" cells, nil at the range's ends.
    let previousDay: String?
    let nextDay: String?
    let select: (GuideEntry) -> Void

    nonisolated static func == (lhs: GuideGrid, rhs: GuideGrid) -> Bool {
        lhs.layout.version == rhs.layout.version && lhs.window == rhs.window && lhs.now == rhs.now
            && lhs.parliament == rhs.parliament
            && lhs.defaultFocusID == rhs.defaultFocusID && lhs.scrollRequest == rhs.scrollRequest
            && lhs.previousDay == rhs.previousDay && lhs.nextDay == rhs.nextDay
    }

    private var leadingEdge: Double { previousDay == nil ? 0 : GuideLayout.dayEdgeWidth }
    private var trailingEdge: Double { nextDay == nil ? 0 : GuideLayout.dayEdgeWidth }

    var body: some View {
        let rows = layout.rows
        let timeline = layout.timeline
        // Vertical scrolling once the Anténa rows are added; the channel names scroll along.
        ScrollViewReader { verticalProxy in
        ScrollView(.vertical, showsIndicators: false) {
            HStack(alignment: .top, spacing: 0) {
                VStack(alignment: .leading, spacing: GuideView.rowSpacing) {
                    Color.clear
                        .frame(width: GuideView.channelColumnWidth, height: GuideView.headerHeight)
                    ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                        HStack(spacing: 8) {
                            SourceIcon(source: row.source)
                                .font(.system(size: 18))
                                .foregroundStyle(.secondary)
                                .frame(width: 24)
                            // channel number on this TV = row position (rows are the visible channels)
                            Text("\(index + 1)")
                                .font(.system(size: 20, weight: .semibold).monospacedDigit())
                                .foregroundStyle(.secondary)
                            Text(row.name)
                                .font(.system(size: 28, weight: .bold))
                                .lineLimit(1)
                                .minimumScaleFactor(0.7)
                        }
                        .frame(width: GuideView.channelColumnWidth - 12, height: GuideView.rowHeight, alignment: .leading)
                            .padding(.trailing, 12)
                            .id("row|\(row.id)")
                    }
                }
                .padding(.vertical, 6)
                ScrollViewReader { proxy in
                    ScrollView(.horizontal, showsIndicators: false) {
                        VStack(alignment: .leading, spacing: GuideView.rowSpacing) {
                            timeHeader(timeline)
                            ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                                if window.rows.contains(index) {
                                    self.row(row, index: index, now: now)
                                        .frame(height: GuideView.rowHeight)
                                } else {
                                    Color.clear.frame(width: leadingEdge + timeline.width + trailingEdge,
                                                      height: GuideView.rowHeight)
                                }
                            }
                        }
                        .overlay(alignment: .topLeading) { nowLine(timeline, now: now) }
                        .padding(.vertical, 6)
                    }
                    .focusSection()
                    .defaultFocus(focusedID, defaultFocusID)
                    // Also on first appearance (new rows and the request arrive together), after layout.
                    .task(id: scrollRequest) {
                        guard let request = scrollRequest else { return }
                        try? await Task.sleep(for: .milliseconds(50))
                        proxy.scrollTo("tick-\(request.tick)", anchor: .leading)
                        if let rowID = request.rowID { verticalProxy.scrollTo("row|\(rowID)", anchor: .top) }
                    }
                }
            }
        }
        }
    }

    private func timeHeader(_ timeline: GuideTimeline) -> some View {
        HStack(spacing: 0) {
            Color.clear.frame(width: leadingEdge)
            ForEach(Array(timeline.ticks.enumerated()), id: \.offset) { index, tick in
                Text(Formatting.time(tick))
                    .font(.system(size: 22, weight: .medium))
                    .foregroundStyle(.secondary)
                    .frame(width: GuideTimeline.tickInterval * GuideView.pointsPerSecond, alignment: .leading)
                    .id("tick-\(index)")
            }
        }
        .frame(height: GuideView.headerHeight)
    }

    /// The row's cells inside the window's x range; empty space of the same width around them.
    private func row(_ row: GuideRow, index rowIndex: Int, now: Date) -> some View {
        let segments = layout.segments[rowIndex]
        let starts = layout.starts[rowIndex]
        let visible = segments.indices.filter { index in
            let from = starts[index]
            return from < window.x.upperBound && from + GuideLayout.width(segments[index]) > window.x.lowerBound
        }
        let lead = visible.first.map { starts[$0] } ?? layout.timeline.width
        let tail = visible.last.map { layout.timeline.width - starts[$0] - GuideLayout.width(segments[$0]) } ?? 0
        return HStack(spacing: 0) {
            if let previousDay { dayEdge(previousDay, id: GuideLayout.edgeID(next: false, rowID: row.id)) }
            Color.clear.frame(width: max(0, lead))
            ForEach(visible, id: \.self) { index in
                let segment = segments[index]
                switch segment {
                case .gap(let width) where width < GuideView.minFocusableGap:
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
                    .focused(focusedID, equals: GuideLayout.gapID(row: row, index: index))
                    .accessibilityIdentifier("guide.\(rowIndex).\(index)")
                    .padding(.horizontal, 3)
                case .item(let entry, let width):
                    let live = parliamentState(entry)
                    let state = live.map(ProgrammeState.init(parliament:)) ?? entry.state(at: now)
                    Button {
                        select(entry)
                    } label: {
                        Group {
                            if let live {
                                ParliamentCellLabel(state: live, today: entry.isAiring(at: now))
                            } else {
                                EntryCellLabel(start: entry.start, title: entry.title, state: state)
                            }
                        }
                        .frame(width: max(width - 6, 2))
                    }
                    .buttonStyle(GuideCellStyle(state: state))
                    .focused(focusedID, equals: entry.id)
                    .accessibilityIdentifier("guide.\(rowIndex).\(index)")
                    .padding(.horizontal, 3)
                }
            }
            Color.clear.frame(width: max(0, tail))
            if let nextDay { dayEdge(nextDay, id: GuideLayout.edgeID(next: true, rowID: row.id)) }
        }
    }

    /// nil for other cells; `.off` look (with "Jen přímé přenosy") on days other than today.
    private func parliamentState(_ entry: GuideEntry) -> PSPLiveState? {
        guard case .parliament(let stream, _, _) = entry else { return nil }
        return entry.isAiring(at: now) ? parliament[stream.key] ?? .unknown : .off
    }

    /// "‹ Včera" / "Zítra ›" at a row's start / end: moving onto it opens that day (GuideView.crossDay).
    private func dayEdge(_ label: String, id: String) -> some View {
        Button {} label: {
            Text(label)
                .font(.system(size: 22, weight: .semibold))
                .lineLimit(1)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .buttonStyle(GuideCellStyle(state: .offAir))
        .focused(focusedID, equals: id)
        .frame(width: GuideLayout.dayEdgeWidth - 6)
        .padding(.horizontal, 3)
    }

    @ViewBuilder
    private func nowLine(_ timeline: GuideTimeline, now: Date) -> some View {
        if timeline.contains(now) {
            Rectangle()
                .fill(Color.red)
                .frame(width: 3)
                .padding(.top, GuideView.headerHeight - 8)
                .offset(x: leadingEdge + timeline.x(for: now) - 1.5)
                .allowsHitTesting(false)
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

/// A Poslanecká sněmovna cell: "Přímý přenos" / "Nevysílá se" / "Zjišťuji…" over the whole day.
/// Other days: "Jen přímé přenosy".
private struct ParliamentCellLabel: View {
    let state: PSPLiveState
    let today: Bool

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: state == .live ? ProgrammeState.airing.symbol : "building.columns")
                .font(.system(size: 18))
            Text(today ? AppModel.parliamentText(state) : PSPStreams.onlyLiveText)
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
            .shadow(color: .black.opacity(isFocused ? 0.5 : 0), radius: isFocused ? 12 : 0, y: isFocused ? 6 : 0)
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

/// Fixed layout (spec/apps.md): constant height, every line in a constant place whatever the programme, so nothing
/// moves while the viewer clicks through cells. Empty episode / status lines and short descriptions keep their space.
private struct EntryDetail: View {
    /// Tall enough for the whole text column (title, episode, meta, status, two description lines ≈ 228 pt at the
    /// tvOS text sizes). At 203 (the image height) the column overflowed whenever a programme had a description and
    /// SwiftUI centred the overflow, so every detail with a description sat ~12 pt higher than one without (T09).
    static let height: CGFloat = 236
    static let imageSize = CGSize(width: 360, height: 203)

    let entry: GuideEntry?
    let today: BroadcastDay
    let now: Date
    /// The day's CT schedule, for the ČT image of an Anténa ČT programme (spec/apps.md "ČT images for
    /// Anténa ČT channels"); matched only for the focused entry.
    let ctSchedule: ScheduleStore.Schedule
    let officialAppFocused: FocusState<Bool>.Binding
    let openOfficialApp: () -> Void
    @Environment(AppModel.self) private var model

    var body: some View {
        if let entry {
            let live = parliamentState(entry)
            let state = live.map(ProgrammeState.init(parliament:)) ?? entry.state(at: now)
            HStack(alignment: .top, spacing: 36) {
                artwork(entry)
                    .frame(width: Self.imageSize.width, height: Self.imageSize.height)
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))

                VStack(alignment: .leading, spacing: 4) {
                    Text(entry.title)
                        .font(.title3.weight(.bold))
                        .lineLimit(1)
                    line(episode(entry))
                        .font(.subheadline)
                    line(meta(entry))
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    Label(live.map { parliamentText(entry, state: $0) } ?? statusText(entry, state: state), systemImage: state.symbol)
                        .font(.subheadline.weight(.semibold))
                        .lineLimit(1)
                        .foregroundStyle(state == .airing ? .red : (state == .buffer || state == .archive ? .green : .secondary))
                    // An empty Text takes no height even with reservesSpace: keep the two lines with a space.
                    Text(description(entry).isEmpty ? " " : description(entry))
                        .font(.system(size: 25))
                        .foregroundStyle(.secondary)
                        .lineLimit(2, reservesSpace: true)
                }
                .frame(height: Self.height, alignment: .top)
                Spacer(minLength: 0)
                // For CT programmes we cannot play (DRM-only ones show CT's slate in our stream). Its place is
                // kept when there is none, so the text beside it never changes width.
                let showsButton = if case .ct(let programme) = entry { programme.start <= now } else { false }
                Button(OfficialApp.buttonTitle, action: openOfficialApp)
                    .focused(officialAppFocused)
                    .font(.system(size: 24, weight: .semibold))
                    .opacity(showsButton ? 1 : 0)
                    .disabled(!showsButton)
                    .accessibilityHidden(!showsButton)
            }
            .frame(maxWidth: .infinity, minHeight: Self.height, maxHeight: Self.height, alignment: .topLeading)
            // Vertical order grid ↔ day strip ↔ this button: up from any chip reaches it, down from it the strip.
            .focusSection()
        } else {
            Text("Žádný pořad")
                .font(.title3)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        }
    }

    private func parliamentState(_ entry: GuideEntry) -> PSPLiveState? {
        guard case .parliament(let stream, _, _) = entry else { return nil }
        return entry.isAiring(at: now) ? model.parliament.state(of: stream.key) : .off
    }

    private func parliamentText(_ entry: GuideEntry, state: PSPLiveState) -> String {
        entry.isAiring(at: now) ? AppModel.parliamentText(state) : PSPStreams.onlyLiveText
    }

    /// One line that keeps its height when empty.
    private func line(_ text: String) -> some View {
        Text(text.isEmpty ? " " : text).lineLimit(1)
    }

    @ViewBuilder
    private func artwork(_ entry: GuideEntry) -> some View {
        let source = GuideView.imageSource(entry, images: model.images, ctSchedule: ctSchedule)
        switch entry {
        case .ct:
            GuideArtwork(source: source) { Color(white: 0.15) }
        case .antenna(_, let channel):
            // ČT image (official, matched to the CT schedule) → gateway thumbnail → placeholder
            GuideArtwork(source: source) {
                ZStack {
                    Color(white: 0.15)
                    VStack(spacing: 8) {
                        Image(systemName: "antenna.radiowaves.left.and.right").font(.system(size: 44))
                        Text(channel.name).font(.headline)
                    }
                    .foregroundStyle(.secondary)
                }
            }
        case .parliament(let stream, _, _):
            // a generic symbol, never the Chamber's logo or the state emblem
            ZStack {
                Color(white: 0.15)
                VStack(spacing: 8) {
                    Image(systemName: "building.columns").font(.system(size: 44))
                    Text(stream.name).font(.headline)
                }
                .foregroundStyle(.secondary)
            }
        }
    }

    private func episode(_ entry: GuideEntry) -> String {
        switch entry {
        case .ct(let programme): return programme.episodeTitle
        case .antenna(let event, _): return event.subtitle
        case .parliament: return ""
        }
    }

    private func description(_ entry: GuideEntry) -> String {
        switch entry {
        case .ct(let programme): return programme.description
        case .antenna(let event, _): return event.description
        case .parliament: return ""  // no programme data
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
        if case .parliament = entry { return "Zdroj: \(PSPStreams.sourceLabel)" }
        let channel: String
        let genre: String
        switch entry {
        case .ct(let programme):
            channel = Channels.bySlug(programme.channel)?.name ?? programme.channel
            genre = programme.genre
        case .antenna(let event, let dvbChannel):
            channel = "\(dvbChannel.name) (anténa)"
            genre = event.genre
        case .parliament:
            return ""
        }
        let day = BroadcastDay(containing: entry.start).label(today: today)
        let range = "\(Formatting.time(entry.start))–\(Formatting.time(entry.end))"
        return [channel, "\(day) \(range)", genre]
            .filter { !$0.isEmpty }
            .joined(separator: "  ·  ")
    }
}

/// The detail image from the guide's in-memory image store (spec/apps.md "Image prefetch"): usually prefetched,
/// else asked for at once, ahead of the queue; the placeholder until it is there.
private struct GuideArtwork<Placeholder: View>: View {
    let source: GuideImageStore.Source?
    @ViewBuilder let placeholder: Placeholder
    @Environment(AppModel.self) private var model

    var body: some View {
        ZStack {
            if let source, let image = model.images.image(source) {
                Image(uiImage: image).resizable().aspectRatio(contentMode: .fill)
            } else {
                placeholder
            }
        }
        .onChange(of: source, initial: true) { _, source in
            if let source { model.images.want(source) }
        }
    }
}
