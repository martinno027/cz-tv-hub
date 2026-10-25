import Foundation

public enum PlaybackRequest: Sendable, Hashable {
    /// The channel's live edge.
    case live(Channel)
    /// A programme picked in the guide; the decision rules choose start-over or archive.
    case programme(Programme, channel: Channel)
    /// An iVysílání episode from the Archiv tab (spec/apps.md "Archiv"): the same decision rules, but the channel may
    /// be unknown (web-only items, ČT3), then it can only play from the archive (VOD by idec).
    case episode(Programme, channel: Channel?)
}

/// A freshly resolved stream, ready for the player. Hold it only while playing.
public struct PlaybackSource: Sendable, Equatable {
    public enum Mode: Sendable, Equatable {
        case live
        /// Timeshift stream: seek to wall-clock `seekDate`, or else `offset` seconds after the
        /// oldest point of the buffer.
        case startOver(seekDate: Date, offset: TimeInterval)
        case vod
        /// A gateway recording: always start at its first segment, also while it still grows
        /// (an EVENT playlist would otherwise open at the live edge).
        case recording
    }

    public let url: URL
    public let title: String
    public let mode: Mode
    /// HTTP headers the player must send on every request (playlist and segments).
    public let headers: [String: String]
    /// "Pokračovat" for `.vod` and `.recording`: start this many seconds into the stream
    /// (nil = from the beginning). Start-over carries its resume point in `mode`.
    public let startPosition: TimeInterval?

    public init(url: URL, title: String, mode: Mode, headers: [String: String] = [:], startPosition: TimeInterval? = nil) {
        self.url = url
        self.title = title
        self.mode = mode
        self.headers = headers
        self.startPosition = startPosition
    }

    /// A gateway ("Anténa") recording, optionally resumed at `resumeAt` seconds.
    public static func antennaRecording(url: URL, title: String, headers: [String: String],
                                        resumeAt: TimeInterval? = nil) -> PlaybackSource {
        PlaybackSource(url: url, title: title, mode: .recording, headers: headers, startPosition: resumeAt)
    }
}

/// Resolves what to play right before playback, always on the device (URLs are IP-bound).
public struct PlaybackPlanner: Sendable {
    private let client: CTClient
    private let clock: @Sendable () -> Date

    public init(client: CTClient, clock: @escaping @Sendable () -> Date = Date.init) {
        self.client = client
        self.clock = clock
    }

    /// `resumeAt` ("Pokračovat") is seconds from the programme's start; ignored for live. The
    /// decision rules still pick the source by time; only the start position changes.
    public func resolve(_ request: PlaybackRequest, resumeAt: TimeInterval? = nil) async throws -> PlaybackSource {
        switch request {
        case .live(let channel):
            let streams = try await client.liveStreams(channelID: channel.id)
            return PlaybackSource(url: streams.main, title: channel.name, mode: .live)
        case .programme(let programme, let channel):
            switch DecisionRules.action(for: programme, now: clock()) {
            case .future:
                throw CTError(CTError.notStarted)
            case .startOver:
                return try await startOver(programme, channel: channel, resumeAt: resumeAt)
            case .vod, .vodUnavailable:
                return try await vod(programme, channel: channel, resumeAt: resumeAt)
            }
        case .episode(let programme, let channel):
            switch DecisionRules.action(for: programme, now: clock()) {
            case .future:
                throw CTError(CTError.notStarted)
            case .startOver:
                // Without a known channel there is no buffer to play from: the archive, or CT's reason.
                guard let channel else { return try await vod(programme, channel: nil, resumeAt: resumeAt) }
                return try await startOver(programme, channel: channel, resumeAt: resumeAt)
            case .vod, .vodUnavailable:
                return try await vod(programme, channel: channel, resumeAt: resumeAt)
            }
        }
    }

    /// The archive item by IDEC; for unavailable items this surfaces CT's own reason as a CTError.
    private func vod(_ programme: Programme, channel: Channel?, resumeAt: TimeInterval?) async throws -> PlaybackSource {
        switch try await client.vod(idec: programme.idec) {
        case .stream(let url):
            return PlaybackSource(url: url, title: programme.title, mode: .vod, startPosition: resumeAt)
        case .stillLive(let channelID):
            guard let live = Channels.byID(channelID) ?? channel else { throw CTError(CTError.noTimeshift) }
            return try await startOver(programme, channel: live, resumeAt: resumeAt)
        }
    }

    private func startOver(_ programme: Programme, channel: Channel, resumeAt: TimeInterval?) async throws -> PlaybackSource {
        let streams = try await client.liveStreams(channelID: channel.id)
        guard let timeshift = streams.timeshift else { throw CTError(CTError.noTimeshift) }
        return PlaybackSource(url: timeshift, title: programme.title,
                              mode: Self.startOverMode(start: programme.start, now: clock(), resumeAt: resumeAt))
    }

    /// Start-over position: the programme's beginning (minus the margin), or `start + resumeAt`.
    public static func startOverMode(start: Date, now: Date, resumeAt: TimeInterval?) -> PlaybackSource.Mode {
        if let resumeAt {
            return .startOver(seekDate: DecisionRules.resumeDate(start: start, progress: resumeAt),
                              offset: DecisionRules.resumeOffset(start: start, progress: resumeAt, now: now))
        }
        return .startOver(seekDate: DecisionRules.startOverDate(start: start),
                          offset: DecisionRules.startOverOffset(start: start, now: now))
    }
}
