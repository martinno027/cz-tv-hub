import CTCore
import Foundation
import Observation
import Tailnet

/// The Tailscale bridge's C API (apps/tailnet/cshim): the only place calling it, every call on one serial queue off
/// the main thread (`TailnetInfo` can take up to 3 s, `TailnetLogout` up to 10 s).
private enum Bridge {
    private static let queue = DispatchQueue(label: "cz.cztvhub.tv.tailnet")

    static func run<T: Sendable>(_ body: @escaping @Sendable () -> T) async -> T {
        await withCheckedContinuation { continuation in
            queue.async { continuation.resume(returning: body()) }
        }
    }

    /// Strings returned by the library are malloc'ed; the caller frees them.
    private static func take(_ pointer: UnsafeMutablePointer<CChar>?) -> String {
        guard let pointer else { return "" }
        defer { free(pointer) }
        return String(cString: pointer)
    }

    /// The bridge's port on 127.0.0.1, or the start error.
    static func start(workDir: String, savedState: String, authKey: String, target: String) -> Result<Int, StartError> {
        let port = workDir.withCString { dir in
            savedState.withCString { state in
                TailnetRules.hostname.withCString { host in
                    authKey.withCString { key in
                        target.withCString { target in
                            TailnetStart(UnsafeMutablePointer(mutating: dir), UnsafeMutablePointer(mutating: state),
                                         UnsafeMutablePointer(mutating: host), UnsafeMutablePointer(mutating: key),
                                         UnsafeMutablePointer(mutating: target))
                        }
                    }
                }
            }
        }
        return port < 0 ? .failure(StartError(message: take(TailnetLastError()))) : .success(Int(port))
    }

    static func info() -> TailnetInfo? { TailnetInfo.parse(take(TailnetInfo())) }

    static func savedState() -> String { take(TailnetSavedState()) }

    /// Stop + start with the node's current state on the same port (T18): the port, or the error.
    static func restart() -> Result<Int, StartError> {
        let port = TailnetRestart()
        return port < 0 ? .failure(StartError(message: take(TailnetLastError()))) : .success(Int(port))
    }

    /// nil on success, else the error.
    static func logout() -> String? { TailnetLogout() == 0 ? nil : take(TailnetLastError()) }

    static func qr(_ text: String) -> [[Bool]]? {
        text.withCString { TailnetRules.qrModules(take(TailnetQR(UnsafeMutablePointer(mutating: $0)))) }
    }

    static func stop() { TailnetStop() }

    struct StartError: Error, Sendable { let message: String }
}

/// "Tailscale v aplikaci" (spec/apps.md): a tailnet node inside the app with a bridge on 127.0.0.1 to the gateway.
/// The node's state (its keys) lives only in the Keychain; the work directory holds Tailscale's caches.
@MainActor
@Observable
final class TailnetController {
    private static let switchKey = "tailnetEnabled"
    private static let stateItem = KeychainItem<String>(service: "cz.cztvhub.tv.tailnet")

    /// "Připojovat přes Tailscale" (default off).
    private(set) var enabled: Bool
    private(set) var info: TailnetInfo?
    private(set) var startError: String?
    /// The login URL's QR modules (quiet zone included) while `status.qr`.
    private(set) var qrModules: [[Bool]]?
    /// Nastavení → Server is on screen: status every 2 s.
    var settingsOpen = false {
        didSet { if settingsOpen != oldValue { schedulePolling(now: settingsOpen) } }
    }

    var status: TailnetStatus { TailnetRules.status(enabled: enabled, info: info, error: startError) }

    /// "Back in front (T18)": checks of the bridge and restarts of the node.
    let recovery = TailnetRecovery()
    /// The bridge is being checked (or the node restarted), or the node is not `Running` yet: Živě and the guide say
    /// "Připojuji se k Tailscale…".
    var connecting: Bool { TailnetRules.connecting(enabled: enabled, info: info, checking: recovery.checking) }
    /// After a restart, and whenever the node's state changes to `Running` (spec/apps.md "Start (T21)"): load the
    /// gateway data the screens need again.
    @ObservationIgnored var onRecovered: () async -> Void = {}

    /// `http://127.0.0.1:<port>` while the app talks to the gateway through the bridge.
    var bridgeURL: URL? {
        guard status.usesBridge, let port else { return nil }
        return URL(string: "http://127.0.0.1:\(port)")
    }

    /// The bridge appeared, moved or went away: the app's gateway base URL changes.
    @ObservationIgnored var onBridgeChange: () -> Void = {}

    @ObservationIgnored private var port: Int?
    @ObservationIgnored private var target: String?
    /// Bumped on every start and stop; an answer for an older node is dropped.
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var savedVersion = 0
    @ObservationIgnored private var qrURL = ""
    @ObservationIgnored private var polling: Task<Void, Never>?

