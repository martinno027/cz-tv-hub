import Foundation
import Network
import Observation

/// Finds cz-tv-hub gateways advertised as `_cztvhub._tcp` on the home LAN (mDNS is LAN-only;
/// over Tailscale the address is typed in settings).
@MainActor
@Observable
final class GatewayBrowser {
    struct Service: Identifiable, Hashable {
        let name: String
        let endpoint: NWEndpoint
        var id: String { name }
    }

    private(set) var services: [Service] = []
    private var browser: NWBrowser?

    func start() {
        guard browser == nil else { return }
        let browser = NWBrowser(for: .bonjour(type: "_cztvhub._tcp", domain: nil), using: .tcp)
        browser.browseResultsChangedHandler = { [weak self] results, _ in
            let found = results.compactMap { result -> Service? in
                guard case .service(let name, _, _, _) = result.endpoint else { return nil }
                return Service(name: name, endpoint: result.endpoint)
            }
            log.info("gateway browser found \(results.count) result(s)")
            // De-duplicate: the same service shows up once per interface.
            let unique = Dictionary(found.map { ($0.name, $0) }, uniquingKeysWith: { first, _ in first }).values
            Task { @MainActor in self?.services = unique.sorted { $0.name < $1.name } }
        }
        browser.stateUpdateHandler = { state in
            log.info("gateway browser \(String(describing: state), privacy: .public)")
        }
        browser.start(queue: .main)
        self.browser = browser
    }

    func stop() {
        browser?.cancel()
        browser = nil
    }

    /// "host:port" of a discovered service (IPv4 preferred), or nil if it cannot be reached.
    func resolve(_ service: Service) async -> String? {
        let parameters = NWParameters.tcp
        (parameters.defaultProtocolStack.internetProtocol as? NWProtocolIP.Options)?.version = .v4
        let connection = NWConnection(to: service.endpoint, using: parameters)
        return await withCheckedContinuation { continuation in
            let once = Once()
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    var address: String?
                    if case .hostPort(let host, let port)? = connection.currentPath?.remoteEndpoint {
                        address = "\(Self.describe(host)):\(port.rawValue)"
                    }
                    once.run { continuation.resume(returning: address) }
                    connection.cancel()
                case .failed, .cancelled:
                    once.run { continuation.resume(returning: nil) }
                default:
                    break
                }
            }
            connection.start(queue: .main)
            DispatchQueue.main.asyncAfter(deadline: .now() + 5) {
                once.run { continuation.resume(returning: nil) }
                connection.cancel()
            }
        }
    }

    private nonisolated static func describe(_ host: NWEndpoint.Host) -> String {
        switch host {
        case .ipv4(let address): return "\(address)"
        case .ipv6(let address): return "[\(address)]"
        case .name(let name, _): return name
        @unknown default: return "\(host)"
        }
    }

    private final class Once: @unchecked Sendable {
        private let lock = NSLock()
        private var done = false

        func run(_ body: () -> Void) {
            lock.lock()
            defer { lock.unlock() }
            guard !done else { return }
            done = true
            body()
        }
    }
}
