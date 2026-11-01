import Foundation

/// The text of a guide row where it has no programme (spec/apps.md, "In the guide (T36 …)"): "Mimo vysílání",
/// or on a ČT row "Program se nepodařilo načíst" when that day's ČT schedule failed. Anténa and Sněmovna rows
/// never take the ČT failure.
public enum GuideFiller {
    public static let offAir = "Mimo vysílání"

    public static func text(source: ChannelPrefs.Source, ctScheduleFailed: Bool) -> String {
        source == .ct && ctScheduleFailed ? CTError.scheduleFailed : offAir
    }
}
