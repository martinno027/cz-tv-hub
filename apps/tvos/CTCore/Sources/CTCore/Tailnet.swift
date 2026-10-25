import Foundation
import Observation

// "Tailscale v aplikaci" (spec/apps.md, T12): the app carries a Tailscale node (apps/tailnet) with a bridge on
// 127.0.0.1 that forwards plain HTTP to the gateway over the tailnet. These are the rules around it (golden cases
// `spec/fixtures/tailnet_cases.json`); the app target owns the node itself.

/// How the node reaches the gateway's peer (`Info().gateway`, T18): directly or through a DERP relay (`relay` = the
/// peer's DERP region code), and the last ping's round trip.
public struct TailnetGatewayPath: Sendable, Equatable, Decodable {
    public let direct: Bool
    public let relay: String
    public let latencyMs: Int?

    public init(direct: Bool, relay: String = "", latencyMs: Int? = nil) {
        self.direct = direct
        self.relay = relay
        self.latencyMs = latencyMs
    }

    private enum CodingKeys: String, CodingKey { case direct, relay, latencyMs }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        direct = try c.decodeIfPresent(Bool.self, forKey: .direct) ?? false
        relay = try c.decodeIfPresent(String.self, forKey: .relay) ?? ""
        latencyMs = try c.decodeIfPresent(Int.self, forKey: .latencyMs)
    }
}

/// The node's `Info()` JSON.
public struct TailnetInfo: Sendable, Equatable, Decodable {
    public let state: String
    public let loginURL: String
    public let selfName: String
    public let ip: String
    public let stateVersion: Int
    /// nil = unknown (not running, or the gateway is not a peer of the tailnet).
    public let gateway: TailnetGatewayPath?

    public init(state: String, loginURL: String = "", selfName: String = "", ip: String = "", stateVersion: Int = 0,
                gateway: TailnetGatewayPath? = nil) {
        self.state = state
        self.loginURL = loginURL
        self.selfName = selfName
        self.ip = ip
        self.stateVersion = stateVersion
        self.gateway = gateway
    }

    private enum CodingKeys: String, CodingKey {
        case state, loginURL, selfName = "self", ip, stateVersion, gateway
    }

    /// Missing fields are empty (the bridge without a node answers just `{"state":"Stopped"}`).
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        state = try c.decodeIfPresent(String.self, forKey: .state) ?? ""
        loginURL = try c.decodeIfPresent(String.self, forKey: .loginURL) ?? ""
        selfName = try c.decodeIfPresent(String.self, forKey: .selfName) ?? ""
        ip = try c.decodeIfPresent(String.self, forKey: .ip) ?? ""
        stateVersion = try c.decodeIfPresent(Int.self, forKey: .stateVersion) ?? 0
        gateway = try? c.decodeIfPresent(TailnetGatewayPath.self, forKey: .gateway)
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
    /// The bridge's own 502 text (apps/tailnet `bridgeError`): the gateway itself was not reached.
    public static let bridgeError = "Brána není přes Tailscale dostupná."

    /// A gateway answer through the bridge that is the bridge's own 502, not the gateway's.
    public static func isBridgeFailure(status: Int, body: Data) -> Bool {
        guard status == 502 else { return false }
        let object = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
        return object?["error"] as? String == bridgeError
    }

    /// "Connection line (T18)" after "Připojeno jako …": "· k bráně přímo, 32 ms", "· přes relé fra, 85 ms"; empty
    /// when the path is unknown (also when neither direct nor through a named relay).
    public static func connectionLine(_ path: TailnetGatewayPath?) -> String {
        guard let path else { return "" }
        let route: String
        if path.direct {
            route = "k bráně přímo"
        } else if !path.relay.isEmpty {
            route = "přes relé \(path.relay)"
        } else {
            return ""
        }
        return path.latencyMs.map { " · \(route), \($0) ms" } ?? " · \(route)"
    }

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
            let line = (info.ip.isEmpty ? "Připojeno jako \(info.selfName)" : "Připojeno jako \(info.selfName) (\(info.ip))")
                + connectionLine(info.gateway)
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

/// A transport to the gateway through the bridge that reports when the bridge itself failed: its own 502, or no
/// answer at all from 127.0.0.1 (a cancelled request is not a failure). The answer is passed on unchanged.
public struct BridgeWatchTransport: HTTPTransport {
    private let inner: HTTPTransport
    private let onFailure: @Sendable () -> Void

