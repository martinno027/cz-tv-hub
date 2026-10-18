import Foundation

// "Anténa": free-to-air DVB-T2 channels from the household's own tuner, served by the
// cz-tv-hub gateway on the home LAN / tailnet (spec/dvb-gateway.md). The gateway only remuxes into
// temporary HLS; nothing is downloaded or kept on the device.

/// Where the gateway is and the bearer key it expects on every request (playlists and segments too).
public struct GatewayConfig: Sendable, Equatable, Codable {
    public static let defaultPort = 8098

    public let baseURL: URL
    public let key: String

    public init(baseURL: URL, key: String) {
        self.baseURL = baseURL
        self.key = key
    }

    /// Accepts what people type: "pi.tailnet.ts.net", "192.168.1.5:8098", "http://pi:8098/".
    /// Adds `http://` and the default port when missing. Returns nil for unusable input.
    public init?(address: String, key: String) {
        let trimmedKey = key.trimmingCharacters(in: .whitespacesAndNewlines)
        var text = address.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !trimmedKey.isEmpty else { return nil }
        if !text.contains("://") { text = "http://" + text }
        guard var parts = URLComponents(string: text), let host = parts.host, !host.isEmpty,
              parts.scheme == "http" || parts.scheme == "https" else { return nil }
        if parts.port == nil { parts.port = Self.defaultPort }
        parts.path = ""
        parts.query = nil
        parts.fragment = nil
        guard let url = parts.url else { return nil }
        self.init(baseURL: url, key: trimmedKey)
    }

    /// "host:port" for showing in settings.
    public var address: String {
        let host = baseURL.host() ?? ""
        return baseURL.port.map { "\(host):\($0)" } ?? host
    }

    public var authorizationHeaders: [String: String] { ["Authorization": "Bearer \(key)"] }

    public func url(_ path: String) -> URL {
        URL(string: path, relativeTo: baseURL)!.absoluteURL
    }
}

public struct DVBChannel: Sendable, Hashable, Identifiable {
    public let id: String
    public let number: Int
    public let name: String
    /// Gateway path of the picon ("/dvb/icon/<id>"), if any.
    public let iconPath: String?

    public init(id: String, number: Int, name: String, iconPath: String? = nil) {
        self.id = id
        self.number = number
        self.name = name
        self.iconPath = iconPath
    }
}

public struct DVBRecording: Sendable, Hashable {
    public enum State: String, Sendable, Hashable {
        case completed, recording, scheduled, failed, unknown
    }

    /// DVR entry id, used for `/dvb/recording/<id>.m3u8`.
    public let id: String
    public let state: State

    public init(id: String, state: State) {
        self.id = id
        self.state = state
    }
}

public struct DVBEvent: Sendable, Hashable, Identifiable {
    /// Opaque; past programmes look like "dvr:<uuid>".
    public let id: String
    public let channel: String
    public let title: String
    public let subtitle: String
    public let description: String
    public let genre: String
    public let start: Date
    public let end: Date
    public let recording: DVBRecording?

    public init(id: String, channel: String, title: String, subtitle: String = "", description: String = "",
                genre: String = "", start: Date, end: Date, recording: DVBRecording? = nil) {
        self.id = id
        self.channel = channel
        self.title = title
        self.subtitle = subtitle
        self.description = description
        self.genre = genre
        self.start = start
        self.end = end
        self.recording = recording
    }

    public func isAiring(at now: Date) -> Bool { start <= now && now < end }
}

public struct DVBStatus: Sendable, Equatable {
    public struct Tuner: Sendable, Equatable {
        public let name: String
        public let inUse: Bool
    }

    public let enabled: Bool
    public let retentionDays: Int
    public let tuners: [Tuner]
    public let tvheadendReachable: Bool
}

extension Programme: TimedItem {}
extension DVBEvent: TimedItem {}

// MARK: - decision rules

/// What selecting an "Anténa" programme does (spec/dvb-gateway.md, "App behaviour").
public enum AntennaAction: Sendable, Equatable {
    case future
    /// On air: live, and start-over from the growing recording when there is one.
    case airing(startOverRecordingID: String?)
    /// Ended and recorded: play the recording.
    case recording(id: String)
    /// Ended without a usable recording.
    case notRecorded

