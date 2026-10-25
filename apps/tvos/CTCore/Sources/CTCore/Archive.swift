import Foundation

// The Archiv tab (spec/apps.md "Archiv (T13)"): iVysílání and the household's recordings in shared categories, from
// the gateway's `/dvb/archive` (spec/dvb-gateway.md "Archive"). The gateway builds the catalogue; the app lists,
// renders cards and plays (iVysílání episodes still resolve on the TV, recordings through the timeline player).

/// Where a card image comes from. Only ČT's own https images or the gateway's own image routes; anything else is
/// dropped by the parser.
public enum ArchiveImage: Sendable, Hashable {
    /// A ČT image on ČT's CDN, loaded without the key.
    case ct(URL)
    /// A TMDb poster through the gateway (`/dvb/details/image/…`), with the key: TMDb data.
    case poster(path: String)
    /// A recording's still (`/dvb/recording/<id>/thumb.jpg`), with the key, only while thumbnails are allowed.
    case thumbnail(recordingID: String)

    /// The card's `image` (a URL or a gateway path), nil when it is neither a ČT https image nor a gateway image route.
    public init?(_ text: String) {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.hasPrefix("/") {
            if ProgrammeDetailsParser.isGatewayImagePath(text) {
                self = .poster(path: text)
                return
            }
            guard let id = Self.thumbnailID(text) else { return nil }
            self = .thumbnail(recordingID: id)
            return
        }
        guard let url = URL(string: text), url.scheme?.lowercased() == "https", let host = url.host(), !host.isEmpty,
              url.user == nil, url.password == nil else { return nil }
        self = .ct(url)
    }

    /// What the resume store keeps (`image`): the URL or the gateway path, as the card had it.
    public var stored: String {
        switch self {
        case .ct(let url): return url.absoluteString
        case .poster(let path): return path
        case .thumbnail(let id): return "/dvb/recording/\(DVBClient.escapePath(id))/thumb.jpg"
        }
    }

    public var isTMDb: Bool {
        if case .poster = self { return true }
        return false
    }

    private static func thumbnailID(_ path: String) -> String? {
        let prefix = "/dvb/recording/", suffix = "/thumb.jpg"
        guard path.hasPrefix(prefix), path.hasSuffix(suffix), path.count > prefix.count + suffix.count,
              !path.contains("?"), !path.contains("#") else { return nil }
        let raw = String(path.dropFirst(prefix.count).dropLast(suffix.count))
        guard !raw.contains("/"), let id = raw.removingPercentEncoding, !id.isEmpty, !id.contains("/"),
              id != ".", id != ".." else { return nil }
        return id
    }
}

public struct ArchiveCard: Sendable, Hashable, Identifiable {
    public enum Source: String, Sendable, Hashable {
        /// iVysílání only.
        case ct
        /// Our recordings only.
        case rec
        /// Both, merged into one card (iVysílání first).
        case both
    }

    public enum Kind: String, Sendable, Hashable {
        case movie, series
    }

    public struct Rating: Sendable, Hashable {
        public let value: Double
        public let votes: Int

        public init(value: Double, votes: Int) {
            self.value = value
            self.votes = votes
        }
    }

    /// `ct:<ČT show id>` or `rec:<normalised title>`: the show page's id.
    public let id: String
    public let source: Source
    public let title: String
    /// nil when the gateway does not know (ČT's search gives no show type): neither "film" nor "seriál".
    public let kind: Kind?
    public let year: Int?
    public let image: ArchiveImage?
    public let rating: Rating?
    public let genres: [String]
    public let episodeCount: Int?
    public let durationMinutes: Int?
    /// The gateway says the card carries TMDb data (`"tmdb": true`, e.g. year or genres from TMDb).
    public let tmdb: Bool

    public init(id: String, source: Source, title: String, kind: Kind?, year: Int? = nil, image: ArchiveImage? = nil,
                rating: Rating? = nil, genres: [String] = [], episodeCount: Int? = nil, durationMinutes: Int? = nil,
                tmdb: Bool = false) {
        self.id = id
        self.source = source
        self.title = title
        self.kind = kind
        self.year = year
        self.image = image
        self.rating = rating
        self.genres = genres
        self.episodeCount = episodeCount
        self.durationMinutes = durationMinutes
        self.tmdb = tmdb
    }
}

