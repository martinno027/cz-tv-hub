import Foundation
import Observation

/// One public live stream of the Chamber of Deputies (spec/apps.md "Poslanecká sněmovna"). Live only: played
/// as published, never recorded, cached, rewound or resumed.
public struct PSPStream: Sendable, Hashable, Identifiable {
    /// `stream1` … `stream5`.
    public let id: String
    public let name: String
    public let url: URL

    public init(id: String, name: String, url: URL) {
        self.id = id
        self.name = name
        self.url = url
    }

    /// Key in "Kanály" (`psp:stream1`).
    public var key: String { "psp:\(id)" }
}

/// The catalogue and the "is it on now?" rules (golden cases `spec/fixtures/psp_cases.json`).
public enum PSPStreams {
    /// Exactly the streams the Chamber publishes on psp.cz ("Živé vysílání"); no other URLs, no mirrors.
    public static let all: [PSPStream] = [
        stream(1, "Schůze PS"),
        stream(2, "Tiskové konference"),
        stream(3, "Výbory a semináře 1"),
        stream(4, "Výbory a semináře 2"),
        stream(5, "Výbory a semináře 3"),
    ]

    private static func stream(_ number: Int, _ name: String) -> PSPStream {
        PSPStream(id: "stream\(number)", name: name,
                  url: URL(string: "https://pspcr-ott-live.ssl.cdn.cra.cz/channels/ps-stream\(number)/playlist/cze.m3u8")!)
    }

    public static func byKey(_ key: String) -> PSPStream? { all.first { $0.key == key } }

    /// Each stream is checked at most this often while Živě or the guide is on screen.
    public static let checkInterval: TimeInterval = 60
    /// After a failed check (network error, 5xx).
    public static let errorInterval: TimeInterval = 120
    /// A live state older than this without a confirming check is no longer shown as live.
    public static let staleLive: TimeInterval = 180

    /// Selecting a stream that is not on, or a 404 when opening it.
    public static let offAirMessage = "Poslanecká sněmovna teď nevysílá."
    /// `CTError.code` of "not on air" (the check right before playback).
    public static let offAirCode = "PSP_OFF_AIR"
    /// Guide cell on any day but today, and what selecting it says.
    public static let onlyLiveText = "Jen přímé přenosy"
    public static let onlyLiveMessage = "Poslanecká sněmovna: jen přímé přenosy, bez záznamů."
    /// Source label (settings, guide detail).
    public static let sourceLabel = "Poslanecká sněmovna PČR"
    public static let liveText = "Přímý přenos"
    public static let offText = "Nevysílá se"
    public static let unknownText = "Zjišťuji…"

    /// On = HTTP 200 and an HLS playlist with at least one media segment or variant. Anything else is not on.
    public static func isLive(status: Int, body: String) -> Bool {
        guard status == 200 else { return false }
        var lines = body.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }
        if let first = lines.first, first.hasPrefix("\u{FEFF}") { lines[0] = String(first.dropFirst()) }
        guard lines.first == "#EXTM3U" else { return false }
        return lines.contains { $0.hasPrefix("#EXTINF") || $0.hasPrefix("#EXT-X-STREAM-INF") }
    }
}

/// What Živě and the guide show for a stream.
public enum PSPLiveState: Sendable, Equatable {
    /// Not checked yet, or "live" not confirmed for `staleLive` ("Zjišťuji…"); selecting it tries to play.
    case unknown
    case live
    case off
}

/// The polling rules without clocks or networking: when each stream is due and what it shows.
public struct PSPLiveTracker: Sendable, Equatable {
    public enum Outcome: Sendable, Equatable {
        /// A playlist with segments or variants.
        case live
        /// A normal answer that it is not on (the 404 "not processed now" outside broadcasts, any other 4xx,
        /// 200 with an empty playlist / no playlist): next check after `checkInterval`.
        case off
        /// 5xx: not on, checked again only after `errorInterval`.
        case serverError
        /// No answer: the last known state stays (live at most `staleLive` after its last confirmation).
        case networkError
    }

    private struct Entry: Sendable, Equatable {
        var lastCheck: Date
        var failed: Bool
        var known: Bool?
        var confirmedLive: Date?
    }

    private var entries: [String: Entry] = [:]

    public init() {}

    public mutating func record(_ key: String, _ outcome: Outcome, at now: Date) {
        var entry = entries[key] ?? Entry(lastCheck: now, failed: false)
        entry.lastCheck = now
        switch outcome {
        case .live:
            entry.failed = false
            entry.known = true
            entry.confirmedLive = now
        case .off:
            entry.failed = false
            entry.known = false
            entry.confirmedLive = nil
        case .serverError:
            entry.failed = true
            entry.known = false
            entry.confirmedLive = nil
        case .networkError:
            entry.failed = true
        }
        entries[key] = entry
    }

