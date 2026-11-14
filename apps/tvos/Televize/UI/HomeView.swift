import CTCore
import SwiftUI

/// "Domů", the first tab (spec/apps.md "Domů", T58): today's date and calendar, the gateway's notice, "Pokračovat",
/// "Teď běží" (the first ten channels) and the Chamber's streams that are on. Nothing new is fetched: the on-air data
/// is Živě's (`LiveNowStore`), the row is the resume store's, the notice the last `/dvb/status`.
struct HomeView: View {
    /// "Další kanály": to Živě.
    let openLive: () -> Void

    @Environment(AppModel.self) private var model
    @FocusState private var focus: HomeFocus?
    /// The column of the focused card: up/down keep it as far as the next row allows.
    @State private var column = 0
    /// Bumped by "Odebrat z řady" so the row is read again.
    @State private var removals = 0
    @State private var focusedAtStart = false

    enum HomeFocus: Hashable {
        case resume(String)
        case live(String)
        case more
        case parliament(String)
    }

    var body: some View {
        let _ = model.playerCloses  // a closed player saved a new position
        let _ = removals
        let live = model.liveNow
        let visible = model.channelPrefs.channels(antenna: live.antennaChannels).visible
        let liveRow = HomeRules.liveRow(visible)  // the Chamber's streams have their own row
        let streams = visible.compactMap(\.parliamentStream)
        let onAirStreams = streams.filter { model.parliament.state(of: $0.key) == .live }
        let resume = model.resume.continueRow()  // exactly Archiv's row
        let resumeItems = resume.map { HomeFocus.resume($0.key) }
        let liveItems = liveRow.isEmpty ? [] : liveRow.map { HomeFocus.live($0.key) } + [.more]
        let parliamentItems = onAirStreams.map { HomeFocus.parliament($0.key) }
        let rows = [resumeItems, liveItems, parliamentItems].filter { !$0.isEmpty }
        // the numbers of "Kanály" (the Chamber's streams count there)
        let number = { (channel: MixedChannel) in (visible.firstIndex(of: channel) ?? 0) + 1 }
        return TimelineView(.periodic(from: .now, by: 30)) { context in
            ScrollView {
                VStack(alignment: .leading, spacing: 44) {
                    HomeHeader(header: HomeCalendar.header(for: BroadcastDay(containing: context.date)))
                    if let notice = HomeRules.gatewayNotice(configured: model.savedGateway != nil,
                                                            tailnetLine: model.tailnet.screenLine,
                                                            failure: model.images.statusFailure,
                                                            recordingPaused: model.images.recordingPaused) {
                        Group {
                            if notice.isWarning {
                                Label(notice.text, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.yellow)
                            } else {
                                Text(notice.text).foregroundStyle(.secondary)
                            }
                        }
                        .font(.headline)
                        .accessibilityIdentifier("home.notice")
                    }
                    if !resume.isEmpty {
                        row(HomeRules.continueTitle, items: resumeItems) {
                            ForEach(resume, id: \.key) { item in
                                Button {
                                    model.playContinue(key: item.key, entry: item.entry)
                                } label: {
                                    ContinueCardView(entry: item.entry).equatable()
                                }
                                .buttonStyle(.card)
                                .focused($focus, equals: .resume(item.key))
                                .contextMenu {
                                    Button(ArchiveRules.removeFromRow, role: .destructive) {
                                        model.resume.remove(item.key)
                                        removals += 1
                                    }
                                }
                                .accessibilityIdentifier("home.resume.\(item.key)")
                            }
                        }
                    }
                    if !liveRow.isEmpty {
                        row(HomeRules.liveTitle, items: liveItems) {
                            ForEach(liveRow) { channel in
                                LiveChannelButton(channel: channel, number: number(channel), now: context.date)
                                    .focused($focus, equals: .live(channel.key))
                                    .accessibilityIdentifier("home.live.\(channel.key)")
                            }
                            Button(action: openLive) {
                                VStack(spacing: 18) {
                                    Image(systemName: "square.grid.3x3.fill").font(.system(size: 54))
                                    Text(HomeRules.moreChannels).font(.title3.weight(.semibold))
                                }
                                .foregroundStyle(.secondary)
                                .frame(width: 300, height: 280)
                            }
                            .buttonStyle(.card)
                            .focused($focus, equals: .more)
                            .accessibilityIdentifier("home.more")
                        }
                    }
                    if !onAirStreams.isEmpty {
                        row(HomeRules.parliamentTitle, items: parliamentItems) {
                            ForEach(onAirStreams, id: \.key) { stream in
                                let channel = MixedChannel.parliament(stream)
                                LiveChannelButton(channel: channel, number: number(channel), now: context.date)
                                    .focused($focus, equals: .parliament(stream.key))
                                    .accessibilityIdentifier("home.psp.\(stream.key)")
                            }
                        }
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 40)
            }
            // the first card of the first row when the content gets the focus from the tab bar
            .defaultFocus($focus, rows.first?.first)
        }
        .onChange(of: focus) { _, focused in
            if let focused, let index = rows.lazy.compactMap({ $0.firstIndex(of: focused) }).first { column = index }
        }
        // App start: the first card of the first row, once something is there to focus.
        .onChange(of: rows.first?.first, initial: true) { _, first in
            guard !focusedAtStart, let first, model.session == nil else { return }
            focusedAtStart = true
            focus = first
        }
        // "Is it on now?" of the Chamber's streams, only while this screen is on screen.
        .watchesParliament(streams)
        // What is on air, shared with Živě: one loop while either is on screen.
        .refreshesLiveNow()
        // the gateway's notice: the status as everywhere, at most every 5 minutes
        .task(id: model.gateway) { await model.images.refreshStatus() }
    }

    /// A titled row of cards scrolling sideways; up/down into it land on the card in the current column.
    private func row(_ title: String, items: [HomeFocus], @ViewBuilder cards: () -> some View) -> some View {
        let target = items.isEmpty ? nil : items[min(column, items.count - 1)]
        return VStack(alignment: .leading, spacing: 20) {
            Text(title).font(.title3.weight(.semibold)).foregroundStyle(.secondary)
            ScrollView(.horizontal) {
                HStack(spacing: 48, content: cards)
                    .padding(.vertical, 20)
            }
            .scrollClipDisabled()
        }
        .focusSection()
        .defaultFocus($focus, target, priority: .userInitiated)
    }
}

/// Today: "středa 4. listopadu", the name day or the state holiday, at most two calendar lines. Not focusable.
private struct HomeHeader: View, Equatable {
    let header: HomeCalendar.Header

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(header.fullDate)
                .font(.system(size: 52, weight: .bold))
                .accessibilityIdentifier("home.date")
            if let name = header.nameLine {
                Text(name).font(.title3)
            }
            ForEach(header.lines, id: \.self) { line in
                Text(line).font(.headline).foregroundStyle(.secondary)
            }
        }
    }
}
