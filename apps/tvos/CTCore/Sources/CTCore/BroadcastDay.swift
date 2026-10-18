import Foundation

extension Calendar {
    /// CT's broadcast days follow Prague time regardless of the device's time zone.
    public static let prague: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Europe/Prague")!
        calendar.locale = Locale(identifier: "cs_CZ")
        return calendar
    }()
}

/// A calendar day in Prague, the unit CT's schedule API works in.
public struct BroadcastDay: Sendable, Hashable, Comparable, CustomStringConvertible {
    public let year: Int
    public let month: Int
    public let day: Int

    static let weekdays = ["ne", "po", "út", "st", "čt", "pá", "so"]  // Calendar weekday 1 = Sunday

    public init(year: Int, month: Int, day: Int) {
        self.year = year
        self.month = month
        self.day = day
    }

    public init(containing date: Date, calendar: Calendar = .prague) {
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        self.init(year: parts.year!, month: parts.month!, day: parts.day!)
    }

    /// Midnight at the start of the day.
    public func startDate(calendar: Calendar = .prague) -> Date {
        calendar.date(from: DateComponents(year: year, month: month, day: day))!
    }

    public func adding(days: Int, calendar: Calendar = .prague) -> BroadcastDay {
        BroadcastDay(containing: calendar.date(byAdding: .day, value: days, to: startDate(calendar: calendar))!, calendar: calendar)
    }

    /// Date format of the GraphQL schedule query: "MM.DD.YYYY".
    public var graphQLDate: String { String(format: "%02d.%02d.%04d", month, day, year) }

    /// "Dnes", "Včera", "Zítra", otherwise e.g. "po 12. 10.".
    public func label(today: BroadcastDay, calendar: Calendar = .prague) -> String {
        switch self {
        case today: return "Dnes"
        case today.adding(days: -1, calendar: calendar): return "Včera"
        case today.adding(days: 1, calendar: calendar): return "Zítra"
        default:
            let weekday = calendar.component(.weekday, from: startDate(calendar: calendar))
            return "\(Self.weekdays[weekday - 1]) \(day). \(month)."
        }
    }

    public var description: String { String(format: "%04d-%02d-%02d", year, month, day) }

    public static func < (lhs: BroadcastDay, rhs: BroadcastDay) -> Bool {
        (lhs.year, lhs.month, lhs.day) < (rhs.year, rhs.month, rhs.day)
    }
}

public enum GuideRange {
    public static let daysBack = 7
    public static let daysAhead = 2

    /// Days offered by the TV guide's day picker, oldest first.
    public static func days(today: BroadcastDay, calendar: Calendar = .prague) -> [BroadcastDay] {
        (-daysBack...daysAhead).map { today.adding(days: $0, calendar: calendar) }
    }
}