public struct ArchiveRow: Sendable, Hashable, Identifiable {
    /// `recordings` or a category slug (`3947-filmy`).
    public let id: String
    public let title: String
    public let cards: [ArchiveCard]
    /// The category has more than the row shows.
    public let more: Bool
}

public struct ArchiveHome: Sendable, Equatable {
    public let rows: [ArchiveRow]
    public let attribution: String
    /// The gateway's Czech text when iVysílání did not answer (our recordings are still listed).
    public let error: String?
}

public struct ArchiveGenre: Sendable, Hashable, Identifiable {
    public let id: String
    public let title: String
}

/// One page of a category or of a search.
public struct ArchivePage: Sendable, Equatable {
    public let title: String
    public let genres: [ArchiveGenre]
    public let cards: [ArchiveCard]
    public let nextPage: Int?
    public let attribution: String
    /// The gateway's Czech text when iVysílání did not answer (our recordings are still listed).
    public let error: String?
}

public struct ArchiveEpisode: Sendable, Hashable, Identifiable {
    public enum Source: Sendable, Hashable {
        case ct(idec: String)
        case rec(recordingID: String)
    }

    public let source: Source
    public let title: String
    public let description: String
    public let durationSeconds: TimeInterval
    /// When it was broadcast (the programme's start).
    public let aired: Date?
    /// Display name only ("ČT1", "Nova HD").
    public let channel: String
    /// The ČT channel to play the buffer from (`channelId`, CT API id); nil = VOD by idec only.
    public let ctChannel: Channel?
    public let playable: Bool

    public init(source: Source, title: String, description: String = "", durationSeconds: TimeInterval,
                aired: Date?, channel: String, ctChannel: Channel? = nil, playable: Bool) {
        self.source = source
        self.title = title
        self.description = description
        self.durationSeconds = durationSeconds
        self.aired = aired
        self.channel = channel
        self.ctChannel = ctChannel
        self.playable = playable
    }

    /// The "Pokračovat" key, as everywhere: `ct:<idec>` / `dvb:<recording id>`.
    public var resumeKey: String {
        switch source {
        case .ct(let idec): return ResumeRules.ctKey(idec: idec)
        case .rec(let id): return ResumeRules.dvbKey(recordingID: id)
        }
    }

    public var id: String { resumeKey }
}

public struct ArchiveEpisodeGroup: Sendable, Hashable {
    public let title: String
    /// The season these episodes belong to (the id for `?season=`), nil for our recordings.
    public let season: String?
    public let episodes: [ArchiveEpisode]
    public let nextPage: Int?

    /// The group of our recorded episodes ("Nahráno z antény"): every episode is a recording.
    public var isRecordings: Bool {
        !episodes.isEmpty && episodes.allSatisfy { if case .rec = $0.source { return true } else { return false } }
    }
}

public struct ArchiveShow: Sendable, Equatable {
    public let card: ArchiveCard
    public let description: String
    /// As the gateway sends it: ČT's own line ("Režie: … Hrají: …") or names.
    public let creators: [String]
    public let countries: [String]
    public let seasons: [ArchiveGenre]
    public let groups: [ArchiveEpisodeGroup]
    public let attribution: String
    /// The gateway's Czech text when iVysílání did not answer (only our recordings are listed).
    public let error: String?
}

/// The episodes of one season (`?season=<id>&page=<n>`).
public struct ArchiveEpisodePage: Sendable, Equatable {
    public let episodes: [ArchiveEpisode]
    public let nextPage: Int?
}

public enum ArchiveSort: String, Sendable, CaseIterable {
    case popular, newest, az

    public var title: String {
        switch self {
        case .popular: return "Nejsledovanější"
        case .newest: return "Naposledy vysílané"
        case .az: return "A–Z"
        }
    }
}

// MARK: - rendering rules

