import Foundation

// "Tailscale v aplikaci" (spec/apps.md, T12): the app carries a Tailscale node (apps/tailnet) with a bridge on
// 127.0.0.1 that forwards plain HTTP to the gateway over the tailnet. These are the rules around it (golden cases
// `spec/fixtures/tailnet_cases.json`); the app target owns the node itself.

/// The node's `Info()` JSON.
public struct TailnetInfo: Sendable, Equatable, Decodable {
    public let state: String
    public let loginURL: String
    public let selfName: String
    public let ip: String
    public let stateVersion: Int

    public init(state: String, loginURL: String = "", selfName: String = "", ip: String = "", stateVersion: Int = 0) {
        self.state = state
        self.loginURL = loginURL
        self.selfName = selfName
        self.ip = ip
        self.stateVersion = stateVersion
    }

    private enum CodingKeys: String, CodingKey {
        case state, loginURL, selfName = "self", ip, stateVersion
    }

    /// Missing fields are empty (the bridge without a node answers just `{"state":"Stopped"}`).
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        state = try c.decodeIfPresent(String.self, forKey: .state) ?? ""
        loginURL = try c.decodeIfPresent(String.self, forKey: .loginURL) ?? ""
        selfName = try c.decodeIfPresent(String.self, forKey: .selfName) ?? ""
        ip = try c.decodeIfPresent(String.self, forKey: .ip) ?? ""
        stateVersion = try c.decodeIfPresent(Int.self, forKey: .stateVersion) ?? 0
    }

    public static func parse(_ json: String) -> TailnetInfo? {
        try? JSONDecoder().decode(TailnetInfo.self, from: Data(json.utf8))
    }
}

/// What Nastavení → Server shows for the node.
public struct TailnetStatus: Sendable, Equatable {
    public let line: String
    /// The QR code of `loginURL` is shown.
    public let qr: Bool
    /// "Odhlásit z Tailscale" is offered.
    public let logout: Bool
    /// The app talks to the gateway through `http://127.0.0.1:<port>`.
    public let usesBridge: Bool
}

public enum TailnetRules {
    public static let switchLabel = "Připojovat přes Tailscale"
    public static let hint = "Mimo domov: zadejte jako adresu jméno Raspberry v Tailscale (např. raspberrypi)."
    public static let offLine = "Vypnuto"
    public static let connectingLine = "Připojuji se k Tailscale…"
    public static let loginLine = "Naskenujte QR kód telefonem a přihlaste se do Tailscale."
    public static let approvalLine = "Televize čeká na schválení v administraci Tailscale."
    /// The start error when the switch is on but there is no gateway address: the node is not started.
    public static let missingAddress = "chybí adresa brány."
    /// "Uložit a ověřit" after a restart: the check waits this long for `Running` (else it would get the bridge's 502).
    public static let checkWait: TimeInterval = 20
    public static let hostname = "televize-appletv"
    /// Status polling: while Nastavení → Server is open, else until `Running`.
    public static let settingsPollInterval: TimeInterval = 2
    public static let backgroundPollInterval: TimeInterval = 30

    /// The gateway address as typed → the `host:port` the bridge dials (the gateway address rules: default port
    /// 8098, scheme and path dropped); nil when unusable.
    public static func target(address: String) -> String? {
        guard let url = GatewayConfig.baseURL(address: address), let host = url.host(), let port = url.port else {
            return nil
        }
        return host.contains(":") ? "[\(host)]:\(port)" : "\(host):\(port)"
    }

    /// The switch, the node's `Info()` (nil = no node) and the start error → the settings' status.
    public static func status(enabled: Bool, info: TailnetInfo?, error: String?) -> TailnetStatus {
        guard enabled else { return TailnetStatus(line: offLine, qr: false, logout: false, usesBridge: false) }
        guard let info else {
            let line = error.map { "Tailscale se nepodařilo spustit: \($0)" } ?? connectingLine
            return TailnetStatus(line: line, qr: false, logout: false, usesBridge: false)
        }
        // Offered when the node has an identity, whatever its state.
        let logout = !info.selfName.isEmpty
        switch info.state {
        case "Running":
            let line = info.ip.isEmpty ? "Připojeno jako \(info.selfName)" : "Připojeno jako \(info.selfName) (\(info.ip))"
            return TailnetStatus(line: line, qr: false, logout: logout, usesBridge: true)
        case "NeedsLogin" where !info.loginURL.isEmpty:
            return TailnetStatus(line: loginLine, qr: true, logout: logout, usesBridge: true)
        case "NeedsMachineAuth":
            return TailnetStatus(line: approvalLine, qr: false, logout: logout, usesBridge: true)
        default:
            return TailnetStatus(line: connectingLine, qr: false, logout: logout, usesBridge: true)
        }
    }

    /// The bridge's `QRMatrix` (rows of `1`/`0`, quiet zone included) → dark modules; nil when empty or not square.
    public static func qrModules(_ matrix: String) -> [[Bool]]? {
        let rows = matrix.split(whereSeparator: \.isNewline).map { $0.map { $0 == "1" } }
        guard !rows.isEmpty, rows.allSatisfy({ $0.count == rows.count }) else { return nil }
        return rows
    }
}
