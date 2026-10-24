import Foundation

// The programme page (spec/apps.md "Programme page (T15)"): what the guide opens for a selected programme, with the
// TMDb facts the gateway adds (spec/dvb-gateway.md "Programme details"). Golden cases: programme_page_cases.json.

/// A button of the programme page, in the order they are shown.
public enum ProgrammeButton: String, Sendable, CaseIterable {
    case resume, startOver, live, openOfficialApp
}

public enum ProgrammePage {
    public enum Source: String, Sendable {
        case ct, dvb
    }

    public enum Phase: String, Sendable {
        case future, airing, ended
    }

    /// The same decision as the old "Pokračovat / Od začátku / Živě" choice, as a list; every page adds "Zpět".
    /// - `startable`: airing and playable from the start (CT buffer, or an Anténa recording in state `recording`).
    /// - `playable`: ended and recorded (a completed Anténa recording). An ended CT programme is always offered:
    ///   playing it shows CT's own text when it cannot be played (licence, DRM), as the decision rules do.
    /// - `resume`: a "Pokračovat" offer exists; it needs a playable start.
    public static func buttons(source: Source, phase: Phase, startable: Bool, playable: Bool,
                               resume: Bool) -> [ProgrammeButton] {
        var buttons: [ProgrammeButton] = []
        switch phase {
        case .future:
            return []
        case .airing:
            if startable {
                if resume { buttons.append(.resume) }
                buttons.append(.startOver)
            }
            buttons.append(.live)
        case .ended:
            if playable || source == .ct {
                if resume { buttons.append(.resume) }
                buttons.append(.startOver)
            }
        }
        // ČT programmes that have started: CT's own app plays what we cannot (DRM-only).
        if source == .ct { buttons.append(.openOfficialApp) }
        return buttons
    }

    /// Below this TMDb's average says little (the gateway drops it too).
    public static let minVotes = 20

    /// "Hodnocení TMDB 7,4 (1 234 hlasů)", nil below `minVotes`.
    public static func ratingLine(value: Double, votes: Int) -> String? {
        guard votes >= minVotes, value.isFinite else { return nil }
        return "Hodnocení TMDB \(oneDecimal(value)) (\(grouped(votes)) \(votesWord(votes)))"
    }

    /// Czech plural of "hlas" (CLDR cs: 1 one, 2–4 few, else other; no fractions here).
    public static func votesWord(_ count: Int) -> String {
        switch count {
        case 1: return "hlas"
        case 2...4: return "hlasy"
        default: return "hlasů"
        }
    }

    /// "Původní název: …" when the original title differs from both the guide's title and TMDb's Czech one.
    public static func originalTitleLine(original: String, title: String, guideTitle: String) -> String? {
        let original = original.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !original.isEmpty, !sameTitle(original, title), !sameTitle(original, guideTitle) else { return nil }
        return "Původní název: \(original)"
    }

    /// Length in whole minutes for the facts line ("110 min").
    public static func minutesText(start: Date, end: Date) -> String? {
        let minutes = Int((end.timeIntervalSince(start) / 60).rounded())
        return minutes > 0 ? "\(minutes) min" : nil
    }

    /// One decimal, half up (on the decimal value, so 6.25 → 6,3), decimal comma.
    static func oneDecimal(_ value: Double) -> String {
        var exact = Decimal(string: "\(value)", locale: Locale(identifier: "en_US_POSIX")) ?? Decimal(value)
        var rounded = Decimal()
        NSDecimalRound(&rounded, &exact, 1, .plain)
        let tenths = NSDecimalNumber(decimal: rounded * 10).intValue
        return "\(tenths / 10),\(abs(tenths % 10))"
    }

    /// Thousands separated by a space: "1 000 000".
    static func grouped(_ number: Int) -> String {
        var digits = String(abs(number))
        var groups: [String] = []
        while digits.count > 3 {
            groups.insert(String(digits.suffix(3)), at: 0)
            digits.removeLast(3)
        }
        groups.insert(digits, at: 0)
        return (number < 0 ? "-" : "") + groups.joined(separator: " ")
    }

    private static func sameTitle(_ a: String, _ b: String) -> Bool {
        a.compare(b, options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive]) == .orderedSame
    }
}

// MARK: - details from the gateway

/// `GET /dvb/details`: the TMDb match of a programme, if the gateway found one.
public struct ProgrammeDetails: Sendable, Equatable {
    public struct Rating: Sendable, Equatable {
        public let value: Double
        public let votes: Int
    }

    public struct Match: Sendable, Equatable {
        public let kind: String
        public let title: String
        public let originalTitle: String
        public let year: Int?
        public let overview: String
        public let genres: [String]
        public let runtimeMinutes: Int?
        public let rating: Rating?
        /// Gateway path of the poster (`/dvb/details/image/…`), loaded with the app key; never a URL elsewhere.
        public let posterPath: String?
    }

