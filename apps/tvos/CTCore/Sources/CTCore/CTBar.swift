import Foundation

// "ČT player bar" (spec/apps.md, T62): ČT start-over / "Zpět v čase" and the ČT archive play with the Anténa
// timeline player's bar and keys, over one window per playback (no previous / next, the edges clamp, no ad breaks).
// Golden cases: spec/fixtures/ct_bar_cases.json (`window`, `clamp`, `labels`).

/// What the timeline bar shows and seeks in: an Anténa window of the continuous recording (T05) or the one window
/// of a ČT playback (T62). Positions are the player's seconds, times wall clock.
public struct BarWindow: Sendable, Equatable {
    /// Where seeks and the moving playhead may go.
    public enum Limits: Sendable, Equatable {
        /// Anténa: the edge rule (`TimelineRules.edge`: previous / next, else clamp at 0 and the end).
        case edges
        /// ČT timeshift: wall clock [now − 3 h + 60 s, now − 10 s]; `origin` = the wall clock of position 0, `lag` = how
        /// far the stream's live edge is behind the real now (its "now", see `ctTimeshift`).
        case timeshift(origin: Date, lag: TimeInterval)
        /// ČT archive: positions [0, duration − 10 s].
        case vod(duration: Double)
    }

    public let id: String
    public let channel: String
    public let map: TimelineMap
    /// Sorted by start.
    public let programmes: [DVBTimeline.Programme]
    public let complete: Bool
    public let previous: String?
    public let next: String?
    public let limits: Limits
    /// A ČT archive item without a known start: the labels are the elapsed position, not a wall clock.
    public let elapsedClock: Bool

    public init(id: String, channel: String, map: TimelineMap, programmes: [DVBTimeline.Programme], complete: Bool,
                previous: String? = nil, next: String? = nil, limits: Limits = .edges, elapsedClock: Bool = false) {
        self.id = id
        self.channel = channel
        self.map = map
        self.programmes = programmes.sorted { $0.start < $1.start }
        self.complete = complete
        self.previous = previous
        self.next = next
        self.limits = limits
        self.elapsedClock = elapsedClock
    }

    /// The Anténa window as the gateway described it.
    public init(_ timeline: DVBTimeline) {
        self.init(id: timeline.id, channel: timeline.channel, map: timeline.map, programmes: timeline.programmes,
                  complete: timeline.complete, previous: timeline.previous, next: timeline.next)
    }

    /// The window's first and last wall clock (golden `window`); nil for an archive item without a known start.
    public var start: Date? { elapsedClock ? nil : map.startTime }
    public var end: Date? { elapsedClock ? nil : map.endTime }
    public var growing: Bool { !complete }

    /// ČT timeshift: the buffer `now − 3 h` … `now`, growing, its start sliding with `now`. `origin` is the wall
    /// clock of player position 0 (from `PROGRAM-DATE-TIME`, else estimated from the live edge). `now` is the stream's
    /// live edge where the player knows it (its newest segment is some seconds behind the real now: a wall-clock
    /// "now − 10 s" lies past what can be played), `lag` how far that is behind the real now.
    public static func ctTimeshift(id: String, channel: String, origin: Date, now: Date, lag: TimeInterval = 0,
                                   programmes: [DVBTimeline.Programme]) -> BarWindow {
        let start = now.addingTimeInterval(-CTBarRules.buffer)
        let piece = DVBTimeline.Piece(position: start.timeIntervalSince(origin), time: start, duration: CTBarRules.buffer)
        return BarWindow(id: id, channel: channel, map: TimelineMap(pieces: [piece], growing: true),
                         programmes: programmes, complete: false, limits: .timeshift(origin: origin, lag: max(0, lag)))
    }

