import Foundation

/// Days of the TV guide (spec/apps.md "TV průvodce", golden cases `spec/fixtures/guide_days_cases.json`).
/// A grid shows one broadcast day, 05:00–05:00 Prague; the day strip speaks calendar days.
public enum GuideDays {
    /// Where a grid opens: at now, or scrolled to a time.
    public enum Anchor: Sendable, Equatable {
        case now
        case time(Date)
    }

    public static let broadcastDayStartHour = 5
    public static let eveningHour = 20

    /// The grid's span for `day`: local 05:00 to the next day's 05:00 (also on DST days).
    public static func bounds(of day: BroadcastDay, calendar: Calendar = .prague) -> (start: Date, end: Date) {
        func five(_ day: BroadcastDay) -> Date {
            let midnight = day.startDate(calendar: calendar)
            return calendar.date(bySettingHour: broadcastDayStartHour, minute: 0, second: 0, of: midnight) ?? midnight
        }
        return (five(day), five(day.adding(days: 1, calendar: calendar)))
    }

    /// The broadcast day whose grid contains `now` (before 05:00 that is the previous calendar day).
    public static func openDay(now: Date, calendar: Calendar = .prague) -> BroadcastDay {
        let day = BroadcastDay(containing: now, calendar: calendar)
        return now < bounds(of: day, calendar: calendar).start ? day.adding(days: -1, calendar: calendar) : day
    }

    /// The chip the day strip highlights for a programme starting at `time`: its calendar day.
    public static func chipDay(_ time: Date, calendar: Calendar = .prague) -> BroadcastDay {
        BroadcastDay(containing: time, calendar: calendar)
    }

    /// The chip to highlight: `chipDay` of the focused programme's start, or the grid's day when that calendar day
    /// has no chip (cells after midnight on the range's last day).
    public static func highlightedChip(focusStart: Date, gridDay: BroadcastDay, chips: [BroadcastDay],
                                       calendar: Calendar = .prague) -> BroadcastDay {
        let day = chipDay(focusStart, calendar: calendar)
        return chips.contains(day) ? day : gridDay
    }

    /// Selecting a chip: "Dnes" opens the grid containing now at now, any other day that day at 20:00.
    public static func select(chip: BroadcastDay, now: Date, calendar: Calendar = .prague) -> (day: BroadcastDay, anchor: Anchor) {
        if chip == BroadcastDay(containing: now, calendar: calendar) {
            return (openDay(now: now, calendar: calendar), .now)
        }
        return (chip, .time(evening(of: chip, calendar: calendar)))
    }

    /// 20:00 Prague on `day`, where a grid other than now's opens.
    public static func evening(of day: BroadcastDay, calendar: Calendar = .prague) -> Date {
        let midnight = day.startDate(calendar: calendar)
        return calendar.date(bySettingHour: eveningHour, minute: 0, second: 0, of: midnight) ?? midnight
    }
}
