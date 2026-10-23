import Foundation

/// Which audio rendition the player prefers ("Audio choice for Anténa", spec/apps.md). Gateway programmes
/// with several broadcast tracks come as a master playlist with Czech marked DEFAULT; AVPlayer's automatic
/// selection would otherwise follow the TV's system language (an English Apple TV would pick English).
public enum AudioPreference {
    public static let defaultLanguage = "cs"

    /// Preferred languages, most wanted first: the track the viewer picked in the audio menu (kept when the
    /// stream is reloaded after a failure), then Czech.
    public static func languages(selected: String? = nil) -> [String] {
        let picked = selected?.trimmingCharacters(in: .whitespaces).lowercased() ?? ""
        guard !picked.isEmpty, picked != defaultLanguage else { return [defaultLanguage] }
        return [picked, defaultLanguage]
    }
}
