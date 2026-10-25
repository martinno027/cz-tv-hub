import CTCore
import Foundation
import UIKit

/// Card images of the Archiv tab (spec/apps.md "Archiv"): ČT images from ČT's CDN, TMDb posters and recording stills
/// through the gateway (with the key; stills only while thumbnails are allowed). Memory only: ephemeral sessions
/// without URLCache, the downloaded bytes and a few decoded images in LRUs here, never on disk.
///
/// Each card asks for its own image while it is built (`image(_:)` from its `.task`), so a loaded image redraws only
/// that card; a card scrolled away cancels its wait. At most 4 ČT and 2 gateway requests run at a time.
@MainActor
final class ArchiveImageStore {
    /// Card image: 400 × 225 pt, 2× on a 4K TV is more than the cards need; fill-decoded to this.
    private static let decodedSize = CGSize(width: 640, height: 360)
    private static let byteBudget = 24 * 1024 * 1024
    /// About two screens of cards decoded (≈ 0.9 MB each).
    private static let decodedCapacity = 48

    private var data = LRUCache<ArchiveImage, Data>(capacity: 400, costLimit: byteBudget)
    private var decoded = LRUCache<ArchiveImage, UIImage>(capacity: decodedCapacity)
    private var loading: [ArchiveImage: Task<Data?, Never>] = [:]
    private var running: [ImageLane: Int] = [:]
    private var waiting: [ImageLane: [(id: UUID, resume: CheckedContinuation<Bool, Never>)]] = [:]
    private var client: DVBClient?
    private var thumbnailsAllowed: () -> Bool = { false }
    private let ctTransport = URLSessionTransport.memoryOnly()

    /// New (or no) gateway: its images are forgotten.
    func setGateway(_ config: GatewayConfig?, thumbnailsAllowed: @escaping () -> Bool) {
        client = config.map { DVBClient(config: $0, transport: URLSessionTransport.memoryOnly()) }
        self.thumbnailsAllowed = thumbnailsAllowed
        data.removeAll { $0.isGateway }
        decoded.removeAll { $0.isGateway }
    }

    /// The decoded image if it is here already (no loading).
    func cached(_ source: ArchiveImage) -> UIImage? {
        isAllowed(source) ? decoded.peek(source) : nil
    }

    /// The card's image, loaded when needed; nil when there is none, it is not allowed, or the card went away.
    func image(_ source: ArchiveImage) async -> UIImage? {
        guard isAllowed(source) else { return nil }
        if let hit = decoded.get(source) { return hit }
        let body: Data?
        if let hit = data.get(source) {
            body = hit
        } else {
            body = await load(source)
        }
        guard let body, !Task.isCancelled, isAllowed(source) else { return nil }
        let size = Self.decodedSize
        let image = await Task.detached(priority: .userInitiated) {
            UIImage(data: body).flatMap { $0.preparingThumbnail(of: Self.fitting($0.size, in: size)) ?? $0 }
        }.value
        if let image { decoded.set(source, image) }
        return image
    }

    // MARK: loading

    /// One download per image, shared by the cards asking for it; its slot is given up when the asking card is gone.
    private func load(_ source: ArchiveImage) async -> Data? {
        if let running = loading[source] { return await running.value }
        let lane: ImageLane = source.isGateway ? .gateway : .ct
        guard await acquire(lane) else { return nil }
        let task = Task { await fetch(source) }
        loading[source] = task
        let body = await task.value
        loading[source] = nil
        release(lane)
        if let body { data.set(source, body, cost: body.count) }
        return body
    }

    private func fetch(_ source: ArchiveImage) async -> Data? {
        switch source {
        case .ct(let url):
            var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 15)
            request.setValue(CTClient.userAgent, forHTTPHeaderField: "User-Agent")
            guard let response = try? await ctTransport.send(request), response.status == 200 else { return nil }
            return response.body
        case .poster(let path):
            return try? await client?.detailsImage(path: path)
        case .thumbnail(let id):
            if case .image(let body)? = try? await client?.thumbnail(recordingID: id) { return body }
            return nil
        }
    }

    /// Waits for a free request slot of `lane`; false when the waiting task was cancelled (the card went away).
    private func acquire(_ lane: ImageLane) async -> Bool {
        if running[lane, default: 0] < lane.limit {
            running[lane, default: 0] += 1
            return true
        }
        let id = UUID()
        return await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                if Task.isCancelled {
                    continuation.resume(returning: false)
                } else {
                    waiting[lane, default: []].append((id, continuation))
                }
            }
        } onCancel: {
            Task { @MainActor in self.cancelWait(id, lane: lane) }
        }
    }

    private func cancelWait(_ id: UUID, lane: ImageLane) {
        guard let index = waiting[lane]?.firstIndex(where: { $0.id == id }) else { return }
        waiting[lane]?.remove(at: index).resume.resume(returning: false)
    }

    /// Hands the slot to the newest waiter: the card just scrolled to is the one on screen.
    private func release(_ lane: ImageLane) {
        if let next = waiting[lane]?.popLast() {
            next.resume.resume(returning: true)
        } else {
            running[lane, default: 1] -= 1
        }
    }

    private func isAllowed(_ source: ArchiveImage) -> Bool {
        switch source {
        case .ct: return true
        case .poster: return client != nil
        case .thumbnail: return client != nil && thumbnailsAllowed()
        }
    }

    private nonisolated static func fitting(_ size: CGSize, in box: CGSize) -> CGSize {
        guard size.width > 0, size.height > 0 else { return box }
        let scale = min(1, max(box.width / size.width, box.height / size.height))
        return CGSize(width: (size.width * scale).rounded(), height: (size.height * scale).rounded())
    }
}

private extension ArchiveImage {
    var isGateway: Bool {
        if case .ct = self { return false }
        return true
    }
}