public enum ArchiveRules {
    /// The tab without a configured gateway (owner, 2026-10-25).
    public static let noGatewayText = "Archiv potřebuje připojení k serveru. Nastavte ho v Nastavení → Server."
    public static let continueRowTitle = "Pokračovat ve sledování"
    public static let continueRowLimit = 20
    public static let removeFromRow = "Odebrat z řady"
    public static let recordingsGroupTitle = "Nahráno z antény"
    public static let searchHint = "Hledat v archivu"
    public static let loadFailed = "Archiv se nepodařilo načíst"
    public static let nothingFound = "Nic nenalezeno"
    public static let fromStart = "Přehrát od začátku"

    /// The gateway's display string ("Režie: … Hrají: …") exactly as sent.
    public static func creatorsLine(_ creators: [String]) -> String? {
        creators.isEmpty ? nil : creators.joined(separator: ", ")
    }

    /// Search: results after 0.5 s without typing, at least 2 characters, at most 100 sent.
    public static let searchDebounce: Duration = .milliseconds(500)
    public static let searchMinLength = 2
    public static let searchMaxLength = 100

    /// The text to search for, or nil when it is too short.
    public static func searchQuery(_ text: String) -> String? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count >= searchMinLength else { return nil }
        return String(trimmed.prefix(searchMaxLength))
    }

    /// "★ 7,4", only with at least 20 votes (the rating line rules of programme_page_cases.json).
    public static func ratingBadge(_ rating: ArchiveCard.Rating?) -> String? {
        guard let rating, rating.votes >= ProgrammePage.minVotes, rating.value.isFinite else { return nil }
        return "★ \(ProgrammePage.oneDecimal(rating.value))"
    }

    /// "2021 · film", "seriál", "2021", "" (no kind and no year).
    public static func metaLine(_ card: ArchiveCard) -> String {
        [card.year.map(String.init), kindText(card.kind)].compactMap { $0 }.joined(separator: " · ")
    }

    public static func kindText(_ kind: ArchiveCard.Kind?) -> String? {
        kind.map { $0 == .movie ? "film" : "seriál" }
    }

    /// The source icons of a card: globe = iVysílání, antenna = our recording, both for `both`.
    public static func icons(_ source: ArchiveCard.Source) -> [ChannelPrefs.Source] {
        switch source {
        case .ct: return [.ct]
        case .rec: return [.dvb]
        case .both: return [.ct, .dvb]
        }
    }

    public static func sourceText(_ source: ArchiveCard.Source) -> String {
        switch source {
        case .ct: return "iVysílání"
        case .rec: return "Z antény"
        case .both: return "iVysílání a z antény"
        }
    }

    /// The card carries TMDb data (the gateway decides, `"tmdb": true`): the TMDB logo and sentence must be on screen.
    public static func showsTMDb(_ card: ArchiveCard) -> Bool { card.tmdb }

    /// Czech plural of "díl" (1 díl, 2–4 díly, else dílů).
    public static func episodesText(_ count: Int) -> String {
        switch count {
        case 1: return "1 díl"
        case 2...4: return "\(count) díly"
        default: return "\(count) dílů"
        }
    }

    /// Show page facts: source · year · kind · countries · episode count.
    public static func showFacts(_ show: ArchiveShow) -> String {
        let card = show.card
        var parts = [sourceText(card.source)]
        if let year = card.year { parts.append(String(year)) }
        if let kind = kindText(card.kind) { parts.append(kind) }
        if !show.countries.isEmpty { parts.append(show.countries.joined(separator: ", ")) }
        if card.kind == .series, let count = card.episodeCount, count > 0 { parts.append(episodesText(count)) }
        if card.kind == .movie, let minutes = card.durationMinutes, minutes > 0 { parts.append("\(minutes) min") }
        return parts.joined(separator: " · ")
    }

    /// "1/26 Od začátku · 53 min · ČT1 9. 1." (the year added when it is not this year's).
    public static func episodeLine(_ episode: ArchiveEpisode, now: Date, calendar: Calendar = .prague) -> String {
        var parts: [String] = []
        if !episode.title.isEmpty { parts.append(episode.title) }
        let minutes = Int((episode.durationSeconds / 60).rounded())
        if minutes > 0 { parts.append("\(minutes) min") }
        var aired = episode.channel
        if let date = episode.aired {
            let day = calendar.dateComponents([.year, .month, .day], from: date)
            var text = "\(day.day ?? 0). \(day.month ?? 0)."
            if day.year != calendar.component(.year, from: now) { text += " \(day.year ?? 0)" }
            aired = aired.isEmpty ? text : "\(aired) \(text)"
        }
        if !aired.isEmpty { parts.append(aired) }
        return parts.joined(separator: " · ")
    }

    /// What selecting an episode (or the film's first button) does: continue from the offer, else from the start.
    public static func episodeButton(resumeAt: TimeInterval?) -> String {
        resumeAt.map(ResumeRules.buttonTitle) ?? "Přehrát"
    }

    /// The film's buttons: [Pokračovat od …] Přehrát, then Zpět (as T15: the offer first and focused).
    public static func filmButtons(resume: Bool) -> [ProgrammeButton] {
        resume ? [.resume, .startOver] : [.startOver]
    }

    /// The "Pokračovat ve sledování" row (archive_cases.json "continueRow"): entries with a title, newest first, at
    /// most 20; equal times by key so the order is stable.
    public static func continueRow(_ entries: [String: ResumeEntry], limit: Int = continueRowLimit) -> [(key: String, entry: ResumeEntry)] {
        entries
            .filter { !($0.value.title ?? "").trimmingCharacters(in: .whitespaces).isEmpty }
            .sorted { $0.value.savedAt != $1.value.savedAt ? $0.value.savedAt > $1.value.savedAt : $0.key < $1.key }
            .prefix(limit)
            .map { ($0.key, $0.value) }
    }

    /// A ČT archive episode as the guide's programme, for the existing decision rules (VOD by idec, the buffer when
    /// it began < 3 h ago and the channel is known; without one VOD only).
    public static func programme(idec: String, title: String, episode: String, aired: Date?, duration: TimeInterval,
                                 playable: Bool, channel: Channel?) -> (Programme, Channel?) {
        // Without a broadcast time the item is treated as long past: VOD.
        let start = aired ?? Date(timeIntervalSince1970: 0)
        let programme = Programme(channel: channel?.slug ?? "", idec: idec, title: title, start: start,
                                  end: start.addingTimeInterval(max(duration, 1)), isPlayableNow: playable,
                                  episodeTitle: episode)
        return (programme, channel)
    }

    /// "ČT1", "CT 1", "ct1", "CH_1" → the channel; nil for anything else.
    public static func ctChannel(_ text: String) -> Channel? {
        if let channel = Channels.byID(text) ?? Channels.bySlug(text) { return channel }
        let key = AntennaCTMatch.normalizedTitle(text)
        return Channels.all.first { AntennaCTMatch.normalizedTitle($0.name) == key }
    }
}

