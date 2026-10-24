import Foundation

// "Anténa timeline player" (spec/apps.md): a `b…` programme of the continuous recording plays inside a
// window of its channel's recording (`GET /dvb/timeline/<id>`, spec/dvb-gateway.md "Channel timeline").
// Golden cases: spec/fixtures/timeline_cases.json.

/// One window of a channel's recording as the gateway describes it.
public struct DVBTimeline: Sendable, Equatable {
    /// A recorded stretch: `duration` seconds of playlist from `position` on, first frame at `time`.
    public struct Piece: Sendable, Equatable {
        public let position: Double
        public let time: Date
        public let duration: Double

        public init(position: Double, time: Date, duration: Double) {
            self.position = position
            self.time = time
            self.duration = duration
        }

        public var endPosition: Double { position + duration }
        public var endTime: Date { time.addingTimeInterval(duration) }
    }

    /// An EPG programme overlapping the window; `id` only when it is a playable `b…` programme.
    public struct Programme: Sendable, Equatable {
        public let id: String?
        public let title: String
        public let subtitle: String
        public let start: Date
        public let end: Date
        /// The gateway stores ad breaks for it (`/dvb/recording/<id>/breaks`).
        public let breaks: Bool

        public init(id: String?, title: String, subtitle: String = "", start: Date, end: Date, breaks: Bool = false) {
            self.id = id
            self.title = title
            self.subtitle = subtitle
            self.start = start
            self.end = end
            self.breaks = breaks
        }

        public var length: TimeInterval { end.timeIntervalSince(start) }
    }

    public let id: String
    public let channel: String
    /// Gateway path of the window's HLS playlist ("/dvb/timeline/<id>.m3u8").
    public let playlist: String
    /// False while the window's end is still being recorded (re-fetch every `refreshInterval`).
    public let complete: Bool
    public let programmeStart: Date
    public let programmeEnd: Date
    public let map: TimelineMap
    /// Sorted by start.
    public let programmes: [Programme]
    public let previous: String?
    public let next: String?

    public init(id: String, channel: String, playlist: String, complete: Bool, programmeStart: Date, programmeEnd: Date,
                pieces: [Piece], programmes: [Programme], previous: String?, next: String?) {
        self.id = id
        self.channel = channel
        self.playlist = playlist
        self.complete = complete
        self.programmeStart = programmeStart
        self.programmeEnd = programmeEnd
        self.map = TimelineMap(pieces: pieces, growing: !complete)
        self.programmes = programmes.sorted { $0.start < $1.start }
        self.previous = previous
        self.next = next
    }

    public static let refreshInterval: TimeInterval = 60
}

/// Playlist position ↔ wall clock through the window's pieces (golden `map`).
public struct TimelineMap: Sendable, Equatable {
    /// In playlist order, never empty (the parser rejects a window without media).
    public let pieces: [DVBTimeline.Piece]
    /// A growing window: beyond its last piece the time runs on with the position.
    public let growing: Bool

    public init(pieces: [DVBTimeline.Piece], growing: Bool = false) {
        self.pieces = pieces
        self.growing = growing
    }

    /// Seconds of playlist the pieces cover.
    public var duration: Double { pieces.last?.endPosition ?? 0 }
    public var startTime: Date? { pieces.first?.time }
    public var endTime: Date? { pieces.last?.endTime }

    /// Wall clock at `position`: a boundary belongs to the later piece; outside the window clamps
    /// (a growing window runs on past its last piece).
    public func time(at position: Double) -> Date? {
        guard let first = pieces.first, let last = pieces.last else { return nil }
        if position <= first.position { return first.time }
        if position >= last.endPosition {
            return last.time.addingTimeInterval(growing ? position - last.position : last.duration)
        }
        let piece = pieces.last { $0.position <= position } ?? first
        return piece.time.addingTimeInterval(min(position - piece.position, piece.duration))
    }

