import Foundation

// "Přeskočit reklamu" for finished Anténa recordings (spec/ad-breaks.md, "App behaviour").
// The household's gateway stores detected ad breaks as positions in the recording; the app only
// offers a seek past them. The recording itself is never changed.

/// One detected ad break, in seconds from the beginning of the recording (its HLS position).
public struct AdBreak: Sendable, Hashable {
    public let start: Double
    public let end: Double
    public let confidence: Double

    public init(start: Double, end: Double, confidence: Double = 1.0) {
        self.start = start
        self.end = end
        self.confidence = confidence
    }
}

public enum AdBreaks {
    /// The button is not offered during the last seconds of a break.
    public static let minRemainingSeconds: Double = 3

    /// Where "Přeskočit reklamu" seeks to at `position`, or nil when the button should be hidden:
    /// the break's end while `start <= position < end - minRemainingSeconds`.
    public static func skipTarget(_ breaks: [AdBreak], position: Double) -> Double? {
        guard position.isFinite else { return nil }
        return breaks.first { $0.start <= position && position < $0.end - minRemainingSeconds }?.end
    }

    /// Parses `GET /dvb/recording/<id>/breaks`. A `pending` answer has no breaks yet; entries
    /// without numeric `start < end` are dropped, the rest sorted by start, confidence defaults to 1.
    /// Non-2xx (404 when the feature is off on the gateway) or a non-JSON body throws.
    public static func parse(status: Int, body: Data) throws -> [AdBreak] {
        guard (200..<300).contains(status) else { throw DVBParser.error(status: status, body: body) }
        guard let root = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] else {
            throw CTError("Brána Anténa neodpovídá", detail: "gateway breaks: body is not a JSON object")
        }
        let breaks: [AdBreak] = (root["breaks"] as? [Any] ?? []).compactMap { item in
            guard let item = item as? [String: Any],
                  let start = number(item["start"]), let end = number(item["end"]), start < end else { return nil }
            return AdBreak(start: start, end: end, confidence: number(item["confidence"]) ?? 1.0)
        }
        return breaks.sorted { $0.start < $1.start }
    }

    /// A finite JSON number (not a string, not a boolean).
    private static func number(_ value: Any?) -> Double? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        let double = number.doubleValue
        return double.isFinite ? double : nil
    }
}
