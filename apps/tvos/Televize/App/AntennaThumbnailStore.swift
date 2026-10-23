import CTCore
import Foundation
import Observation
import UIKit

/// "Anténa thumbnails" (spec/apps.md, spec/dvb-gateway.md "Thumbnails", approved 2026-10-23): one still per
/// recording from the gateway, kept in memory only (ephemeral session without URLCache, small LRU here).
@MainActor
@Observable
final class AntennaThumbnailStore {
    static let tvSwitchKey = "antennaThumbnails"
    private static let capacity = 30
    private static let growingMaxAge: TimeInterval = 60
    private static let statusMaxAge: TimeInterval = 5 * 60

    /// The household allows thumbnails (`/dvb/status`); refreshed at most every 5 minutes.
    private(set) var gatewayAllows = false
    /// This TV's switch "Náhledy anténních pořadů" (Nastavení → Server), default on.
    var tvAllows: Bool {
        didSet {
            UserDefaults.standard.set(tvAllows, forKey: Self.tvSwitchKey)
            if !tvAllows { clear() }
        }
    }

    @ObservationIgnored private var images: [String: (image: UIImage, final: Bool, madeAt: Date)] = [:]
    @ObservationIgnored private var order: [String] = []
    @ObservationIgnored private var statusCheckedAt: Date?
    @ObservationIgnored private var client: DVBClient?

    init() {
        tvAllows = UserDefaults.standard.object(forKey: Self.tvSwitchKey) as? Bool ?? true
    }

    /// New (or no) gateway: forget everything and ask it again.
    func setGateway(_ config: GatewayConfig?) {
        clear()
        gatewayAllows = false
        statusCheckedAt = nil
        client = config.map { DVBClient(config: $0, transport: URLSessionTransport.memoryOnly()) }
        Task { await refreshStatus() }
    }

    func refreshStatus(force: Bool = false) async {
        guard let client else { return }
        if !force, let checked = statusCheckedAt, Date().timeIntervalSince(checked) < Self.statusMaxAge { return }
        statusCheckedAt = Date()
        if let status = try? await client.status() {
            gatewayAllows = status.thumbnails
            if !gatewayAllows { clear() }
        }
    }

    func recordingID(for event: DVBEvent) -> String? {
        AntennaThumbnails.recordingID(for: event, gatewayAllows: gatewayAllows, tvAllows: tvAllows)
    }

    /// The still of `event`'s recording, or nil (no thumbnail, off, or the gateway has none).
    func image(for event: DVBEvent) async -> UIImage? {
        guard let id = recordingID(for: event), let client else { return nil }
        if let hit = images[id], hit.final || Date().timeIntervalSince(hit.madeAt) < Self.growingMaxAge {
            touch(id)
            return hit.image
        }
        guard let data = try? await client.thumbnail(recordingID: id), let image = UIImage(data: data) else { return nil }
        guard recordingID(for: event) != nil else { return nil }  // switched off meanwhile
        images[id] = (image, event.recording?.state == .completed, Date())
        touch(id)
        while order.count > Self.capacity { images[order.removeFirst()] = nil }
        return image
    }

    private func touch(_ id: String) {
        order.removeAll { $0 == id }
        order.append(id)
    }

    private func clear() {
        images = [:]
        order = []
    }
}
