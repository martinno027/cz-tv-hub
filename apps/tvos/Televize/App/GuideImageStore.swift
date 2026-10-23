import CTCore
import Foundation
import Observation
import UIKit

/// Images of the guide detail: ČT images and "Anténa thumbnails" (spec/apps.md "Image prefetch", spec/dvb-gateway.md
/// "Thumbnails", approved 2026-10-23), prefetched around the guide's focus and kept in memory only (ephemeral
/// sessions without URLCache, an LRU here; never on disk).
@MainActor
@Observable
final class GuideImageStore {
    /// What to load. A thumbnail of a finished recording is kept; one of a growing recording is refreshed after 60 s.
    enum Source: Hashable, Sendable {
        case ct(URL)
        case thumbnail(recordingID: String, final: Bool)

        var lane: ImageLane {
            switch self {
            case .ct: return .ct
            case .thumbnail: return .gateway
            }
        }

        fileprivate var key: String {
            switch self {
            case .ct(let url): return "ct|\(url.absoluteString)"
            case .thumbnail(let id, _): return "thumb|\(id)"
            }
        }
    }

    static let tvSwitchKey = "antennaThumbnails"
    /// Compressed images around the focus (spec: about 300).
    private static let capacity = 300
    /// Decoded ones for the detail; decoding all 300 would take hundreds of MB.
    private static let decodedCapacity = 24
    /// Detail image: 360 × 203 pt, 2× on a 4K TV.
    private static let decodedSize = CGSize(width: 720, height: 406)
    private static let growingMaxAge: TimeInterval = 60
    private static let statusMaxAge: TimeInterval = 5 * 60

    /// The household allows thumbnails (`/dvb/status`); refreshed at most every 5 minutes.
    private(set) var gatewayAllows = false
    /// This TV's switch "Náhledy anténních pořadů" (Nastavení → Server), default on.
    var tvAllows: Bool {
        didSet {
            UserDefaults.standard.set(tvAllows, forKey: Self.tvSwitchKey)
            if !tvAllows { clearThumbnails() }
        }
    }
    /// Bumped when the wanted (focused) image is ready, so the detail shows it.
    private(set) var revision = 0

    @ObservationIgnored private var data = LRUCache<String, (data: Data, madeAt: Date)>(capacity: capacity)
    @ObservationIgnored private var decoded = LRUCache<String, UIImage>(capacity: decodedCapacity)
    @ObservationIgnored private var queue = ImageQueue<Source>()
    @ObservationIgnored private var misses = ThumbnailMisses()
    /// The detail's image: decoded as soon as its data is here.
    @ObservationIgnored private var wanted: Source?
    @ObservationIgnored private var statusCheckedAt: Date?
    @ObservationIgnored private var client: DVBClient?
    @ObservationIgnored private let ctTransport = URLSessionTransport.memoryOnly()

    init() {
        tvAllows = UserDefaults.standard.object(forKey: Self.tvSwitchKey) as? Bool ?? true
    }

    /// New (or no) gateway: forget its thumbnails and ask it again.
    func setGateway(_ config: GatewayConfig?) {
        clearThumbnails()
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
            if !gatewayAllows { clearThumbnails() }
        }
    }

    func recordingID(for event: DVBEvent) -> String? {
        AntennaThumbnails.recordingID(for: event, gatewayAllows: gatewayAllows, tvAllows: tvAllows)
    }

    /// The thumbnail to load for `event`, nil when there is none or thumbnails are off.
    func thumbnail(for event: DVBEvent) -> Source? {
        recordingID(for: event).map { .thumbnail(recordingID: $0, final: event.recording?.state == .completed) }
    }

    /// The guide opened: 404s of finished recordings are remembered for the guide's lifetime only.
    func guideOpened() {
        misses.removeAll()
    }

    /// The decoded image for the detail, nil until it is loaded (read `revision` to be told).
    func image(_ source: Source) -> UIImage? {
        _ = revision
        return isAllowed(source) ? decoded.peek(source.key) : nil
    }

    /// The detail shows `source`: decode it at once when its data is here, else load it ahead of the queue.
    func want(_ source: Source) {
        wanted = source
        guard isAllowed(source), decoded.peek(source.key) == nil || isStale(source) else { return }
        if let hit = data.get(source.key) {
            decode(source, hit.data)
            if !isStale(source) { return }
        }
        guard !isMissing(source) else { return }
        queue.prioritize(source, lane: source.lane)
        pump()
    }

    /// A new prefetch window (nearest first): queued requests that left it are dropped, running ones finish.
    func prefetch(_ sources: [Source]) {
        let needed = sources.filter { isAllowed($0) && !isMissing($0) && (data.peek($0.key) == nil || isStale($0)) }
        queue.replace(with: needed.map { ($0, $0.lane) }, keep: wanted)
        pump()
    }

    // MARK: loading

    private func pump() {
        for request in queue.start() {
            Task { await load(request.key) }
        }
    }

    private func load(_ source: Source) async {
        defer {
            queue.finish(source)
            pump()
        }
        let result: DVBThumbnail
        switch source {
        case .ct(let url):
            var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 15)
            request.setValue(CTClient.userAgent, forHTTPHeaderField: "User-Agent")
            if let response = try? await ctTransport.send(request), response.status == 200 {
                result = .image(response.body)
            } else {
                result = .failed
            }
        case .thumbnail(let id, _):
            guard let client else { return }
            result = (try? await client.thumbnail(recordingID: id)) ?? .failed
        }
        guard isAllowed(source) else { return }  // switched off meanwhile
        switch result {
        case .image(let body):
            data.set(source.key, (body, Date()))
            if source == wanted { decode(source, body) }
        case .missing:
            if case .thumbnail(let id, let final) = source { misses.record(id, final: final, at: Date()) }
        case .failed:
            break
        }
    }

    private func decode(_ source: Source, _ body: Data) {
        let key = source.key
        Task {
            let size = Self.decodedSize
            let image = await Task.detached(priority: .userInitiated) {
                UIImage(data: body).flatMap { $0.preparingThumbnail(of: Self.fitting($0.size, in: size)) ?? $0 }
            }.value
            guard let image, isAllowed(source) else { return }
            decoded.set(key, image)
            revision += 1
        }
    }

    // MARK: rules

    private nonisolated static func fitting(_ size: CGSize, in box: CGSize) -> CGSize {
        guard size.width > 0, size.height > 0 else { return box }
        let scale = min(1, max(box.width / size.width, box.height / size.height))  // fills the detail frame
        return CGSize(width: (size.width * scale).rounded(), height: (size.height * scale).rounded())
    }

    private func isAllowed(_ source: Source) -> Bool {
        if case .thumbnail = source { return client != nil && gatewayAllows && tvAllows }
        return true
    }

    private func isMissing(_ source: Source) -> Bool {
        guard case .thumbnail(let id, _) = source else { return false }
        return misses.isMissing(id, at: Date())
    }

    /// A growing recording's thumbnail older than 60 s is loaded again (and shown until then).
    private func isStale(_ source: Source) -> Bool {
        guard case .thumbnail(_, let final) = source, !final, let hit = data.peek(source.key) else { return false }
        return Date().timeIntervalSince(hit.madeAt) >= Self.growingMaxAge
    }

    private func clearThumbnails() {
        data.removeAll { $0.hasPrefix("thumb|") }
        decoded.removeAll { $0.hasPrefix("thumb|") }
        misses.removeAll()
        queue.replace(with: queue.pending.filter { $0.lane != .gateway }, keep: nil)
        revision += 1
    }
}
