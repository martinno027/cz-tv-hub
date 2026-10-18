import Foundation

// "Pokračovat" (spec/apps.md): each TV remembers, on the device only, how far it got in a
// programme. Only positions are stored, never stream URLs or video.

/// What a saved progress means for a programme of a given length (golden cases in resume_cases.json).
public enum ResumeDecision: Sendable, Equatable {
    /// Nothing to offer or store (too early).
    case none
    /// Offer "Pokračovat od <label>".
    case offer(seconds: TimeInterval)
    /// Watched to (almost) the end: remove the entry.
    case clear
}

public enum ResumeRules {
    /// Below this nothing is stored.
    public static let minSeconds: TimeInterval = 60
    /// Within this of the end the programme counts as watched.
    public static let endMarginSeconds: TimeInterval = 120
    public static let maxEntries = 200
    public static let maxAgeDays = 30
    public static var maxAge: TimeInterval { TimeInterval(maxAgeDays) * 86_400 }
    /// How often the player saves progress while playing.
    public static let saveInterval: TimeInterval = 10

    public static func decide(progress: TimeInterval, length: TimeInterval) -> ResumeDecision {
        guard progress.isFinite, length.isFinite, length > 0 else { return .none }
        if progress >= length - endMarginSeconds { return .clear }
        if progress >= minSeconds { return .offer(seconds: progress) }
        return .none
    }

    /// "23:10" below an hour, else "1:02:05" (whole seconds, rounded down).
    public static func label(_ seconds: TimeInterval) -> String {
        let total = max(0, Int(seconds.rounded(.down)))
        let hours = total / 3600, minutes = total % 3600 / 60, secs = total % 60
        if hours > 0 { return String(format: "%d:%02d:%02d", hours, minutes, secs) }
        return String(format: "%d:%02d", minutes, secs)
    }

    /// Button title of the offer.
    public static func buttonTitle(_ seconds: TimeInterval) -> String { "Pokračovat od \(label(seconds))" }

    public static func ctKey(idec: String) -> String { "ct:\(idec)" }
    public static func dvbKey(recordingID: String) -> String { "dvb:\(recordingID)" }

    /// Seconds from the programme's start that the player is showing; nil for live or when unknown.
    /// - Timeshift: wall clock of the shown frame − programme start; without a frame date it is
    ///   estimated from the distance to the live edge (`liveEdgePosition` ≈ `now`).
    /// - Archive and recordings: the player position.
    public static func progress(mode: PlaybackSource.Mode, position: Double?, frameDate: Date?,
                                liveEdgePosition: Double?, programmeStart: Date, now: Date) -> TimeInterval? {
        switch mode {
        case .live:
            return nil
        case .vod, .recording:
            guard let position, position.isFinite, position >= 0 else { return nil }
            return position
        case .startOver:
            if let frameDate { return frameDate.timeIntervalSince(programmeStart) }
            guard let position, let liveEdgePosition, position.isFinite, liveEdgePosition.isFinite else { return nil }
            let shown = now.addingTimeInterval(-(max(0, liveEdgePosition - position)))
            return shown.timeIntervalSince(programmeStart)
        }
    }
}

/// What the player saves progress for: the store key, the programme's start and its length.
public struct ResumeTarget: Sendable, Equatable {
    public let key: String
    public let programmeStart: Date
    public let length: TimeInterval

    public init(key: String, programmeStart: Date, length: TimeInterval) {
        self.key = key
        self.programmeStart = programmeStart
        self.length = length
    }

    public static func ct(_ programme: Programme) -> ResumeTarget {
        ResumeTarget(key: ResumeRules.ctKey(idec: programme.idec), programmeStart: programme.start,
                     length: programme.end.timeIntervalSince(programme.start))
    }

    public static func antenna(recordingID: String, event: DVBEvent) -> ResumeTarget {
        ResumeTarget(key: ResumeRules.dvbKey(recordingID: recordingID), programmeStart: event.start,
                     length: event.end.timeIntervalSince(event.start))
    }
}