    public static func decide(_ event: DVBEvent, now: Date) -> AntennaAction {
        if now < event.start { return .future }
        if now < event.end {
            let recording = event.recording.flatMap { $0.state == .recording ? $0.id : nil }
            return .airing(startOverRecordingID: recording)
        }
        if let recording = event.recording, recording.state == .completed { return .recording(id: recording.id) }
        return .notRecorded
    }

    public static let notRecordedMessage = "Pořad nebyl nahrán"
}

// MARK: - parsing

public enum DVBParser {
    public static func channels(status: Int, body: Data) throws -> [DVBChannel] {
        let root = try object(status: status, body: body)
        let channels: [DVBChannel] = (root["channels"] as? [Any] ?? []).compactMap { item in
            guard let item = item as? [String: Any], let id = item["id"] as? String, !id.isEmpty else { return nil }
            return DVBChannel(
                id: id,
                number: (item["number"] as? NSNumber)?.intValue ?? 0,
                name: item["name"] as? String ?? id,
                iconPath: (item["icon"] as? String).flatMap { $0.isEmpty ? nil : $0 }
            )
        }
        return channels.sorted { $0.number < $1.number }
    }

    /// Events of one gateway day keyed by channel id, sorted by start, duplicates dropped.
    public static func epg(status: Int, body: Data) throws -> [String: [DVBEvent]] {
        let root = try object(status: status, body: body)
        var result: [String: [DVBEvent]] = [:]
        var seen = Set<String>()
        for case let item as [String: Any] in root["events"] as? [Any] ?? [] {
            guard let event = event(item), seen.insert("\(event.channel)|\(event.id)").inserted else { continue }
            result[event.channel, default: []].append(event)
        }
        return result.mapValues { $0.sorted { $0.start < $1.start } }
    }

    public static func status(status: Int, body: Data) throws -> DVBStatus {
        let root = try object(status: status, body: body)
        let tuners: [DVBStatus.Tuner] = (root["tuners"] as? [[String: Any]] ?? []).map {
            DVBStatus.Tuner(name: $0["name"] as? String ?? "", inUse: $0["inUse"] as? Bool ?? false)
        }
        return DVBStatus(
            enabled: root["enabled"] as? Bool ?? false,
            retentionDays: (root["retentionDays"] as? NSNumber)?.intValue ?? 0,
            tuners: tuners,
            tvheadendReachable: root["tvheadendReachable"] as? Bool ?? false
        )
    }

    /// Error for a non-2xx gateway answer, with the gateway's Czech `error` text when present.
    public static func error(status: Int, body: Data) -> CTError {
        let text = (try? JSONSerialization.jsonObject(with: body) as? [String: Any])?["error"] as? String
        let fallback: String
        switch status {
        case 401: fallback = "Brána odmítla klíč. Zkontrolujte nastavení."
        case 503: fallback = "Tuner je obsazený"
        default: fallback = "Brána Anténa neodpovídá"
        }
        return CTError(text.flatMap { $0.isEmpty ? nil : $0 } ?? fallback, code: "HTTP \(status)", detail: "gateway HTTP \(status)")
    }

    static func event(_ item: [String: Any]) -> DVBEvent? {
        guard let id = item["id"] as? String, !id.isEmpty,
              let channel = item["channel"] as? String, !channel.isEmpty,
              let start = CTParser.date(item["start"]), let end = CTParser.date(item["end"]), end > start
        else { return nil }
        var recording: DVBRecording?
        if let entry = item["recording"] as? [String: Any], let recordingID = entry["id"] as? String, !recordingID.isEmpty {
            recording = DVBRecording(id: recordingID, state: DVBRecording.State(rawValue: entry["state"] as? String ?? "") ?? .unknown)
        }
        return DVBEvent(
            id: id, channel: channel, title: item["title"] as? String ?? "",
            subtitle: item["subtitle"] as? String ?? "", description: item["description"] as? String ?? "",
            genre: item["genre"] as? String ?? "", start: start, end: end, recording: recording
        )
    }

    private static func object(status: Int, body: Data) throws -> [String: Any] {
        guard (200..<300).contains(status) else { throw error(status: status, body: body) }
        guard let root = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] else {
            throw CTError("Brána Anténa neodpovídá", detail: "gateway: body is not a JSON object")
        }
        return root
    }
}

