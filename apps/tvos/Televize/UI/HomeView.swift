import CTCore
import SwiftUI

/// "Domů", the first tab (spec/apps.md "Domů", T58): today's date and calendar, the gateway's notice, "Pokračovat",
/// "Teď běží" (the first ten channels), the Chamber's streams that are on and the week's weather (T59). Nothing new is
/// fetched for the rows: the on-air data is Živě's (`LiveNowStore`), the row is the resume store's, the notice the last
/// `/dvb/status`; the weather is `/dvb/weather` when its last answer is older than 30 minutes.
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
        let weather = model.weather.weather
        let lastRow = rows.last
        return TimelineView(.periodic(from: .now, by: 30)) { context in
            ScrollViewReader { scroller in
                ScrollView {
                    VStack(alignment: .leading, spacing: 44) {
                        HStack(alignment: .top, spacing: 80) {
                            HomeHeader(header: HomeCalendar.header(for: BroadcastDay(containing: context.date)))
                            if let weather {
                                WeatherNowView(weather: weather, now: context.date).equatable()
                            }
                        }
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
                        let today = BroadcastDay(containing: context.date)
                        if let days = weather.map({ WeatherRules.weekDays($0.days, today: today) }), !days.isEmpty {
                            WeatherWeekView(days: days, today: today).equatable()
                                .id(Self.weekID)
                        }
                    }
                    .padding(.horizontal, 20)
                    .padding(.vertical, 40)
                }
                // the first card of the first row when the content gets the focus from the tab bar
                .defaultFocus($focus, rows.first?.first)
                // The week is no focus stop: on the last row the screen scrolls to the end so it is seen.
                .onChange(of: focus) { _, focused in
                    guard weather != nil, let focused, lastRow?.contains(focused) == true else { return }
                    withAnimation { scroller.scrollTo(Self.weekID, anchor: .bottom) }
                }
            }
        }
        .onChange(of: focus) { _, focused in
            if let focused, let index = rows.lazy.compactMap({ $0.firstIndex(of: focused) }).first { column = index }
        }
        // the weather when its last answer is older than 30 minutes; the location question once
        .task(id: model.gateway) { await model.weather.domuShown() }
        // The remote's Menu closes it: no answer (asked again at the next start, meanwhile the household place); the
        // buttons answer before the binding hears of the closing. Not an alert: on tvOS Menu closes one only through
        // a third, cancel button.
        .fullScreenCover(isPresented: Binding(get: { model.weather.showsConsent && model.session == nil },
                                              set: { if !$0, model.weather.showsConsent { answerConsent(.none) } })) {
            WeatherConsentView { answerConsent($0) }
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

    private static let weekID = "home.week"

    private func answerConsent(_ answer: WeatherConsent.Answer) {
        model.weather.answer(answer)
        Task { await model.weather.domuShown() }
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

/// Weather now next to the date (spec/apps.md "Domů" 1): icon, "8 °C, polojasno", "Slunce 7:13–16:19", air, pollen,
/// ČHMÚ warnings in their colour and the attribution. Not focusable.
private struct WeatherNowView: View, Equatable {
    let weather: Weather
    /// Passed warnings are hidden; one ending on another day names the day.
    let now: Date

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 20) {
                WeatherIcon(key: weather.now.icon).font(.system(size: 52))
                Text(WeatherRules.nowLine(weather.now))
                    .font(.system(size: 40, weight: .semibold))
                    .accessibilityIdentifier("home.weather.now")
            }
            ForEach([WeatherRules.sunLine(weather), WeatherRules.airLine(weather), WeatherRules.pollenLine(weather)]
                .compactMap { $0 }, id: \.self) { line in
                Text(line).font(.headline).foregroundStyle(.secondary)
            }
            ForEach(Array(WeatherRules.visibleWarnings(weather.warnings, now: now).enumerated()), id: \.offset) { _, warning in
                Label(WeatherRules.warningLine(warning, now: now), systemImage: "exclamationmark.triangle.fill")
                    .font(.headline)
                    .foregroundStyle(Self.color(warning.level))
            }
            if let attribution = weather.attribution {
                Text(attribution).font(.caption2).foregroundStyle(.tertiary)
            }
        }
    }

    static func color(_ level: Weather.Warning.Level?) -> Color {
        switch level {
        case .yellow: .yellow
        case .orange: .orange
        case .red: .red
        case nil: .secondary
        }
    }
}

/// Block 8 "Počasí na týden": the seven days as cards, not a focus stop.
private struct WeatherWeekView: View, Equatable {
    let days: [Weather.Day]
    let today: BroadcastDay

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(WeatherRules.weekTitle).font(.title3.weight(.semibold)).foregroundStyle(.secondary)
            HStack(spacing: 24) {
                ForEach(days, id: \.date) { day in
                    VStack(spacing: 12) {
                        Text(WeatherRules.dayLabel(day.date, today: today)).font(.headline)
                        WeatherIcon(key: day.icon).font(.system(size: 54)).frame(height: 64)
                        Text(WeatherRules.rangeLine(day) ?? "–").font(.title3.weight(.semibold))
                        Text(WeatherRules.rainLine(day) ?? " ").font(.callout).foregroundStyle(.secondary)
                    }
                    .frame(width: 220)
                    .padding(.vertical, 24)
                    .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 16))
                }
            }
            .accessibilityIdentifier("home.week")
        }
    }
}

/// The gateway's icon key as an SF Symbol; an unknown key is a cloud.
private struct WeatherIcon: View {
    let key: String

    var body: some View {
        Image(systemName: Self.symbol(key)).symbolRenderingMode(.multicolor)
    }

    static func symbol(_ key: String) -> String {
        switch key {
        case "sun": "sun.max.fill"
        case "partly": "cloud.sun.fill"
        case "fog": "cloud.fog.fill"
        case "drizzle": "cloud.drizzle.fill"
        case "rain": "cloud.rain.fill"
        case "snow": "cloud.snow.fill"
        case "storm": "cloud.bolt.rain.fill"
        default: "cloud.fill"
        }
    }
}

/// The one-time question before the OS is asked for the location (spec/apps.md "Location for weather"), on Domů and
/// when the switch in Nastavení → Server is turned on. Menu closes it without an answer.
struct WeatherConsentView: View {
    let answer: (WeatherConsent.Answer) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 40) {
            Text(WeatherRules.consentTitle).font(.title2.weight(.bold))
                .accessibilityIdentifier("weather.consent")
            Text(WeatherRules.consentText).font(.title3)
            HStack(spacing: 40) {
                Button(WeatherRules.consentAllow) { answer(.allow) }
                    .accessibilityIdentifier("weather.consent.allow")
                Button(WeatherRules.consentDeny) { answer(.deny) }
                    .accessibilityIdentifier("weather.consent.deny")
            }
        }
        .frame(maxWidth: 1100, alignment: .leading)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(white: 0.08).ignoresSafeArea())
    }
}