    /// Like `position(at:)`, but a time outside the window stays outside: before the start a negative position, after
    /// the end the duration plus the distance, so the edge rule can open `previous` / `next` for it.
    public func unclampedPosition(at time: Date) -> Double {
        guard let first = pieces.first, let last = pieces.last else { return 0 }
        if time < first.time { return first.position - first.time.timeIntervalSince(time) }
        if !growing, time > last.endTime { return last.endPosition + time.timeIntervalSince(last.endTime) }
        return position(at: time)
    }

    /// Position of `time`: in a gap the next piece's start; outside the window clamps (a growing window runs on).
    public func position(at time: Date) -> Double {
        guard let first = pieces.first, let last = pieces.last else { return 0 }
        if time <= first.time { return first.position }
        for piece in pieces {
            if time < piece.time { return piece.position }  // in the gap before this piece
            if time < piece.endTime { return piece.position + time.timeIntervalSince(piece.time) }
        }
        return growing ? last.position + time.timeIntervalSince(last.time) : last.endPosition
    }
}

public enum TimelineRules {
    /// "Od začátku" starts this long before the programme (as the gateway's `EXT-X-START`).
    public static let startMargin: TimeInterval = 30
    /// A left/right click.
    public static let stepSeconds: Double = 10
    /// Held at least this long, a left/right press scrubs instead of stepping.
    public static let holdThreshold: Double = 0.5
    /// ▲ more than this far into a programme goes to its start, else to the previous one.
    public static let jumpBackThreshold: TimeInterval = 10
    /// A growing window's right end reads "Teď" while its last piece ends this close to now.
    public static let nowTolerance: TimeInterval = 30

    /// What a seek target does at the window's edges (golden `edge`).
    public enum Edge: String, Sendable, Equatable {
        case seek
        /// Stay inside: seek to the nearest end.
        case clamp
        /// Open the `previous` window at the target's wall clock.
        case previous
        /// Open the `next` window at the target's wall clock.
        case next
    }

    public static func edge(target: Double, duration: Double, complete: Bool, hasPrevious: Bool, hasNext: Bool) -> Edge {
        if target < 0 { return hasPrevious ? .previous : .clamp }
        if target >= duration {
            // A growing window's end is the live edge.
            return complete && hasNext ? .next : .clamp
        }
        return .seek
    }

    /// Seek offset (seconds, before the direction's sign) of a left/right press held `held` seconds (golden `hold`):
    /// a click steps 10 s, held it scrubs 60 s per second up to 3 s, then 600 s per second.
    public static func holdOffset(held: Double) -> Double {
        guard held >= holdThreshold else { return stepSeconds }
        if held <= 3 { return 60 * held }
        return 180 + 600 * (held - 3)
    }

    /// Where the window opens: programme start − 30 s, or "Pokračovat" at start + progress.
    public static func startTime(programmeStart: Date, resumeAt: TimeInterval?) -> Date {
        guard let resumeAt else { return programmeStart.addingTimeInterval(-startMargin) }
        return programmeStart.addingTimeInterval(resumeAt)
    }

    // MARK: labels

    /// The playhead's wall clock: "Dnes 21:14:30", "Včera 23:59:59", "čt 22. 10. 10:05:00" (golden `labels`).
    public static func label(_ time: Date, now: Date, calendar: Calendar = .prague) -> String {
        "\(dayLabel(time, now: now, calendar: calendar)) \(clock(time, seconds: true, calendar: calendar))"
    }

    /// The window's end labels "H:MM", with the day in front only when it is not the playhead's day; a growing
    /// window's right end is "Teď" while its last piece ends within 30 s of now (golden `edges`).
    public static func edgeLabels(start: Date, end: Date, playhead: Date, now: Date, growing: Bool = false,
                                  calendar: Calendar = .prague) -> (start: String, end: String) {
        let playheadDay = BroadcastDay(containing: playhead, calendar: calendar)
        func label(_ time: Date) -> String {
            let text = clock(time, seconds: false, calendar: calendar)
            guard BroadcastDay(containing: time, calendar: calendar) != playheadDay else { return text }
            return "\(dayLabel(time, now: now, calendar: calendar)) \(text)"
        }
        let endLabel = growing && now.timeIntervalSince(end) <= nowTolerance ? "Teď" : label(end)
        return (label(start), endLabel)
    }