// MARK: - parsing

public enum ArchiveParser {
    public static func home(status: Int, body: Data) throws -> ArchiveHome {
        let root = try object(status: status, body: body)
        let rows: [ArchiveRow] = array(root["rows"]).compactMap { item in
            guard let id = text(item["id"]) else { return nil }
            let cards = cards(item["cards"])
            guard !cards.isEmpty else { return nil }
            return ArchiveRow(id: id, title: text(item["title"]) ?? id, cards: cards, more: item["more"] as? Bool ?? false)
        }
        return ArchiveHome(rows: rows, attribution: attribution(root), error: text(root["error"]))
    }

    /// `GET /dvb/archive/categories` (T23): the categories in the gateway's order; entries without an id or a title
    /// and repeated ids are dropped.
    public static func categories(status: Int, body: Data) throws -> [ArchiveCategory] {
        let root = try object(status: status, body: body)
        var seen = Set<String>()
        return array(root["categories"]).compactMap { item -> ArchiveCategory? in
            guard let id = text(item["id"]), let title = text(item["title"]), id != "recordings" else { return nil }
            return ArchiveCategory(id: id, title: title)
        }.filter { seen.insert($0.id).inserted }
    }

    public static func page(status: Int, body: Data) throws -> ArchivePage {
        let root = try object(status: status, body: body)
        let genres: [ArchiveGenre] = array(root["genres"]).compactMap(genre)
        return ArchivePage(title: text(root["title"]) ?? "", genres: genres, cards: cards(root["cards"]),
                           nextPage: page(root["nextPage"]), attribution: attribution(root), error: text(root["error"]))
    }