    public func state(of key: String, now: Date) -> PSPLiveState {
        guard let entry = entries[key], let known = entry.known else { return .unknown }
        guard known, let confirmed = entry.confirmedLive else { return .off }
        return now.timeIntervalSince(confirmed) < PSPStreams.staleLive ? .live : .unknown
    }

    /// When `key` was last checked (any outcome).
    public func lastCheck(_ key: String) -> Date? { entries[key]?.lastCheck }

    /// The outcome class of one answer (`isLive` decides on/off; the status decides the next interval).
    public static func outcome(status: Int, body: String) -> Outcome {
        if PSPStreams.isLive(status: status, body: body) { return .live }
        return status >= 500 ? .serverError : .off
    }

    /// When `key` may be checked next (now for a stream never checked).
    public func due(_ key: String, now: Date) -> Date {
        guard let entry = entries[key] else { return now }
        return entry.lastCheck.addingTimeInterval(entry.failed ? PSPStreams.errorInterval : PSPStreams.checkInterval)
    }

    /// The stream to check next among `keys` (the earliest due, the list's order on a tie) and when.
    public func next(among keys: [String], now: Date) -> (key: String, at: Date)? {
        var best: (key: String, at: Date)?
        for key in keys {
            let at = max(due(key, now: now), now)
            if best == nil || at < best!.at { best = (key, at) }
        }
        return best
    }

    /// When a stream shown as live turns stale (the state changes without a check), if any.
    public func nextExpiry(among keys: [String], now: Date) -> Date? {
        keys.compactMap { key -> Date? in
            guard state(of: key, now: now) == .live, let confirmed = entries[key]?.confirmedLive else { return nil }
            return confirmed.addingTimeInterval(PSPStreams.staleLive)
        }.min()
    }
}

/// Checks the streams while a screen that shows them watches (`watch`): one request at a time, each stream at
/// most once per `checkInterval` (`errorInterval` after an error); only the playlist itself is read, never a
/// segment, with no extra headers and no cache. Nothing runs when nobody watches.
@MainActor
@Observable
public final class PSPLiveMonitor {
    /// Current state per stream key; missing = unknown.
    public private(set) var states: [String: PSPLiveState] = [:]

    @ObservationIgnored private var tracker = PSPLiveTracker()
    @ObservationIgnored private let transport: HTTPTransport
    @ObservationIgnored private let clock: @Sendable () -> Date
    @ObservationIgnored private var activeWatch: UUID?

    public init(transport: HTTPTransport = URLSessionTransport.memoryOnly(), clock: @escaping @Sendable () -> Date = Date.init) {
        self.transport = transport
        self.clock = clock
    }

    public func state(of key: String) -> PSPLiveState { states[key] ?? .unknown }

    /// A 404 when opening a stream: its broadcast just ended.
    public func markOff(_ key: String) {
        tracker.record(key, .off, at: clock())
        publish()
    }

    /// Checks `streams` until the calling task is cancelled. A newer `watch` takes over from an older one, so
    /// two screens never poll at the same time.
    public func watch(_ streams: [PSPStream]) async {
        let id = UUID()
        activeWatch = id
        defer { if activeWatch == id { activeWatch = nil } }
        let keys = streams.map(\.key)
        publish()
        while !Task.isCancelled, activeWatch == id {
            let now = clock()
            guard let next = tracker.next(among: keys, now: now) else { return }
            if next.at <= now {
                guard let stream = streams.first(where: { $0.key == next.key }) else { return }
                await check(stream)
                continue
            }
            let wake = min(next.at, tracker.nextExpiry(among: keys, now: now) ?? next.at)
            do {
                try await Task.sleep(for: .seconds(max(0.5, wake.timeIntervalSince(now))))
            } catch {
                return
            }
            publish()
        }
    }

    /// Right before playback: the state from a check younger than `checkInterval`, else after one more check
    /// of the same playlist (one GET per viewer action).
    public func stateBeforePlaying(_ stream: PSPStream) async -> PSPLiveState {
        if let last = tracker.lastCheck(stream.key), clock().timeIntervalSince(last) < PSPStreams.checkInterval {
            return tracker.state(of: stream.key, now: clock())
        }
        await check(stream)
        return tracker.state(of: stream.key, now: clock())
    }

    /// One check of one stream (the playlist only).
    func check(_ stream: PSPStream) async {
        var request = URLRequest(url: stream.url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 10)
        request.httpMethod = "GET"
        let outcome: PSPLiveTracker.Outcome
        do {
            let response = try await transport.send(request)
            outcome = PSPLiveTracker.outcome(status: response.status, body: String(decoding: response.body, as: UTF8.self))
        } catch is CancellationError {
            return
        } catch {
            if Task.isCancelled { return }
            outcome = .networkError
        }
        tracker.record(stream.key, outcome, at: clock())
        publish()
    }

    private func publish() {
        let now = clock()
        var next: [String: PSPLiveState] = [:]
        for stream in PSPStreams.all {
            let state = tracker.state(of: stream.key, now: now)
            if state != .unknown { next[stream.key] = state }
        }
        if next != states { states = next }
    }
}
