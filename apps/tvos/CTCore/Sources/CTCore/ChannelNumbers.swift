import Foundation

/// Channel numbers and zapping (spec/apps.md "Channel numbers and zapping"): a channel's number is its
/// 1-based position in the visible list of "Kanály"; ▲/▼ steps through that list, digits pick by number.
public enum ChannelNumbers {
    /// The new stream is resolved only after this long without another zap press.
    public static let zapDelay: Duration = .milliseconds(600)
    /// A digit buffer that could still grow is used after this long without another digit.
    public static let entryTimeout: Duration = .seconds(2)
    /// How long the "<number>  <channel>" box stays on screen.
    public static let overlayDuration: Duration = .seconds(3)

    /// 1-based number of `key` in `visible`, nil for a channel that is not shown.
    public static func number(of key: String, in visible: [String]) -> Int? {
        visible.firstIndex(of: key).map { $0 + 1 }
    }

    /// The neighbour `by` (±1) positions away, wrapping at both ends. A channel not in the list steps to the
    /// first (next) or last (previous) one; an empty list has none.
    public static func step(_ visible: [String], from current: String?, by delta: Int) -> String? {
        guard !visible.isEmpty else { return nil }
        guard let current, let index = visible.firstIndex(of: current) else {
            return delta >= 0 ? visible.first : visible.last
        }
        let count = visible.count
        return visible[((index + delta) % count + count) % count]
    }

    /// One digit into the buffer. `commit` = use the buffer now: no further digit could give a valid number
    /// (`buffer × 10 > count`) or it has 3 digits. A leading 0 is ignored.
    public static func type(buffer: String, digit: Int, count: Int) -> (buffer: String, commit: Bool) {
        if buffer.isEmpty && digit == 0 { return ("", false) }
        let next = buffer + String(digit)
        let value = Int(next) ?? 0
        return (next, value * 10 > count || next.count >= 3)
    }

    /// The channel with `number`, nil outside 1…count.
    public static func resolve(_ visible: [String], number: Int) -> String? {
        visible.indices.contains(number - 1) ? visible[number - 1] : nil
    }

    /// Message for a number that has no channel.
    public static func missingMessage(_ number: String) -> String { "Kanál \(number) není" }
}
