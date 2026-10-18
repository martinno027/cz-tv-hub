import AVFoundation
import CTCore
import os

private let playerLog = Logger(subsystem: "cz.cztvhub.tv", category: "player")

/// Owns the AVPlayer for one session: applies the start-over position and re-resolves
/// the stream once when playback fails (signed URLs expire after ~1 h).
@MainActor
final class PlayerController {
    private enum Resume {
        case date(Date)
        case seconds(Double)
    }

    let player = AVPlayer()
    var onFatalError: ((String) -> Void)?

    private let resolve: @MainActor () async throws -> PlaybackSource
    private var source: PlaybackSource
    private var hasRetried = false
    private var statusObservation: NSKeyValueObservation?
    private var statusHandled = false
    private var failureObserver: NSObjectProtocol?
    private var timeObserver: Any?
    private var started = false

    /// `resolve` fetches a fresh source (new signed URL / new gateway session) after a failure.
    init(source: PlaybackSource, resolve: @escaping @MainActor () async throws -> PlaybackSource) {
        self.source = source
        self.resolve = resolve
    }

    func start() {
        guard !started else { return }
        started = true
        load(source, resume: nil)
        #if DEBUG
        timeObserver = player.addPeriodicTimeObserver(forInterval: CMTime(seconds: 5, preferredTimescale: 1), queue: .main) { [player] time in
            let date = player.currentItem?.currentDate().map { "\($0)" } ?? "-"
            playerLog.info("tick rate=\(player.rate) time=\(time.seconds, format: .fixed(precision: 1)) date=\(date, privacy: .public)")
        }
        #endif
    }

    /// Safe to call more than once (fatal error, then the screen disappearing).
    func stop() {
        statusObservation = nil
        if let failureObserver { NotificationCenter.default.removeObserver(failureObserver) }
        failureObserver = nil
        if let timeObserver { player.removeTimeObserver(timeObserver) }
        timeObserver = nil
        player.pause()
        player.replaceCurrentItem(with: nil)
    }

    private func load(_ source: PlaybackSource, resume: Resume?) {
        var url = source.url
        #if DEBUG
        // `-breakURLs first|all` simulates expired URLs to exercise the re-resolve and error paths.
        let broken = UserDefaults.standard.string(forKey: "breakURLs")
        if broken == "all" || (broken == "first" && !hasRetried) {
            url = URL(string: "https://127.0.0.1:9/expired.m3u8")!
        }
        #endif
        // Gateway streams need the bearer key on the playlist and on every segment.
        let options: [String: Any]? = source.headers.isEmpty ? nil : ["AVURLAssetHTTPHeaderFieldsKey": source.headers]
        let item = AVPlayerItem(asset: AVURLAsset(url: url, options: options))
        item.externalMetadata = [Self.metadata(.commonIdentifierTitle, source.title)]
        player.replaceCurrentItem(with: item)
        observe(item, source: source, resume: resume)
        // A stream that never becomes ready must end in the failure path, not a frozen screen.
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(Self.readyTimeout))
            guard let self, self.player.currentItem === item, item.status != .readyToPlay, !self.statusHandled else { return }
            self.statusHandled = true
            self.handleFailure(CTError("Stream se nepodařilo načíst", detail: "not ready after \(Self.readyTimeout) s"))
        }
    }

    private static let readyTimeout: Double = 20

    private func observe(_ item: AVPlayerItem, source: PlaybackSource, resume: Resume?) {
        statusHandled = false
        statusObservation = item.observe(\.status, options: [.initial, .new]) { [weak self] item, _ in
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    guard let self, !self.statusHandled, self.player.currentItem === item else { return }
                    switch item.status {
                    case .readyToPlay:
                        self.statusHandled = true
                        playerLog.info("ready to play")
                        Task {
                            await self.applyStartPosition(item, source: source, resume: resume)
                            self.player.play()
                        }
                    case .failed:
                        self.statusHandled = true
                        self.handleFailure(item.error)
                    default:
                        break
                    }
                }
            }
        }
        if let failureObserver { NotificationCenter.default.removeObserver(failureObserver) }
        failureObserver = NotificationCenter.default.addObserver(
            forName: AVPlayerItem.failedToPlayToEndTimeNotification, object: item, queue: .main
        ) { [weak self] notification in
            let error = notification.userInfo?[AVPlayerItemFailedToPlayToEndTimeErrorKey] as? Error
            MainActor.assumeIsolated { self?.handleFailure(error) }
        }
    }

    private func applyStartPosition(_ item: AVPlayerItem, source: PlaybackSource, resume: Resume?) async {
        switch (source.mode, resume) {
        case (.vod, .seconds(let seconds)?):
            await item.seek(to: CMTime(seconds: seconds, preferredTimescale: 600))
        case (.startOver, .date(let date)?):
            await seek(item, to: date, fallbackOffset: nil)
        case (.startOver(let date, let offset), _):
            await seek(item, to: date, fallbackOffset: offset)
        case (.recording, .seconds(let seconds)?):
            await item.seek(to: CMTime(seconds: seconds, preferredTimescale: 600))
        case (.recording, _):
            await item.seek(to: .zero)
        default:
            break  // live edge, or VOD from the beginning
        }
    }

    /// Seek by wall clock (EXT-X-PROGRAM-DATE-TIME); if the stream has no dates, fall back to
    /// `offset` seconds after the oldest point of the timeshift buffer.
    private func seek(_ item: AVPlayerItem, to date: Date, fallbackOffset: TimeInterval?) async {
        if await item.seek(to: date) {
            playerLog.info("seek by date ok: \(date, privacy: .public)")
            return
        }
        guard let offset = fallbackOffset, let range = item.seekableTimeRanges.last?.timeRangeValue else {
            playerLog.error("seek by date failed, no fallback")
            return
        }
        let latest = max(range.start.seconds, range.end.seconds - 10)
        let target = min(range.start.seconds + offset, latest)
        playerLog.info("seek by date failed, offset fallback to \(target, format: .fixed(precision: 1))")
        await item.seek(to: CMTime(seconds: target, preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero)
    }

    private func handleFailure(_ error: Error?) {
        playerLog.error("playback failed: \(String(describing: error), privacy: .public)")
        guard !hasRetried else {
            stop()
            onFatalError?("Přehrávání se nezdařilo. Zkuste to prosím znovu.")
            return
        }
        hasRetried = true
        let resume = currentResume()
        Task {
            do {
                let fresh = try await resolve()
                playerLog.info("re-resolved after failure")
                source = fresh
                load(fresh, resume: resume)
            } catch {
                stop()
                onFatalError?(AppModel.message(for: error))
            }
        }
    }

    private func currentResume() -> Resume? {
        guard let item = player.currentItem else { return nil }
        switch source.mode {
        case .live:
            return nil
        case .startOver:
            return item.currentDate().map(Resume.date)
        case .vod, .recording:
            let seconds = item.currentTime().seconds
            return seconds.isFinite && seconds > 0 ? .seconds(seconds) : nil
        }
    }

    private static func metadata(_ identifier: AVMetadataIdentifier, _ value: String) -> AVMetadataItem {
        let item = AVMutableMetadataItem()
        item.identifier = identifier
        item.value = value as NSString
        item.extendedLanguageTag = "cs"
        return item.copy() as! AVMetadataItem
    }
}
