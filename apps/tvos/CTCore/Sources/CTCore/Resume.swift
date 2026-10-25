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

/// What a "Pokračovat" entry shows in the Archiv tab's "Pokračovat ve sledování" row (spec/apps.md "Archiv"), kept
/// on the device only. `start` and `channel` let the row play the entry at once without the guide.
public struct ResumeCard: Sendable, Equatable, Codable {
    public enum Source: String, Sendable, Codable {
        case ct, dvb
    }

    /// The programme or episode.
    public var title: String
    /// The show the episode belongs to; empty for a programme on its own.
    public var showTitle: String
    /// The card image as the card had it: a ČT image URL or a gateway path.
    public var image: String?
    public var source: Source
    /// The programme's start (the decision rules and the timeline player need it to play the entry).
    public var start: Date?
    /// ČT: the channel id ("CH_1"); Anténa: the channel name for the player's second line.
    public var channel: String?

    public init(title: String, showTitle: String = "", image: String? = nil, source: Source, start: Date? = nil,
                channel: String? = nil) {
        self.title = title
        self.showTitle = showTitle
        self.image = image
        self.source = source
        self.start = start
        self.channel = channel
    }

    /// A guide programme: its episode as the title and the programme as the show, else the programme alone.
    public static func programme(title: String, episode: String, image: String?, source: Source, start: Date,
                                 channel: String?) -> ResumeCard {
        let episode = episode.trimmingCharacters(in: .whitespacesAndNewlines)
        return episode.isEmpty
            ? ResumeCard(title: title, image: image, source: source, start: start, channel: channel)
            : ResumeCard(title: episode, showTitle: title, image: image, source: source, start: start, channel: channel)
    }
}

/// What the player saves progress for: the store key, the programme's start and its length.
public struct ResumeTarget: Sendable, Equatable {
    public let key: String
    public let programmeStart: Date
    public let length: TimeInterval
    /// What the Archiv row shows for it; nil keeps what an earlier save stored.
    public let card: ResumeCard?

    public init(key: String, programmeStart: Date, length: TimeInterval, card: ResumeCard? = nil) {
        self.key = key
        self.programmeStart = programmeStart
        self.length = length
        self.card = card
    }

    public static func ct(_ programme: Programme, card: ResumeCard? = nil) -> ResumeTarget {
        let channel = Channels.bySlug(programme.channel)
        return ResumeTarget(key: ResumeRules.ctKey(idec: programme.idec), programmeStart: programme.start,
                            length: programme.end.timeIntervalSince(programme.start),
                            card: card ?? .programme(title: programme.title, episode: programme.episodeTitle,
                                                     image: programme.imageURL?.absoluteString, source: .ct,
                                                     start: programme.start, channel: channel?.id))
    }

    public static func antenna(recordingID: String, event: DVBEvent, channelName: String? = nil,
                               card: ResumeCard? = nil) -> ResumeTarget {
        ResumeTarget(key: ResumeRules.dvbKey(recordingID: recordingID), programmeStart: event.start,
                     length: event.end.timeIntervalSince(event.start),
                     card: card ?? .programme(title: event.title, episode: event.subtitle,
                                              image: ArchiveImage.thumbnail(recordingID: recordingID).stored,
                                              source: .dvb, start: event.start, channel: channelName))
    }
}

public struct ResumeEntry: Sendable, Equatable, Codable {
    public let progressSeconds: TimeInterval
    public let lengthSeconds: TimeInterval
    public let savedAt: Date
    /// T13 on: what the Archiv row shows (`title`, `showTitle`, `image`, `source`); missing in entries saved before.
    public let card: ResumeCard?

    public init(progressSeconds: TimeInterval, lengthSeconds: TimeInterval, savedAt: Date, card: ResumeCard? = nil) {
        self.progressSeconds = progressSeconds
        self.lengthSeconds = lengthSeconds
        self.savedAt = savedAt
        self.card = card
    }

    /// The row's title; nil (left out of the row) for entries from before T13.
    public var title: String? { card?.title }
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
            all[target.key] = ResumeEntry(progressSeconds: seconds, lengthSeconds: target.length, savedAt: clock(),
                                          card: target.card ?? all[target.key]?.card)
        }
        write(all)
    }

    public func remove(_ key: String) {
        var all = entries()
        guard all.removeValue(forKey: key) != nil else { return }
        write(all)
    }

    public var count: Int { entries().count }

    /// "Pokračovat ve sledování" (spec/apps.md "Archiv"): entries with a title, newest first, at most 20.
    public func continueRow() -> [(key: String, entry: ResumeEntry)] { ArchiveRules.continueRow(entries()) }

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