    /// ČT archive: `start` … `start + duration`, complete; without a known start (nil) the labels are elapsed
    /// positions (the axis then counts from a fixed placeholder time). The programme is the whole window.
    public static func ctVOD(id: String, channel: String, start: Date?, duration: Double, title: String,
                             subtitle: String = "") -> BarWindow {
        let origin = start ?? CTBarRules.unknownStart
        let length = max(duration, 1)
        let piece = DVBTimeline.Piece(position: 0, time: origin, duration: length)
        let programme = DVBTimeline.Programme(id: id, title: title, subtitle: subtitle, start: origin,
                                              end: origin.addingTimeInterval(length))
        return BarWindow(id: id, channel: channel, map: TimelineMap(pieces: [piece]), programmes: [programme],
                         complete: true, limits: .vod(duration: duration), elapsedClock: start == nil)
    }

    /// The positions seeks may reach at the real `now` (golden `clamp`); nil = the Anténa edge rule. The timeshift's
    /// bounds slide with `now`, also while rewind / fast-forward moves.
    public func positionLimits(now: Date) -> ClosedRange<Double>? {
        switch limits {
        case .edges:
            return nil
        case .timeshift(let origin, let lag):
            let range = CTBarRules.timeshiftRange(now: now.addingTimeInterval(-lag))
            let lower = range.lowerBound.timeIntervalSince(origin)
            return lower...max(lower, range.upperBound.timeIntervalSince(origin))
        case .vod(let duration):
            return CTBarRules.vodRange(duration: duration)
        }
    }

    /// `target` inside `positionLimits`; unchanged for an Anténa window.
    public func clamped(_ target: Double, now: Date) -> Double {
        guard let range = positionLimits(now: now) else { return target }
        return min(max(target, range.lowerBound), range.upperBound)
    }

    /// The playhead's label: the wall clock ("Dnes 21:14:30"), or the elapsed position ("0:12:34").
    public func clockLabel(position: Double, now: Date, calendar: Calendar = .prague) -> String {
        if elapsedClock { return CTBarRules.elapsedLabel(position - (map.pieces.first?.position ?? 0)) }
        return TimelineRules.label(map.time(at: position) ?? now, now: now, calendar: calendar)
    }

    /// The bar's end labels (`TimelineRules.edgeLabels`, or `0:00` and the duration without a known start).
    public func edgeLabels(end: Date, playhead: Date, now: Date, calendar: Calendar = .prague) -> (start: String, end: String) {
        let first = map.startTime ?? now
        if elapsedClock { return CTBarRules.elapsedEdges(duration: end.timeIntervalSince(first)) }
        // A timeshift's end is "Teď" at the stream's live edge, however far that lags the real now.
        var edgeNow = now
        if case .timeshift(_, let lag) = limits { edgeNow = now.addingTimeInterval(-lag) }
        return TimelineRules.edgeLabels(start: first, end: end, playhead: playhead, now: edgeNow, growing: growing,
                                        calendar: calendar)
    }
}

public enum CTBarRules {
    /// The "Zpět v čase" buffer.
    public static let buffer = DecisionRules.timeshiftWindow
    /// The buffer's oldest minute may already be gone from the CDN.
    public static let oldestMargin: TimeInterval = 60
    /// The existing live-edge guard of start-over.
    public static let liveEdgeMargin: TimeInterval = 10
    /// An archive item's last seconds are not a seek target.
    public static let endMargin: Double = 10
    /// What `ArchiveRules.programme` and "Pokračovat" use for an item without a broadcast time.
    public static let unknownStart = Date(timeIntervalSince1970: 0)

    /// Whether a resolved ČT stream plays with the bar: start-over / timeshift and the archive, never live.
    public static func usesBar(_ mode: PlaybackSource.Mode) -> Bool {
        switch mode {
        case .startOver, .vod: return true
        case .live, .recording: return false
        }
    }

    /// The programme's start for the bar's wall clock; nil when it is the placeholder of an item without one.
    public static func knownStart(_ start: Date) -> Date? {
        start.timeIntervalSince(unknownStart) > 86_400 ? start : nil
    }