    private static func dayLabel(_ time: Date, now: Date, calendar: Calendar) -> String {
        BroadcastDay(containing: time, calendar: calendar)
            .label(today: BroadcastDay(containing: now, calendar: calendar), calendar: calendar)
    }

    /// "9:05:09" / "9:05", hours without a leading zero, seconds rounded down.
    private static func clock(_ time: Date, seconds: Bool, calendar: Calendar) -> String {
        let parts = calendar.dateComponents([.hour, .minute, .second], from: time)
        let hour = parts.hour ?? 0, minute = parts.minute ?? 0, second = parts.second ?? 0
        return seconds ? String(format: "%d:%02d:%02d", hour, minute, second) : String(format: "%d:%02d", hour, minute)
    }

    // MARK: programmes

    /// Index of the programme on air at `time` (a boundary belongs to the later one), nil in a gap (golden `programmeAt`).
    public static func programmeAt(_ time: Date, in programmes: [DVBTimeline.Programme]) -> Int? {
        programmes.lastIndex { $0.start <= time && time < $0.end }
    }

    public enum Direction: String, Sendable { case up, down }

    /// Target of ▲ (start of this programme, or of the previous one within 10 s of its start) and ▼ (start of the
    /// next one); nil when there is nothing (golden `jump`). `programmes` sorted by start.
    public static func jump(from time: Date, _ direction: Direction, in programmes: [DVBTimeline.Programme]) -> Date? {
        switch direction {
        case .down:
            return programmes.first { $0.start > time }?.start
        case .up:
            var before = time
            if let index = programmeAt(time, in: programmes) {
                let current = programmes[index]
                if time.timeIntervalSince(current.start) > jumpBackThreshold { return current.start }
                before = current.start
            }
            return programmes.last { $0.start < before }?.start
        }
    }

    /// ▲ / ▼ from the playhead at `position`, as a seek target in the window (outside it for the edge rule, see
    /// `TimelineMap.unclampedPosition`); nil = nothing happens. ▲ skips a programme whose start maps within 10 s of
    /// the playhead (a start inside a recording gap maps to the next piece's start, where ▲ would otherwise stay).
    public static func jumpTarget(from position: Double, _ direction: Direction, map: TimelineMap,
                                  programmes: [DVBTimeline.Programme]) -> Double? {
        guard var time = map.time(at: position) else { return nil }
        while let target = jump(from: time, direction, in: programmes) {
            let mapped = map.unclampedPosition(at: target)
            guard direction == .up, abs(mapped - position) <= jumpBackThreshold else { return mapped }
            time = target  // from this programme's own start ▲ goes to the one before
        }
        return nil
    }

    /// "Pokračovat" progress of `programme` with the playhead at `time`; before its start 0 (golden `resume`).
    public static func resumeProgress(time: Date, programmeStart: Date) -> TimeInterval {
        max(0, time.timeIntervalSince(programmeStart))
    }

    /// Ad breaks of a programme (positions from its own playlist start = programme start − 30 s) as positions in
    /// the window: to wall clock, then through the map.
    public static func windowBreaks(_ breaks: [AdBreak], programmeStart: Date, map: TimelineMap) -> [AdBreak] {
        let origin = programmeStart.addingTimeInterval(-startMargin)
        return breaks.compactMap { item in
            let start = map.position(at: origin.addingTimeInterval(item.start))
            let end = map.position(at: origin.addingTimeInterval(item.end))
            return start < end ? AdBreak(start: start, end: end, confidence: item.confidence) : nil
        }
    }
}

