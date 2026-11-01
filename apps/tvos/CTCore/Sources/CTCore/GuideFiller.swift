import Foundation

/// The text of a guide row where it has no programme (spec/apps.md, "In the guide (T36 …)"): "Mimo vysílání",
/// or on a ČT row "Program se nepodařilo načíst" when that day's ČT schedule failed. Anténa and Sněmovna rows
/// never take the ČT failure.
public enum GuideFiller {
    public static let offAir = "Mimo vysílání"

    public static func text(source: ChannelPrefs.Source, ctScheduleFailed: Bool) -> String {
        source == .ct && ctScheduleFailed ? CTError.scheduleFailed : offAir
    }

    /// Selecting a filler (spec/apps.md "Selecting a filler that covers now", T38): the channel plays live, as its
    /// Živě card does, when the cell is a gap of a ČT or Anténa row that covers `now`, or any gap of a ČT row whose
    /// schedule failed on the grid's day when that day contains `now` (gaps are cut every 30 minutes: one cell
    /// under the now line is too easy to miss). Other gaps (past or future on a loaded row, another day) and
    /// Sněmovna rows: nothing.
    public static func playsLive<Item>(source: ChannelPrefs.Source, ctScheduleFailed: Bool, segment index: Int,
                                       in segments: [GuideTimeline.Segment<Item>], timeline: GuideTimeline,
                                       now: Date) -> Bool {
        guard source == .ct || source == .dvb, segments.indices.contains(index),
              case .gap = segments[index], let range = timeline.timeRange(ofSegment: index, in: segments)
        else { return false }
        if source == .ct, ctScheduleFailed, timeline.contains(now) { return true }
        return range.start <= now && now < range.end
    }
}
