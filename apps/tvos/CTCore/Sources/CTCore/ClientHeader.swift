import Foundation

/// `X-Cztvhub-Client` for the gateway's sign-in log (spec/dvb-gateway.md "Sign-in log (T37)"): which app and version
/// connected. Sent only on the profile routes, `GET /dvb/status` and the watch reports (`POST /dvb/watch`, T53), never
/// on other gateway requests (never on playback), never to ČT or the Chamber's CDN.
public enum ClientHeader {
    public static let field = "X-Cztvhub-Client"
    public static let platform = "tvos"
    /// The gateway ignores a longer value or one with other characters.
    public static let maxLength = 64
    static let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ./()+_-")

    /// "Připojení a přihlášení se zaznamenává …" under „Přihlásit se"/„Vytvořit profil" and in Nastavení → Server
    /// (spec/apps.md "Sign-in note (T37)").
    public static let note = "Připojení a přihlášení se zaznamenává (kdy, odkud a z jakého zařízení). "
        + "Záznam vidí jen správce a po 90 dnech se smaže."

    /// "tvos/1.0 (123)"; characters the gateway would refuse (and parentheses inside the parts) are dropped, so a
    /// build "?" leaves "tvos/1.0"; cut to 64 characters.
    public static func value(version: String, build: String) -> String {
        let version = clean(version), build = clean(build)
        var text = version.isEmpty ? platform : "\(platform)/\(version)"
        if !build.isEmpty { text += " (\(build))" }
        return String(text.prefix(maxLength))
    }

    /// The header goes on this request: `GET /dvb/status`, `POST /dvb/watch` (spec/dvb-gateway.md "Watch log (T53)":
    /// the device of a watch report as T37), and any method on `/dvb/profile`, `/dvb/profile/…`, `/dvb/profiles`,
    /// `/dvb/profiles/…` of the gateway.
    public static func applies(method: String, path: String) -> Bool {
        if path == "/dvb/status" { return method.uppercased() == "GET" }
        if path == WatchRules.path { return method.uppercased() == "POST" }
        for root in ["/dvb/profile", "/dvb/profiles"] where path == root || path.hasPrefix(root + "/") {
            return true
        }
        return false
    }

    private static func clean(_ text: String) -> String {
        String(String.UnicodeScalarView(text.unicodeScalars.filter { allowed.contains($0) && $0 != "(" && $0 != ")" }))
            .trimmingCharacters(in: .whitespaces)
    }
}
