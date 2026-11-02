import Foundation

/// The year/month pre-selection of a long ČT episode list (T32, spec/dvb-gateway.md "Archive" → "Year and month",
/// spec/apps.md "Archiv" → show page): the gateway sends it only when the list has more than 100 episodes.
/// A year of at most 100 episodes is shown whole (T51): `months` empty and `month` nil, no month row.
public struct ArchivePeriods: Sendable, Hashable {
    static let yearRange = 1900...2999

    /// Newest first, as the gateway lists them.
    public let years: [Int]
    public let year: Int
    /// The months of `year` with an episode (1–12), newest first; empty = the year is shown whole (T51).
    public let months: [Int]
    /// The month listed; nil exactly when `months` is empty (the whole year, newest first).
    public let month: Int?

    public init(years: [Int], year: Int, months: [Int], month: Int?) {
        self.years = years
        self.year = year
        self.months = months
        self.month = month
    }
}

/// One chip of the year or month row.
public struct ArchivePeriodChip: Sendable, Hashable, Identifiable {
    /// The year (2026) or the month (1–12).
    public let value: Int
    public let label: String
    public let selected: Bool

    public var id: Int { value }
}

public enum ArchivePeriodRules {
    private static let monthNames = ["leden", "únor", "březen", "duben", "květen", "červen", "červenec", "srpen",
                                     "září", "říjen", "listopad", "prosinec"]

    /// 1–12 → the Czech nominative in lower case ("listopad"); anything else nil.
    public static func monthName(_ month: Int) -> String? {
        (1...12).contains(month) ? monthNames[month - 1] : nil
    }

    /// The year chips and the month chips in the gateway's order (newest first), the chosen ones selected;
    /// no periods = no chips.
    public static func chips(_ periods: ArchivePeriods?) -> (years: [ArchivePeriodChip], months: [ArchivePeriodChip]) {
        guard let periods else { return ([], []) }
        let years = periods.years.map { ArchivePeriodChip(value: $0, label: String($0), selected: $0 == periods.year) }
        let months = periods.months.compactMap { month in
            monthName(month).map { ArchivePeriodChip(value: month, label: $0, selected: month == periods.month) }
        }
        return (years, months)
    }

    /// A year alone shows its newest month. The gateway picks it for `?year=` without `month`; this is only the
    /// fallback when its answer names no month or one not in its list. No months (a year shown whole, T51) = nil.
    public static func selectYear(months: [Int]) -> Int? {
        months.filter { (1...12).contains($0) }.max()
    }
}

/// What the show page asks the gateway for: the season (nil = the gateway's default), the year and month
/// (nil = the default: the newest; a year alone = its newest month).
public struct ArchiveEpisodeQuery: Sendable, Hashable {
    public var season: String?
    public var year: Int?
    public var month: Int?

    public init(season: String? = nil, year: Int? = nil, month: Int? = nil) {
        self.season = season
        self.year = year
        self.month = month
    }

    /// Another season starts at its own newest month: its years differ, a kept one could be outside its list.
    public func selectingSeason(_ id: String?) -> ArchiveEpisodeQuery {
        ArchiveEpisodeQuery(season: id)
    }

    /// A year alone: the gateway answers with its newest month.
    public func selectingYear(_ year: Int) -> ArchiveEpisodeQuery {
        ArchiveEpisodeQuery(season: season, year: year)
    }

    /// A month of the year the gateway showed.
    public func selectingMonth(_ month: Int, of periods: ArchivePeriods) -> ArchiveEpisodeQuery {
        ArchiveEpisodeQuery(season: season, year: periods.year, month: month)
    }

    /// The next page of what is listed: the year and month the gateway answered with, so a page 2 cannot drift
    /// to another month.
    public func paging(_ periods: ArchivePeriods?) -> ArchiveEpisodeQuery {
        guard let periods else { return self }
        return ArchiveEpisodeQuery(season: season, year: periods.year, month: periods.month)
    }

    public func queryItems(page: Int) -> [URLQueryItem] {
        var items: [URLQueryItem] = []
        if let season { items.append(URLQueryItem(name: "season", value: season)) }
        if let year { items.append(URLQueryItem(name: "year", value: String(year))) }
        // a month only with its year (the gateway defines no month alone)
        if year != nil, let month { items.append(URLQueryItem(name: "month", value: String(month))) }
        items.append(URLQueryItem(name: "page", value: String(page)))
        return items
    }
}