    /// TMDb's terms: shown with any TMDb data.
    public static let defaultAttribution =
        "This application uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB."

    public let match: Match?
    public let attribution: String
}

/// Which programme to ask about: always an id the gateway knows, never free text.
public enum ProgrammeDetailsQuery: Sendable, Hashable {
    /// An Anténa event id from `/dvb/epg`.
    case dvb(eventID: String)
    /// A CT programme of the schedule of `day` (the broadcast day it was listed in).
    case ct(channel: String, day: BroadcastDay, idec: String)

    var path: String {
        var parts = URLComponents()
        parts.path = "/dvb/details"
        switch self {
        case .dvb(let id):
            parts.queryItems = [URLQueryItem(name: "source", value: "dvb"), URLQueryItem(name: "id", value: id)]
        case .ct(let channel, let day, let idec):
            parts.queryItems = [URLQueryItem(name: "source", value: "ct"), URLQueryItem(name: "channel", value: channel),
                                URLQueryItem(name: "date", value: day.description), URLQueryItem(name: "idec", value: idec)]
        }
        // `+` would read as a space in a query
        parts.percentEncodedQuery = parts.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
        return parts.string ?? "/dvb/details"
    }
}

public enum ProgrammeDetailsParser {
    /// nil for `404` (unknown programme, or details off on the gateway): the page without the TMDb lines.
    public static func parse(status: Int, body: Data) throws -> ProgrammeDetails? {
        if status == 404 { return nil }
        guard (200..<300).contains(status) else { throw DVBParser.error(status: status, body: body) }
        guard let root = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] else {
            throw CTError("Brána Anténa neodpovídá", detail: "details: body is not a JSON object")
        }
        let attribution = (root["attribution"] as? String).flatMap { $0.isEmpty ? nil : $0 }
            ?? ProgrammeDetails.defaultAttribution
        return ProgrammeDetails(match: (root["match"] as? [String: Any]).flatMap(match), attribution: attribution)
    }

    static func match(_ item: [String: Any]) -> ProgrammeDetails.Match? {
        guard let title = text(item["title"]) else { return nil }
        var rating: ProgrammeDetails.Rating?
        if let entry = item["rating"] as? [String: Any], let value = number(entry["value"]), let votes = number(entry["votes"]),
           value.isFinite, votes >= 0 {
            rating = ProgrammeDetails.Rating(value: value, votes: Int(votes))
        }
        return ProgrammeDetails.Match(
            kind: text(item["kind"]) ?? "",
            title: title,
            originalTitle: text(item["originalTitle"]) ?? "",
            year: number(item["year"]).map { Int($0) }.flatMap { $0 > 1800 && $0 < 3000 ? $0 : nil },
            overview: text(item["overview"]) ?? "",
            genres: (item["genres"] as? [Any] ?? []).compactMap(text),
            runtimeMinutes: number(item["runtimeMinutes"]).map { Int($0) }.flatMap { $0 > 0 ? $0 : nil },
            rating: rating,
            posterPath: text(item["poster"]).flatMap { isGatewayImagePath($0) ? $0 : nil }
        )
    }

    /// Only the gateway's own image route: no scheme or host, no `..`, no query.
    public static func isGatewayImagePath(_ path: String) -> Bool {
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/")
        return path.hasPrefix("/dvb/details/image/") && !path.contains("..") && !path.contains("//")
            && path.unicodeScalars.allSatisfy(allowed.contains)
    }

    private static func text(_ value: Any?) -> String? {
        guard let text = (value as? String)?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else { return nil }
        return text
    }

    private static func number(_ value: Any?) -> Double? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        return number.doubleValue
    }
}

extension DVBClient {
    /// The TMDb details of a programme; nil when the gateway has none for it (404).
    public func details(_ query: ProgrammeDetailsQuery) async throws -> ProgrammeDetails? {
        let response = try await get(query.path)
        return try ProgrammeDetailsParser.parse(status: response.status, body: response.body)
    }

    /// A poster from `/dvb/details/image/…` (a path from `details`, never a URL); nil when it is not an image.
    /// Memory only: the caller passes a transport without a disk cache.
    public func detailsImage(path: String) async throws -> Data? {
        guard ProgrammeDetailsParser.isGatewayImagePath(path) else { return nil }
        let response = try await get(path)
        let body = response.body
        let isJPEG = body.starts(with: [0xFF, 0xD8])
        let isPNG = body.starts(with: [0x89, 0x50, 0x4E, 0x47])
        guard response.status == 200, isJPEG || isPNG else { return nil }
        return body
    }
}
