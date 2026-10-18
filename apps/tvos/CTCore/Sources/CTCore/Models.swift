import Foundation

/// Where a channel's streams and programme come from. Only Ceska televize for now;
/// a DVB-T2 (TVHeadend) source may be added later.
public enum ChannelSource: String, Sendable, Hashable {
    case ceskaTelevize
}

public struct Channel: Sendable, Hashable, Identifiable {
    /// API id used by the live-stream endpoint, e.g. "CH_1".
    public let id: String
    /// Channel name in CT's programme (GraphQL) API, e.g. "ct1".
    public let slug: String
    public let name: String
    public let source: ChannelSource

    public init(id: String, slug: String, name: String, source: ChannelSource = .ceskaTelevize) {
        self.id = id
        self.slug = slug
        self.name = name
        self.source = source
    }
}

public enum Channels {
    /// CH_7 ("ČT 3") returned 404 on 2026-10-17 and is left out.
    public static let all: [Channel] = [
        Channel(id: "CH_1", slug: "ct1", name: "ČT1"),
        Channel(id: "CH_2", slug: "ct2", name: "ČT2"),
        Channel(id: "CH_24", slug: "ct24", name: "ČT24"),
        Channel(id: "CH_4", slug: "sport", name: "ČT sport"),
        Channel(id: "CH_5", slug: "decko", name: "ČT :D"),
        Channel(id: "CH_6", slug: "art", name: "ČT art"),
    ]

    public static func byID(_ id: String) -> Channel? { all.first { $0.id == id } }
    public static func bySlug(_ slug: String) -> Channel? { all.first { $0.slug == slug } }
}

/// Fresh signed HLS URLs of a live channel. Never persist these: they expire (~1 h)
/// and are bound to the requesting device's IP.
public struct LiveStreams: Sendable, Equatable {
    public let main: URL
    /// ~3 h rewind buffer ("Zpět v čase"), if offered.
    public let timeshift: URL?

    public init(main: URL, timeshift: URL?) {
        self.main = main
        self.timeshift = timeshift
    }
}

public enum VODResult: Sendable, Equatable {
    case stream(URL)
    /// The item is airing right now on this channel (API id); play start-over instead.
    case stillLive(channelID: String)
}

public struct Programme: Sendable, Hashable, Identifiable {
    /// GraphQL channel slug ("ct1").
    public let channel: String
    public let idec: String
    public let title: String
    public let start: Date
    public let end: Date
    /// Available in the archive right now.
    public let isPlayableNow: Bool
    public let liveOnly: Bool
    public let episodeTitle: String
    public let description: String
    public let genre: String
    public let imageURL: URL?

    /// The same IDEC can air on two channels (ČT1 and ČT24 simulcasts), so the id includes both.
    public var id: String { "\(channel)|\(idec)|\(Int(start.timeIntervalSince1970))" }

    public init(
        channel: String, idec: String, title: String, start: Date, end: Date,
        isPlayableNow: Bool, liveOnly: Bool = false, episodeTitle: String = "",
        description: String = "", genre: String = "", imageURL: URL? = nil
    ) {
        self.channel = channel
        self.idec = idec
        self.title = title
        self.start = start
        self.end = end
        self.isPlayableNow = isPlayableNow
        self.liveOnly = liveOnly
        self.episodeTitle = episodeTitle
        self.description = description
        self.genre = genre
        self.imageURL = imageURL
    }

    public func isAiring(at now: Date) -> Bool { start <= now && now < end }
}

/// The API did not give us a playable stream or programme.
public struct CTError: Error, Sendable, Equatable, LocalizedError {
    /// Short Czech text for viewers (CT's own wording when the API provides one).
    public let userMessage: String
    /// CT's status name (e.g. "MISSING_LICENCE_NOW"), when known.
    public let code: String?
    /// Technical detail for logs.
    public let detail: String

    public init(_ userMessage: String, code: String? = nil, detail: String = "") {
        self.userMessage = userMessage
        self.code = code
        self.detail = detail
    }

    public var errorDescription: String? { userMessage }

    public static let genericStream = "Stream se nepodařilo načíst"
    public static let videoUnavailable = "Video není k dispozici"
    public static let scheduleFailed = "Program se nepodařilo načíst"
    public static let blocked = "Vysílání je blokováno"
    public static let noTimeshift = "Zpět v čase není pro tento kanál k dispozici"
    public static let notStarted = "Pořad ještě nezačal"
    public static let drmHint = "Pořad můžete pustit v oficiální aplikaci iVysílání."
    /// CT status of archive items that are offered only with DRM. We never request DRM.
    public static let unsupportedDRM = "UNSUPPORTED_DRM"
}