    public init(_ inner: HTTPTransport, onFailure: @escaping @Sendable () -> Void) {
        self.inner = inner
        self.onFailure = onFailure
    }

    public func send(_ request: URLRequest) async throws -> HTTPResponse {
        let response: HTTPResponse
        do {
            response = try await inner.send(request)
        } catch {
            if !(error is CancellationError), (error as? URLError)?.code != .cancelled { onFailure() }
            throw error
        }
        if TailnetRules.isBridgeFailure(status: response.status, body: response.body) { onFailure() }
        return response
    }
}

/// "Back in front (T18)": the node's connections can die while the app is suspended and its state still says
/// `Running`. When the app becomes active, or a request through the bridge fails, the bridge is checked
/// (`GET /health` through it, `checkTimeout`); not answered → the node is restarted with its saved state, its status
/// polled every `runningPoll` until `Running` (at most `runningWait`), the bridge checked again and the gateway data
/// reloaded. One check at a time; after a (re)start of the node no check for `quietAfterStart`.
@MainActor
@Observable
public final class TailnetRecovery {
    public static let checkTimeout: TimeInterval = 4
    public static let runningPoll: TimeInterval = 2
    public static let runningWait: TimeInterval = 20
    public static let quietAfterStart: TimeInterval = 30

    public enum Outcome: Sendable, Equatable {
        /// A check runs already, or the node (re)started less than `quietAfterStart` ago.
        case skipped
        /// The bridge answered: nothing to do.
        case healthy
        /// Restarted, and the bridge answers again.
        case recovered
        /// Restarted (or the restart failed), and the bridge still does not answer.
        case failed
    }

    /// What a check does, supplied by the app (the node and the network live there).
    public struct Steps {
        /// `GET /health` through the bridge within `checkTimeout`: true when answered.
        public var check: @MainActor () async -> Bool
        /// Stop + start with the saved state; false when it could not start.
        public var restart: @MainActor () async -> Bool
        /// Reads the node's status: true once `Running`.
        public var isRunning: @MainActor () async -> Bool
        /// Loads the gateway data the screens need again.
        public var reload: @MainActor () async -> Void

        public init(check: @escaping @MainActor () async -> Bool, restart: @escaping @MainActor () async -> Bool,
                    isRunning: @escaping @MainActor () async -> Bool, reload: @escaping @MainActor () async -> Void) {
            self.check = check
            self.restart = restart
            self.isRunning = isRunning
            self.reload = reload
        }
    }

    /// A check (and what follows it) runs: Živě and the guide say "Připojuji se k Tailscale…" instead of hiding Anténa.
    public private(set) var checking = false
    @ObservationIgnored private var lastStart: Date?
    @ObservationIgnored private let clock: @Sendable () -> Date
    @ObservationIgnored private let sleep: @Sendable (TimeInterval) async -> Void

    public init(clock: @escaping @Sendable () -> Date = Date.init,
                sleep: @escaping @Sendable (TimeInterval) async -> Void = { try? await Task.sleep(for: .seconds($0)) }) {
        self.clock = clock
        self.sleep = sleep
    }

    /// The node was started (app start, switch on, new address): it connects by itself first.
    public func nodeStarted() {
        lastStart = clock()
    }

    @discardableResult
    public func run(_ steps: Steps) async -> Outcome {
        if checking { return .skipped }
        if let lastStart, clock().timeIntervalSince(lastStart) < Self.quietAfterStart { return .skipped }
        checking = true
        defer { checking = false }
        if await steps.check() { return .healthy }
        lastStart = clock()
        var answered = false
        if await steps.restart() {
            let deadline = clock().addingTimeInterval(Self.runningWait)
            while !(await steps.isRunning()), clock() < deadline, !Task.isCancelled {
                await sleep(Self.runningPoll)
            }
            answered = await steps.check()
        }
        await steps.reload()
        return answered ? .recovered : .failed
    }
}
