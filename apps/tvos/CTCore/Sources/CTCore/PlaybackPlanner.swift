import Foundation

public enum PlaybackRequest: Sendable, Hashable {
    /// The channel's live edge.
    case live(Channel)
    /// A programme picked in the guide; the decision rules choose start-over or archive.
    case programme(Programme, channel: Channel)
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

    public init(url: URL, title: String, mode: Mode, headers: [String: String] = [:]) {
        self.url = url
        self.title = title
        self.mode = mode
        self.headers = headers
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

    public func resolve(_ request: PlaybackRequest) async throws -> PlaybackSource {
        switch request {
        case .live(let channel):
            let streams = try await client.liveStreams(channelID: channel.id)
            return PlaybackSource(url: streams.main, title: channel.name, mode: .live)
        case .programme(let programme, let channel):
            switch DecisionRules.action(for: programme, now: clock()) {
            case .future:
                throw CTError(CTError.notStarted)
            case .startOver:
                return try await startOver(programme, channel: channel)
            case .vod, .vodUnavailable:
                // For unavailable items this surfaces CT's own reason as a CTError.
                switch try await client.vod(idec: programme.idec) {
                case .stream(let url):
                    return PlaybackSource(url: url, title: programme.title, mode: .vod)
                case .stillLive(let channelID):
                    return try await startOver(programme, channel: Channels.byID(channelID) ?? channel)
                }
            }
        }
    }

    private func startOver(_ programme: Programme, channel: Channel) async throws -> PlaybackSource {
        let streams = try await client.liveStreams(channelID: channel.id)
        guard let timeshift = streams.timeshift else { throw CTError(CTError.noTimeshift) }
        let offset = DecisionRules.startOverOffset(start: programme.start, now: clock())
        return PlaybackSource(
            url: timeshift,
            title: programme.title,
            mode: .startOver(seekDate: DecisionRules.startOverDate(start: programme.start), offset: offset)
        )
    }
}