    public static func show(status: Int, body: Data) throws -> ArchiveShow {
        let root = try object(status: status, body: body)
        guard let cardItem = root["card"] as? [String: Any], let card = card(cardItem) else {
            throw CTError("Pořad se nepodařilo načíst", detail: "archive show: no card")
        }
        let groups: [ArchiveEpisodeGroup] = array(root["groups"]).compactMap { item in
            let episodes = episodes(item["episodes"])
            guard !episodes.isEmpty else { return nil }
            return ArchiveEpisodeGroup(title: text(item["title"]) ?? "", season: text(item["season"]), episodes: episodes,
                                       nextPage: page(item["nextPage"]))
        }
        return ArchiveShow(card: card, description: text(root["description"]) ?? "", creators: texts(root["creators"]),
                           countries: texts(root["countries"]), seasons: array(root["seasons"]).compactMap(genre),
                           groups: groups, attribution: attribution(root), error: text(root["error"]))
    }

    /// A season's episodes: `{"episodes", "nextPage"}`, or a show-shaped answer whose first group holds them.
    public static func episodePage(status: Int, body: Data) throws -> ArchiveEpisodePage {
        let root = try object(status: status, body: body)
        if root["episodes"] != nil {
            return ArchiveEpisodePage(episodes: episodes(root["episodes"]), nextPage: page(root["nextPage"]))
        }
        let group = array(root["groups"]).first { !episodes($0["episodes"]).isEmpty && !(episodes($0["episodes"]).allSatisfy {
            if case .rec = $0.source { return true } else { return false }
        }) }
        return ArchiveEpisodePage(episodes: episodes(group?["episodes"]),
                                  nextPage: page(group?["nextPage"]) ?? page(root["nextPage"]))
    }

    static func card(_ item: [String: Any]) -> ArchiveCard? {
        guard let id = text(item["id"]), let title = text(item["title"]),
              let source = ArchiveCard.Source(rawValue: text(item["source"]) ?? "") else { return nil }
        var rating: ArchiveCard.Rating?
        if let entry = item["rating"] as? [String: Any], let value = number(entry["value"]), let votes = number(entry["votes"]),
           value.isFinite, value >= 0, votes >= 0 {
            rating = ArchiveCard.Rating(value: value, votes: Int(votes))
        }
        return ArchiveCard(
            id: id, source: source, title: title,
            kind: ArchiveCard.Kind(rawValue: text(item["kind"]) ?? ""),
            year: number(item["year"]).map { Int($0) }.flatMap { $0 > 1800 && $0 < 3000 ? $0 : nil },
            image: text(item["image"]).flatMap(ArchiveImage.init),
            rating: rating,
            genres: texts(item["genres"]),
            episodeCount: number(item["episodeCount"]).map { Int($0) }.flatMap { $0 > 0 ? $0 : nil },
            durationMinutes: number(item["durationMinutes"]).map { Int($0) }.flatMap { $0 > 0 ? $0 : nil },
            tmdb: item["tmdb"] as? Bool ?? false
        )
    }

    static func episode(_ item: [String: Any]) -> ArchiveEpisode? {
        let source: ArchiveEpisode.Source
        switch text(item["source"]) {
        case "ct":
            guard let idec = text(item["idec"]) else { return nil }
            source = .ct(idec: idec)
        case "rec":
            guard let id = text(item["recordingId"]) else { return nil }
            source = .rec(recordingID: id)
        default:
            return nil
        }
        return ArchiveEpisode(
            source: source, title: text(item["title"]) ?? "", description: text(item["description"]) ?? "",
            durationSeconds: number(item["durationSeconds"]).flatMap { $0.isFinite && $0 > 0 ? $0 : nil } ?? 0,
            aired: CTParser.date(item["aired"]), channel: text(item["channel"]) ?? "",
            // `channelId` first; null, missing or unknown → the display name ("ČT1" → CH_1); else VOD only
            ctChannel: text(item["channelId"]).flatMap(Channels.byID) ?? text(item["channel"]).flatMap(ArchiveRules.ctChannel),
            playable: item["playable"] as? Bool ?? true
        )
    }

    private static func cards(_ value: Any?) -> [ArchiveCard] {
        var seen = Set<String>()
        return array(value).compactMap(card).filter { seen.insert($0.id).inserted }
    }

