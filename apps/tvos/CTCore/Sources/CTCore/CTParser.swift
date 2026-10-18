import Foundation

/// Turns raw CT API responses into models. The API is undocumented, so parsing is
/// deliberately loose: unknown or missing fields never crash, they just mean "no data".
public enum CTParser {
    public static func liveStreams(status: Int, body: Data, channelID: String) throws -> LiveStreams {
        guard (200..<300).contains(status) else { throw httpError(status: status, body: body) }
        let urls = object(body)?["streamUrls"] as? [String: Any] ?? [:]
        if urls["isBlocked"] as? Bool == true {
            throw CTError(CTError.blocked, detail: "\(channelID) is blocked (geo restriction?)")
        }
        guard let main = url(urls["main"]) else {
            throw CTError(CTError.genericStream, detail: "\(channelID): no stream URL in API response")
        }
        return LiveStreams(main: main, timeshift: url(urls["timeshift"]))
    }

    public static func vod(status: Int, body: Data, idec: String) throws -> VODResult {
        guard (200..<300).contains(status) else { throw httpError(status: status, body: body) }
        let data = object(body) ?? [:]
        for stream in data["streams"] as? [[String: Any]] ?? [] {
            if let url = url(stream["url"]) { return .stream(url) }
        }
        if let channel = data["platformChannel"] as? String, !channel.isEmpty {
            return .stillLive(channelID: channel)
        }
        throw CTError(CTError.videoUnavailable, detail: "\(idec): no stream URL in API response")
    }

    /// Programmes of one broadcast day keyed by channel slug.
    public static func schedules(status: Int, body: Data) throws -> [String: [Programme]] {
        guard (200..<300).contains(status) else {
            throw CTError(CTError.scheduleFailed, detail: "schedule HTTP \(status)")
        }
        let root = object(body)
        guard let plans = (root?["data"] as? [String: Any])?["TVProgramDailyChannelsPlanV2"] as? [Any] else {
            throw CTError(CTError.scheduleFailed, detail: "schedule query failed: \(String(describing: root?["errors"]))")
        }
        var result: [String: [Programme]] = [:]
        for case let plan as [String: Any] in plans {
            guard let channel = plan["channel"] as? String else { continue }
            var seen = Set<String>()
            var programmes: [Programme] = []
            for case let item as [String: Any] in plan["program"] as? [Any] ?? [] {
                guard let programme = programme(item, channel: channel), seen.insert(programme.id).inserted else {
                    continue  // skip malformed or duplicate entries, keep the rest
                }
                programmes.append(programme)
            }
            result[channel] = programmes.sorted { $0.start < $1.start }
        }
        return result
    }

    /// Error for a non-2xx response, keeping CT's Czech explanation when present.
    static func httpError(status: Int, body: Data) -> CTError {
        let message = object(body)?["message"] as? [String: Any]
        let code = (message?["status"] as? [String: Any])?["name"] as? String
        let text = (message?["labels"] as? [String: Any])?["textShort"] as? String
        let detail = ["HTTP \(status)", code].compactMap { $0 }.joined(separator: " ")
        var userMessage = nonEmpty(text) ?? CTError.videoUnavailable
        if code == CTError.unsupportedDRM {
            userMessage += "\n" + CTError.drmHint
        }
        return CTError(userMessage, code: nonEmpty(code), detail: detail)
    }

    static func programme(_ item: [String: Any], channel: String) -> Programme? {
        let idec: String
        switch item["idec"] {
        case let value as String where !value.isEmpty: idec = value
        case let value as NSNumber: idec = value.stringValue
        default: return nil
        }
        guard let start = date(item["start"]), let end = date(item["end"]), end > start else { return nil }
        return Programme(
            channel: channel,
            idec: idec,
            title: item["title"] as? String ?? "",
            start: start,
            end: end,
            isPlayableNow: item["isPlayableNow"] as? Bool ?? false,
            liveOnly: item["liveOnly"] as? Bool ?? false,
            episodeTitle: item["episodeTitle"] as? String ?? "",
            description: item["description"] as? String ?? "",
            genre: item["genre"] as? String ?? "",
            imageURL: url(item["imageUrl"])
        )
    }

    static func date(_ value: Any?) -> Date? {
        guard let text = value as? String else { return nil }
        let withFraction = Date.ISO8601FormatStyle(includingFractionalSeconds: true)
        return (try? withFraction.parse(text)) ?? (try? Date.ISO8601FormatStyle().parse(text))
    }

    private static func object(_ body: Data) -> [String: Any]? {
        (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
    }

    private static func url(_ value: Any?) -> URL? {
        guard let text = nonEmpty(value as? String) else { return nil }
        return URL(string: text)
    }

    private static func nonEmpty(_ text: String?) -> String? {
        guard let text, !text.isEmpty else { return nil }
        return text
    }
}