/// Which Anténa recordings play in the timeline: the continuous recording's programme ids (`b<channel>-<start>-<end>`,
/// as the gateway validates them); TVHeadend DVR entries (uuids) keep the plain recording playlist.
public enum TimelineIDs {
    public static func isContinuous(_ recordingID: String) -> Bool {
        recordingID.wholeMatch(of: /b[A-Za-z0-9_]{1,40}-\d{9,11}-\d{9,11}/) != nil
    }
}

// MARK: - parsing

extension DVBParser {
    /// `GET /dvb/timeline/<id>`. Unknown fields are ignored; a window without valid pieces, a missing id or a
    /// playlist that is not a gateway path (starts with "/", not "//") throws: the bearer key goes with every request
    /// and must never go to another host.
    public static func timeline(status: Int, body: Data) throws -> DVBTimeline {
        guard (200..<300).contains(status) else { throw error(status: status, body: body) }
        guard let root = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] else {
            throw invalidTimeline("body is not a JSON object")
        }
        guard let id = nonEmpty(root["id"]) else { throw invalidTimeline("no id") }
        guard let playlist = nonEmpty(root["playlist"]), playlist.hasPrefix("/"), !playlist.hasPrefix("//") else {
            throw invalidTimeline("playlist is not a gateway path")
        }
        guard let programme = root["programme"] as? [String: Any],
              let start = CTParser.date(programme["start"]), let end = CTParser.date(programme["end"]), end > start else {
            throw invalidTimeline("no programme times")
        }
        guard let items = root["pieces"] as? [Any], !items.isEmpty else { throw invalidTimeline("no pieces") }
        var pieces: [DVBTimeline.Piece] = []
        for item in items {
            guard let item = item as? [String: Any], let position = number(item["position"]), position >= 0,
                  let time = CTParser.date(item["time"]), let duration = number(item["duration"]), duration > 0,
                  position >= (pieces.last?.endPosition ?? 0) - 0.001 else {
                throw invalidTimeline("bad piece")
            }
            pieces.append(DVBTimeline.Piece(position: position, time: time, duration: duration))
        }
        let programmes: [DVBTimeline.Programme] = (root["programmes"] as? [Any] ?? []).compactMap { item in
            guard let item = item as? [String: Any], let start = CTParser.date(item["start"]),
                  let end = CTParser.date(item["end"]), end > start else { return nil }
            return DVBTimeline.Programme(
                id: nonEmpty(item["id"]), title: item["title"] as? String ?? "", subtitle: item["subtitle"] as? String ?? "",
                start: start, end: end, breaks: item["breaks"] as? Bool ?? false
            )
        }
        return DVBTimeline(
            id: id, channel: root["channel"] as? String ?? "", playlist: playlist,
            complete: root["complete"] as? Bool ?? false, programmeStart: start, programmeEnd: end,
            pieces: pieces, programmes: programmes, previous: nonEmpty(root["previous"]), next: nonEmpty(root["next"])
        )
    }

    private static func invalidTimeline(_ reason: String) -> CTError {
        CTError("Brána Anténa neodpovídá", detail: "gateway timeline: \(reason)")
    }

    private static func nonEmpty(_ value: Any?) -> String? {
        guard let text = value as? String, !text.isEmpty else { return nil }
        return text
    }

    /// A finite JSON number (not a string, not a boolean).
    private static func number(_ value: Any?) -> Double? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        let double = number.doubleValue
        return double.isFinite ? double : nil
    }
}

extension DVBClient {
    /// The window around a `b…` programme (spec/dvb-gateway.md "Channel timeline").
    public func timeline(id: String) async throws -> DVBTimeline {
        let response = try await get("/dvb/timeline/\(escape(id))")
        return try DVBParser.timeline(status: response.status, body: response.body)
    }

    public func timelinePlaylistURL(_ timeline: DVBTimeline) -> URL { config.url(timeline.playlist) }
}