    private static func episodes(_ value: Any?) -> [ArchiveEpisode] {
        var seen = Set<String>()
        return array(value).compactMap(episode).filter { seen.insert($0.id).inserted }
    }

    private static func genre(_ item: [String: Any]) -> ArchiveGenre? {
        guard let title = text(item["title"]) else { return nil }
        let id = text(item["id"]) ?? number(item["id"]).map { String(Int($0)) }
        return id.map { ArchiveGenre(id: $0, title: title) }
    }

    private static func attribution(_ root: [String: Any]) -> String {
        text(root["attribution"]) ?? ProgrammeDetails.defaultAttribution
    }

    private static func page(_ value: Any?) -> Int? {
        number(value).map { Int($0) }.flatMap { $0 > 0 ? $0 : nil }
    }

    private static func object(status: Int, body: Data) throws -> [String: Any] {
        guard (200..<300).contains(status) else { throw DVBParser.error(status: status, body: body) }
        guard let root = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] else {
            throw CTError("Brána Anténa neodpovídá", detail: "archive: body is not a JSON object")
        }
        return root
    }

    private static func array(_ value: Any?) -> [[String: Any]] {
        (value as? [Any] ?? []).compactMap { $0 as? [String: Any] }
    }

    /// A list of strings, or one string ("Česko, Slovensko" stays one entry).
    private static func texts(_ value: Any?) -> [String] {
        if let single = text(value) { return [single] }
        return (value as? [Any] ?? []).compactMap(text)
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

// MARK: - client

extension DVBClient {
    public func archiveHome() async throws -> ArchiveHome {
        let response = try await get("/dvb/archive")
        return try ArchiveParser.home(status: response.status, body: response.body)
    }

    /// The menu's categories (T23).
    public func archiveCategories() async throws -> [ArchiveCategory] {
        let response = try await get("/dvb/archive/categories")
        return try ArchiveParser.categories(status: response.status, body: response.body)
    }

    /// A page of a category (`recordings` = ours only); `genre` nil = all sub-genres.
    public func archiveCategory(_ slug: String, sort: ArchiveSort, genre: String?, page: Int) async throws -> ArchivePage {
        var query = [URLQueryItem(name: "sort", value: sort.rawValue)]
        if let genre { query.append(URLQueryItem(name: "genre", value: genre)) }
        query.append(URLQueryItem(name: "page", value: String(page)))
        let response = try await get(Self.path("/dvb/archive/category/\(escape(slug))", query))
        return try ArchiveParser.page(status: response.status, body: response.body)
    }

    public func archiveSearch(_ text: String, page: Int) async throws -> ArchivePage {
        let query = String(text.prefix(ArchiveRules.searchMaxLength))
        let response = try await get(Self.path("/dvb/archive/search", [URLQueryItem(name: "q", value: query),
                                                                      URLQueryItem(name: "page", value: String(page))]))
        return try ArchiveParser.page(status: response.status, body: response.body)
    }

    public func archiveShow(_ id: String) async throws -> ArchiveShow {
        let response = try await get("/dvb/archive/show/\(escape(id))")
        return try ArchiveParser.show(status: response.status, body: response.body)
    }

    public func archiveSeason(show id: String, season: String, page: Int) async throws -> ArchiveEpisodePage {
        let response = try await get(Self.path("/dvb/archive/show/\(escape(id))", [
            URLQueryItem(name: "season", value: season), URLQueryItem(name: "page", value: String(page)),
        ]))
        return try ArchiveParser.episodePage(status: response.status, body: response.body)
    }

    /// One path segment, percent-encoded (no `/` inside).
    static func escapePath(_ component: String) -> String {
        component.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed.subtracting(["/"])) ?? component
    }

    private static func path(_ path: String, _ query: [URLQueryItem]) -> String {
        var parts = URLComponents()
        parts.percentEncodedPath = path
        parts.queryItems = query
        // `+` would read as a space in a query, `&`/`=` are encoded by URLComponents only partly
        parts.percentEncodedQuery = parts.percentEncodedQuery?
            .replacingOccurrences(of: "+", with: "%2B")
        return parts.string ?? path
    }
}
