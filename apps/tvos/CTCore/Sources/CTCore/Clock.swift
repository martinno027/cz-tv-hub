import Foundation

/// The clock top right on Živě, the guide and Nastavení (spec/apps.md "Clock (T22)").
public enum Clock {
    /// Prague time "H:MM", 24 h, no leading zero on the hour, whatever the device's time zone.
    public static func text(_ date: Date, calendar: Calendar = .prague) -> String {
        let parts = calendar.dateComponents([.hour, .minute], from: date)
        return "\(parts.hour!):" + String(format: "%02d", parts.minute!)
    }

    /// The next minute boundary after `date`: when the text changes next.
    public static func nextMinute(after date: Date) -> Date {
        // Prague offsets are whole hours, so the boundary is the same in UTC.
        Date(timeIntervalSince1970: (date.timeIntervalSince1970 / 60).rounded(.down) * 60 + 60)
    }
}