    /// Timeshift seek targets: wall clock [now − 3 h + 60 s, now − 10 s].
    public static func timeshiftRange(now: Date) -> ClosedRange<Date> {
        let lower = now.addingTimeInterval(-buffer + oldestMargin)
        return lower...max(lower, now.addingTimeInterval(-liveEdgeMargin))
    }

    public static func clamp(timeshift target: Date, now: Date) -> Date {
        let range = timeshiftRange(now: now)
        return min(max(target, range.lowerBound), range.upperBound)
    }

    /// Archive seek targets: positions [0, duration − 10 s].
    public static func vodRange(duration: Double) -> ClosedRange<Double> { 0...max(0, duration - endMargin) }

    public static func clamp(vod target: Double, duration: Double) -> Double {
        let range = vodRange(duration: duration)
        return min(max(target, range.lowerBound), range.upperBound)
    }

    /// The elapsed position "0:12:34" (`H:MM:SS`, seconds rounded down).
    public static func elapsedLabel(_ position: Double) -> String {
        let total = max(0, Int(position.isFinite ? position.rounded(.down) : 0))
        return String(format: "%d:%02d:%02d", total / 3600, total % 3600 / 60, total % 60)
    }

    /// The bar's ends without a wall clock: "0:00" and the duration "H:MM" (whole minutes, rounded down).
    public static func elapsedEdges(duration: Double) -> (start: String, end: String) {
        let minutes = max(0, Int(duration.isFinite ? (duration / 60).rounded(.down) : 0))
        return ("0:00", String(format: "%d:%02d", minutes / 60, minutes % 60))
    }

    /// Timeshift "Pokračovat": the programme at the playhead `time` with an id (idec) and its progress (≥ 0). In the
    /// start margin before the opened programme (start-over begins 30 s early) it is the opened one at 0, so the
    /// programme before it is not stored as watched to its end.
    public static func resumeProgramme(at time: Date, in programmes: [DVBTimeline.Programme],
                                       opened: (id: String, start: Date)) -> (id: String, progress: TimeInterval)? {
        if time < opened.start, time >= opened.start.addingTimeInterval(-DecisionRules.startMargin) {
            return (opened.id, 0)
        }
        guard let index = TimelineRules.programmeAt(time, in: programmes), let id = programmes[index].id else { return nil }
        return (id, TimelineRules.resumeProgress(time: time, programmeStart: programmes[index].start))
    }

    /// A subtitle item of the long-OK list: "Titulky: <name>", else the language in Czech, else "Titulky <n>"
    /// (`index` from 1); `nil` option = "Titulky: Vypnuto".
    public static func subtitleTitle(name: String?, language: String?, index: Int) -> String {
        if let name = name?.trimmingCharacters(in: .whitespaces), !name.isEmpty { return "Titulky: \(name)" }
        if let code = language?.split(separator: "-").first.map(String.init), !code.isEmpty,
           let czech = Locale(identifier: "cs").localizedString(forLanguageCode: code), czech.lowercased() != code.lowercased() {
            return "Titulky: \(czech)"
        }
        return "Titulky \(index)"
    }

    public static let subtitlesOff = "Titulky: Vypnuto"

    /// The long-OK list's title: "Zvuk a titulky" only when the stream has subtitles.
    public static func trackListTitle(hasSubtitles: Bool) -> String { hasSubtitles ? "Zvuk a titulky" : "Zvuk" }

    /// The timeshift bar's programmes: the channel's guide, plus the opened programme when the guide lacks it (so its
    /// "Pokračovat" is still remembered). Ids are the `idec`s.
    public static func programmes(guide: [Programme], opened: Programme) -> [DVBTimeline.Programme] {
        var list = guide.filter { $0.end > $0.start }
        if !list.contains(where: { $0.idec == opened.idec }), opened.end > opened.start { list.append(opened) }
        return list.map {
            DVBTimeline.Programme(id: $0.idec.isEmpty ? nil : $0.idec, title: $0.title, subtitle: $0.episodeTitle,
                                  start: $0.start, end: $0.end)
        }.sorted { $0.start < $1.start }
    }
}