    init() {
        enabled = UserDefaults.standard.bool(forKey: Self.switchKey)
    }

    private static var workDir: String {
        let dir = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("tailnet")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.path
    }

    /// The switch. On: starts the node to `target` (nil = no gateway address yet); off: stops it, the saved state is kept.
    func setEnabled(_ value: Bool, target: String?) async {
        guard value != enabled else { return }
        enabled = value
        UserDefaults.standard.set(value, forKey: Self.switchKey)
        if value {
            await start(target: target)
        } else {
            await stop()
        }
    }

    /// Starts (or restarts) the node with the bridge to `target`; does nothing when it already runs to that target and
    /// there is no auth key to use. The key is used once and never stored. Returns whether the node was (re)started.
    @discardableResult
    func start(target: String?, authKey: String = "") async -> Bool {
        guard enabled else { return false }
        guard let target else {
            await stop()
            startError = TailnetRules.missingAddress
            return false
        }
        if target == self.target, port != nil, authKey.isEmpty { return false }
        generation += 1
        let current = generation
        let saved = Self.stateItem.load() ?? ""
        let workDir = Self.workDir
        let result = await Bridge.run { () -> (Result<Int, Bridge.StartError>, TailnetInfo?) in
            let result = Bridge.start(workDir: workDir, savedState: saved, authKey: authKey, target: target)
            return (result, (try? result.get()) == nil ? nil : Bridge.info())
        }
        guard current == generation else { return false }
        savedVersion = 0  // the node counts its state writes from its start
        switch result.0 {
        case .success(let port):
            log.info("tailnet: bridge on port \(port) to \(target, privacy: .public)")
            self.port = port
            self.target = target
            startError = nil
            recovery.nodeStarted()
            // the gateway is applied right after this (onBridgeChange): that is the first load
            await apply(result.1, reload: false)
        case .failure(let error):
            log.error("tailnet: start failed: \(error.message, privacy: .public)")
            port = nil
            self.target = nil
            info = nil
            startError = error.message
        }
        onBridgeChange()
        schedulePolling()
        return true
    }

    /// After a restart: waits up to `TailnetRules.checkWait` for `Running`, so a check through the bridge does not get
    /// its 502 just because the node is still connecting.
    func waitUntilRunning() async {
        let deadline = Date().addingTimeInterval(TailnetRules.checkWait)
        while port != nil, info?.state != "Running", Date() < deadline, !Task.isCancelled {
            try? await Task.sleep(for: .milliseconds(500))
            await refresh()
        }
    }

    /// "Back in front (T18)": the app became active, or a request through the bridge got no answer from the gateway.
    /// The bridge is checked; not answered → the node restarts with its saved state and the gateway data is reloaded.
    func checkBridge() async {
        guard enabled, bridgeURL != nil else { return }
        let outcome = await recovery.run(TailnetRecovery.Steps(
            check: { [weak self] in
                guard let url = self?.bridgeURL else { return false }
                return await Self.bridgeAnswers(url)
            },
            restart: { [weak self] in await self?.restartNode() ?? false },
            isRunning: { [weak self] in
                guard let self else { return false }
                await refresh()
                return info?.state == "Running"
            },
            reload: { [weak self] in await self?.onRecovered() }
        ))
        if outcome != .healthy, outcome != .skipped {
            log.info("tailnet: bridge check: \(String(describing: outcome), privacy: .public)")
        }
    }