public struct ResumeEntry: Sendable, Equatable, Codable {
    public let progressSeconds: TimeInterval
    public let lengthSeconds: TimeInterval
    public let savedAt: Date

    public init(progressSeconds: TimeInterval, lengthSeconds: TimeInterval, savedAt: Date) {
        self.progressSeconds = progressSeconds
        self.lengthSeconds = lengthSeconds
        self.savedAt = savedAt
    }
}

/// Key-value storage for the store; `UserDefaults` in the app, an in-memory one in tests.
public protocol ResumeBackend: AnyObject {
    func data(forKey key: String) -> Data?
    func set(_ value: Any?, forKey key: String)
}

extension UserDefaults: ResumeBackend {}

/// Saved positions keyed `ct:<idec>` / `dvb:<recording id>`, kept as one JSON object under
/// `storageKey`. At most `ResumeRules.maxEntries` entries, none older than `ResumeRules.maxAge`.
@MainActor
public final class ResumeStore {
    public static let storageKey = "resume.v1"

    private let backend: ResumeBackend
    private let clock: () -> Date
    private var cache: [String: ResumeEntry]?

    public init(backend: ResumeBackend = UserDefaults.standard, clock: @escaping () -> Date = Date.init) {
        self.backend = backend
        self.clock = clock
    }

    /// Seconds to offer "Pokračovat" at, or nil. `length` is the programme's current length
    /// (the stored one is used when not given).
    public func offer(for key: String, length: TimeInterval? = nil) -> TimeInterval? {
        guard let entry = entries()[key] else { return nil }
        if case .offer(let seconds) = ResumeRules.decide(progress: entry.progressSeconds, length: length ?? entry.lengthSeconds) {
            return seconds
        }
        return nil
    }

    public func entry(for key: String) -> ResumeEntry? { entries()[key] }

    /// Applies the rules: stores an offerable position, removes a watched programme, ignores the rest.
    public func save(_ target: ResumeTarget, progress: TimeInterval) {
        var all = entries()
        switch ResumeRules.decide(progress: progress, length: target.length) {
        case .none:
            return
        case .clear:
            guard all.removeValue(forKey: target.key) != nil else { return }
        case .offer(let seconds):
            all[target.key] = ResumeEntry(progressSeconds: seconds, lengthSeconds: target.length, savedAt: clock())
        }
        write(all)
    }

    public func remove(_ key: String) {
        var all = entries()
        guard all.removeValue(forKey: key) != nil else { return }
        write(all)
    }

    public var count: Int { entries().count }

    // MARK: storage

    private func entries() -> [String: ResumeEntry] {
        if let cache { return cache }
        let decoded = backend.data(forKey: Self.storageKey)
            .flatMap { try? JSONDecoder().decode([String: ResumeEntry].self, from: $0) } ?? [:]
        let pruned = Self.pruned(decoded, now: clock())
        cache = pruned
        if pruned.count != decoded.count { write(pruned) }
        return pruned
    }

    private func write(_ all: [String: ResumeEntry]) {
        let pruned = Self.pruned(all, now: clock())
        cache = pruned
        backend.set(try? JSONEncoder().encode(pruned), forKey: Self.storageKey)
    }

    /// Drops entries older than the maximum age, then keeps the newest `maxEntries`.
    static func pruned(_ all: [String: ResumeEntry], now: Date) -> [String: ResumeEntry] {
        let fresh = all.filter { now.timeIntervalSince($0.value.savedAt) <= ResumeRules.maxAge }
        guard fresh.count > ResumeRules.maxEntries else { return fresh }
        let newest = fresh.sorted { ($0.value.savedAt, $0.key) > ($1.value.savedAt, $1.key) }.prefix(ResumeRules.maxEntries)
        return Dictionary(uniqueKeysWithValues: newest.map { ($0.key, $0.value) })
    }
}
