import Foundation

/// What the player's info bar shows (spec/apps.md "Title in the player's info bar"): the programme,
/// with the channel as the second line. `until` = when a live title has to be looked up again.
public struct PlayerTitle: Sendable, Equatable {
    public let title: String
    public let subtitle: String
    public let until: Date?

    public init(title: String, subtitle: String = "", until: Date? = nil) {
        self.title = title
        self.subtitle = subtitle
        self.until = until
    }

    /// Live: the programme airing now (title, end), else the channel name alone.
    public static func live(channel: String, airing: (title: String, end: Date)?) -> PlayerTitle {
        guard let airing, !airing.title.isEmpty else { return PlayerTitle(title: channel) }
        return PlayerTitle(title: airing.title, subtitle: channel, until: airing.end)
    }

    /// Start-over, archive, recordings: the programme; channel (+ episode) below.
    public static func programme(_ title: String, channel: String?, episode: String = "") -> PlayerTitle {
        let parts = [channel ?? "", episode].filter { !$0.isEmpty }
        return PlayerTitle(title: title, subtitle: parts.joined(separator: " · "))
    }

    /// When to look again for a live title: at the programme's end, and at least every minute.
    public func nextCheck(after now: Date) -> Date {
        let minute = now.addingTimeInterval(60)
        guard let until, until > now else { return minute }
        return min(until.addingTimeInterval(1), minute)
    }
}