// MARK: - client

public struct DVBClient: Sendable {
    public let config: GatewayConfig
    private let transport: HTTPTransport
    private let timeout: TimeInterval

    public init(config: GatewayConfig, transport: HTTPTransport = URLSessionTransport(), timeout: TimeInterval = 10) {
        self.config = config
        self.transport = transport
        self.timeout = timeout
    }

    public func status() async throws -> DVBStatus {
        let response = try await get("/dvb/status")
        return try DVBParser.status(status: response.status, body: response.body)
    }

    public func channels() async throws -> [DVBChannel] {
        let response = try await get("/dvb/channels")
        return try DVBParser.channels(status: response.status, body: response.body)
    }

    public func epg(day: BroadcastDay) async throws -> [String: [DVBEvent]] {
        let response = try await get("/dvb/epg?date=\(day)")
        return try DVBParser.epg(status: response.status, body: response.body)
    }

    public func liveURL(channelID: String) -> URL { config.url("/dvb/live/\(escape(channelID)).m3u8") }
    public func recordingURL(id: String) -> URL { config.url("/dvb/recording/\(escape(id)).m3u8") }
    public func iconURL(path: String) -> URL { config.url(path) }

    /// Requests the HLS playlist once before handing it to the player: this starts the gateway's
    /// session (first live request blocks until a segment exists) and turns 503/502/404 into the
    /// gateway's Czech message, which the player could not show.
    public func preparePlaylist(_ url: URL) async throws {
        let response = try await send(url, timeout: max(timeout, 30))
        guard (200..<300).contains(response.status) else {
            throw DVBParser.error(status: response.status, body: response.body)
        }
    }

    private func get(_ path: String) async throws -> HTTPResponse {
        try await send(config.url(path), timeout: timeout)
    }

    private func send(_ url: URL, timeout: TimeInterval) async throws -> HTTPResponse {
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: timeout)
        for (field, value) in config.authorizationHeaders { request.setValue(value, forHTTPHeaderField: field) }
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        do {
            return try await transport.send(request)
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw CTError("Bránu Anténa se nepodařilo kontaktovat", detail: "\(url.path): \(error.localizedDescription)")
        }
    }

    private func escape(_ component: String) -> String {
        component.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed.subtracting(["/"])) ?? component
    }
}

// MARK: - cache

/// Channels and daily EPG of the gateway, cached in memory. Today's EPG is refreshed after
/// `todayMaxAge` because recording states change while programmes air.
public actor AntennaStore {
    public typealias EPG = [String: [DVBEvent]]

    public nonisolated let client: DVBClient
    private let todayMaxAge: TimeInterval
    private let clock: @Sendable () -> Date
    private var channelCache: (fetchedAt: Date, channels: [DVBChannel])?
    private var epgCache: [BroadcastDay: (fetchedAt: Date, epg: EPG)] = [:]

    public init(client: DVBClient, todayMaxAge: TimeInterval = 120, clock: @escaping @Sendable () -> Date = Date.init) {
        self.client = client
        self.todayMaxAge = todayMaxAge
        self.clock = clock
    }

    public func channels() async throws -> [DVBChannel] {
        let now = clock()
        if let hit = channelCache, now.timeIntervalSince(hit.fetchedAt) < 600 { return hit.channels }
        let channels = try await client.channels()
        channelCache = (now, channels)
        return channels
    }

    public func epg(for day: BroadcastDay) async throws -> EPG {
        let now = clock()
        let today = BroadcastDay(containing: now)
        if let hit = epgCache[day], day < today || now.timeIntervalSince(hit.fetchedAt) < todayMaxAge {
            return hit.epg
        }
        let epg = try await client.epg(day: day)
        epgCache[day] = (now, epg)
        return epg
    }

    /// The event on air per channel id. The gateway's broadcast day starts at 05:00, so the
    /// previous day is checked too.
    public func onAir(at now: Date) async -> [String: DVBEvent] {
        let today = BroadcastDay(containing: now)
        var result: [String: DVBEvent] = [:]
        for day in [today, today.adding(days: -1)] {
            guard let epg = try? await epg(for: day) else { continue }
            for (channel, events) in epg where result[channel] == nil {
                result[channel] = events.first { $0.isAiring(at: now) }
            }
        }
        return result
    }
}
