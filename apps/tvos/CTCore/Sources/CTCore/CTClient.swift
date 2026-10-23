import Foundation

public struct HTTPResponse: Sendable {
    public let status: Int
    public let body: Data

    public init(status: Int, body: Data) {
        self.status = status
        self.body = body
    }
}

public protocol HTTPTransport: Sendable {
    func send(_ request: URLRequest) async throws -> HTTPResponse
}

public struct URLSessionTransport: HTTPTransport {
    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    /// No URLCache, no cookies, nothing on disk: for derived images that must stay in memory
    /// (Anténa thumbnails, spec/dvb-gateway.md).
    public static func memoryOnly() -> URLSessionTransport {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSessionTransport(session: URLSession(configuration: configuration))
    }

    public func send(_ request: URLRequest) async throws -> HTTPResponse {
        let (data, response) = try await session.data(for: request)
        return HTTPResponse(status: (response as? HTTPURLResponse)?.statusCode ?? 0, body: data)
    }
}

/// Client for Ceska televize's public stream and programme APIs: the same endpoints the
/// iVysílání web player uses. We never touch video data; the player fetches the signed
/// URL straight from CT's CDN.
public struct CTClient: Sendable {
    public static let apiBase = "https://api.ceskatelevize.cz"
    public static let userAgent = "Mozilla/5.0 (compatible; cz-tv-hub/0.1)"
    static let streamQuery = "canPlayDrm=false&quality=web&streamType=hls&origin=ivysilaniweb"
        + "&usePlayability=true&clientVersion=0.0.0"
    static let graphQLURL = apiBase + "/graphql/?client=iVysilaniWeb&version=1.131.1&use-new-playability=true"
    static let scheduleQuery = """
        query($channels: [String!]!, $date: Date!) {
          TVProgramDailyChannelsPlanV2(channels: $channels, date: $date) {
            channel
            program { idec title episodeTitle description genre imageUrl start end isPlayableNow liveOnly }
          }
        }
        """

    private let transport: HTTPTransport
    private let timeout: TimeInterval

    public init(transport: HTTPTransport = URLSessionTransport(), timeout: TimeInterval = 10) {
        self.transport = transport
        self.timeout = timeout
    }

    public func liveStreams(channelID: String) async throws -> LiveStreams {
        let url = "\(Self.apiBase)/video/v1/playlist-live/v1/stream-data/channel/\(escape(channelID))?\(Self.streamQuery)"
        let response = try await send(request(url), failure: CTError.genericStream)
        return try CTParser.liveStreams(status: response.status, body: response.body, channelID: channelID)
    }

    public func vod(idec: String) async throws -> VODResult {
        let url = "\(Self.apiBase)/video/v1/playlist-vod/v1/stream-data/media/external/\(escape(idec))?\(Self.streamQuery)"
        let response = try await send(request(url), failure: CTError.genericStream)
        return try CTParser.vod(status: response.status, body: response.body, idec: idec)
    }

    /// Programme of one broadcast day for the given channel slugs, keyed by slug.
    public func schedules(slugs: [String], day: BroadcastDay) async throws -> [String: [Programme]] {
        var request = request(Self.graphQLURL)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "query": Self.scheduleQuery,
            "variables": ["channels": slugs, "date": day.graphQLDate],
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let response = try await send(request, failure: CTError.scheduleFailed)
        return try CTParser.schedules(status: response.status, body: response.body)
    }

    private func request(_ string: String) -> URLRequest {
        var request = URLRequest(url: URL(string: string)!, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: timeout)
        request.setValue(Self.userAgent, forHTTPHeaderField: "User-Agent")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        return request
    }

    private func send(_ request: URLRequest, failure: String) async throws -> HTTPResponse {
        do {
            return try await transport.send(request)
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            let endpoint = request.url?.path ?? ""
            throw CTError(failure, detail: "request to \(endpoint) failed: \(error.localizedDescription)")
        }
    }

    private func escape(_ component: String) -> String {
        component.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed.subtracting(["/"])) ?? component
    }
}