    /// No URLCache, no cookies: only the status matters.
    private static let healthSession: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil
        configuration.timeoutIntervalForRequest = TailnetRecovery.checkTimeout
        return URLSession(configuration: configuration)
    }()

    /// `GET /health` through the bridge (the gateway's health, no key) answered within `checkTimeout`.
    private static func bridgeAnswers(_ base: URL) async -> Bool {
        let request = URLRequest(url: base.appending(path: "health"), cachePolicy: .reloadIgnoringLocalCacheData,
                                 timeoutInterval: TailnetRecovery.checkTimeout)
        guard let (_, response) = try? await healthSession.data(for: request) else { return false }
        return (response as? HTTPURLResponse)?.statusCode == 200
    }

    /// The node again with its state, on the same bridge port (a player's URLs stay valid).
    private func restartNode() async -> Bool {
        generation += 1
        let current = generation
        let result = await Bridge.run { Bridge.restart() }
        guard current == generation else { return false }
        switch result {
        case .success(let port):
            log.info("tailnet: restarted, bridge on port \(port)")
            if port != self.port {
                self.port = port
                onBridgeChange()
            }
            schedulePolling()
            return true
        case .failure(let error):
            log.error("tailnet: restart failed: \(error.message, privacy: .public)")
            return false
        }
    }

    func stop() async {
        generation += 1
        polling?.cancel()
        let hadNode = port != nil
        await refresh(reload: false)
        await Bridge.run { Bridge.stop() }
        port = nil
        target = nil
        info = nil
        startError = nil
        qrModules = nil
        qrURL = ""
        if hadNode { onBridgeChange() }
    }

    /// "Odhlásit z Tailscale": logs the node out, deletes the saved state, turns the switch off.
    func logout() async {
        polling?.cancel()
        if let error = await Bridge.run({ Bridge.logout() }) {
            log.error("tailnet: logout failed: \(error, privacy: .public)")
        }
        await stop()
        Self.stateItem.delete()
        savedVersion = 0
        enabled = false
        UserDefaults.standard.set(false, forKey: Self.switchKey)
    }

    /// Reads the node's status and keeps its newest state in the Keychain (also when the app goes to the background).
    /// A change to `Running` reloads the gateway data (`reload: false` when the node is about to stop).
    func refresh(reload: Bool = true) async {
        guard port != nil else { return }
        let current = generation
        let info = await Bridge.run { Bridge.info() }
        guard current == generation, let info else { return }
        await apply(info, reload: reload)
    }

    private func apply(_ info: TailnetInfo?, reload: Bool = true) async {
        let bridge = bridgeURL
        self.info = info
        guard let info else { return }
        if reload {
            // the bridge comes into use with the first status of a node that had none at its start
            if bridgeURL != bridge { onBridgeChange() }
            if recovery.stateRead(info.state) {
                log.info("tailnet: Running, gateway data reloaded")
                Task { await onRecovered() }
            }
        } else {
            recovery.stateRead(info.state, reload: false)
        }
        if info.stateVersion != savedVersion {
            let current = generation
            let state = await Bridge.run { Bridge.savedState() }
            guard current == generation else { return }
            if !state.isEmpty, Self.stateItem.save(state) { savedVersion = info.stateVersion }
        }
        if status.qr, info.loginURL != qrURL {
            let url = info.loginURL
            let modules = await Bridge.run { Bridge.qr(url) }
            qrURL = url
            qrModules = modules
        } else if !status.qr {
            qrURL = ""
            qrModules = nil
        }
    }

    /// Every 2 s while Nastavení → Server is open, every 1 s for 20 s after a node start, else every 30 s until
    /// `Running` (`TailnetRecovery.pollInterval`). `now`: the settings just opened, show the current state at once.
    private func schedulePolling(now: Bool = false) {
        polling?.cancel()
        guard enabled, port != nil else { return }
        polling = Task { [weak self] in
            if now { await self?.refresh() }
            while !Task.isCancelled {
                guard let self, let interval = self.recovery.pollInterval(settingsOpen: self.settingsOpen) else { return }
                try? await Task.sleep(for: .seconds(interval))
                if Task.isCancelled { return }
                await self.refresh()
            }
        }
    }
}

#if DEBUG
extension TailnetController {
    /// `-tailnetInfo '{"state":"Running",…}'` shows that node status in Nastavení → Server without starting a node
    /// (simulator screenshots; nothing is saved).
    func debugShow(_ json: String) {
        enabled = true
        info = TailnetInfo.parse(json)
    }

    /// `-tailnetFakeRunningAfter 8` with `-gatewayAddress 127.0.0.1:<port>`: a cold start without a tailnet
    /// (spec/apps.md "Start (T21)"). A fake node "starts" with the dev gateway as its bridge, says `Starting` for that
    /// many seconds and then `Running`; until then gateway requests get the bridge's 502 (`debugBridgeDown`).
    func debugFakeStart(address: String, runningAfter: TimeInterval) {
        guard let target = TailnetRules.target(address: address), let url = GatewayConfig.baseURL(address: address),
              url.host() == "127.0.0.1", let port = url.port else { return }
        enabled = true
        self.port = port
        self.target = target
        recovery.nodeStarted()
        Task {
            await apply(TailnetInfo(state: "Starting"), reload: false)
            try? await Task.sleep(for: .seconds(runningAfter))
            log.info("tailnet: fake node Running")
            await apply(TailnetInfo(state: "Running", selfName: TailnetRules.hostname, ip: "100.64.0.9"))
        }
    }

    /// The fake node of `debugFakeStart` is not `Running` yet.
    var debugBridgeDown: Bool { target != nil && info?.state != "Running" && info?.state != nil }
}

/// Answers like the bridge while `down()`: its own 502 (spec/apps.md "Tailscale v aplikaci").
struct DebugStartingBridgeTransport: HTTPTransport {
    let inner: HTTPTransport
    let down: @MainActor @Sendable () -> Bool

    func send(_ request: URLRequest) async throws -> HTTPResponse {
        if await down() {
            let body = try JSONSerialization.data(withJSONObject: ["error": TailnetRules.bridgeError])
            return HTTPResponse(status: 502, body: body)
        }
        return try await inner.send(request)
    }
}
#endif
