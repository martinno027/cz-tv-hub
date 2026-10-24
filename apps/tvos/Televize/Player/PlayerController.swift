import AVFoundation
import AVKit
import CTCore
import os
import UIKit

private let playerLog = Logger(subsystem: "cz.cztvhub.tv", category: "player")

/// Owns the AVPlayer for one session: applies the start-over position and re-resolves
/// the stream once when playback fails (signed URLs expire after ~1 h). For finished Anténa
/// recordings it also offers "Přeskočit reklamu" during detected ad breaks (spec/ad-breaks.md).
/// Programmes and recordings save their progress for "Pokračovat" (never live).
@MainActor
final class PlayerController {
    private enum Resume {
        case date(Date)
        case seconds(Double)
    }

    let player = AVPlayer()
    var onFatalError: ((String) -> Void)?

    private var resolve: @MainActor () async throws -> PlaybackSource
    private var source: PlaybackSource
    private var hasRetried = false
    private var statusObservation: NSKeyValueObservation?
    private var statusHandled = false
    private var failureObserver: NSObjectProtocol?
    /// Playback diagnostics (time jumps, stalls, access/error log entries), visible in the Xcode console.
    private var diagnosticObservers: [NSObjectProtocol] = []
    private var timeObserver: Any?
    private var started = false
    private var stopped = false

    // "Přeskočit reklamu" (finished gateway recordings only).
    private let fetchAdBreaks: (@Sendable () async throws -> [AdBreak])?
    private var adBreaks: [AdBreak] = []
    private var adBreakTask: Task<Void, Never>?
    private var skipObserver: Any?
    private var shownSkipTarget: Double?
    private var isSkipping = false
    /// Set by the player screen; the skip button is one of its contextual actions.
    weak var playerViewController: AVPlayerViewController? {
        didSet {
            shownSkipTarget = nil
            playerViewController?.contextualActions = []
            updateSkipButton()
        }
    }

    private static let adBreakRefreshInterval: Duration = .seconds(60)

    // "Pokračovat": where to save progress; nil for live.
    private let progressTarget: ResumeTarget?
    private let resumeStore: ResumeStore?
    /// Timeline playback: the programme at the playhead and its progress for a player position (it changes
    /// while playing), instead of the fixed target.
    private let progressAt: (@MainActor (Double) -> (target: ResumeTarget, progress: TimeInterval)?)?
    private var progressObserver: Any?
    private var title: PlayerTitle
    private var liveTitle: (@MainActor () async -> PlayerTitle)?
    /// Poslanecká sněmovna: a 404 when opening = the broadcast ended (no retry, its own message).
    private var offAir: (@MainActor () -> Void)?
    private var titleTask: Task<Void, Never>?
    private var backgroundObserver: NSObjectProtocol?
    /// False until the start position has been applied, so the pre-seek position is never saved.
    private var positionApplied = false
    var isStartApplied: Bool { positionApplied }
    /// Audio renditions of the current item (gateway master playlists with several broadcast tracks).
    private var audioGroup: AVMediaSelectionGroup?

    /// `resolve` fetches a fresh source (new signed URL / new gateway session) after a failure.
    /// `adBreaks` is given only for finished recordings: it enables the "Přeskočit reklamu" button.
    /// `progress` is where to save the "Pokračovat" position (programmes and recordings only).
    /// `title` is the info bar for programmes; `liveTitle` looks up what is airing now (live), again at
    /// the programme's end and at least every minute (spec/apps.md "Title in the player's info bar").
    init(source: PlaybackSource, resolve: @escaping @MainActor () async throws -> PlaybackSource,
         adBreaks: (@Sendable () async throws -> [AdBreak])? = nil,
         progress: (target: ResumeTarget, store: ResumeStore)? = nil,
         progressAt: (store: ResumeStore, at: @MainActor (Double) -> (target: ResumeTarget, progress: TimeInterval)?)? = nil,
         title: PlayerTitle? = nil, liveTitle: (@MainActor () async -> PlayerTitle)? = nil,
         offAir: (@MainActor () -> Void)? = nil) {
        self.source = source
        self.offAir = offAir
        self.title = title ?? PlayerTitle(title: source.title)
        self.liveTitle = liveTitle
        self.resolve = resolve
        self.fetchAdBreaks = adBreaks
        let tracked = source.mode == .live ? nil : progress
        self.progressTarget = tracked?.target
        self.resumeStore = tracked?.store ?? progressAt?.store
        self.progressAt = progressAt?.at
        applyAudioPreference(language: nil, describesVideo: false)
    }

    func start() {
        guard !started else { return }
        started = true
        load(source, resume: nil)
        startLiveTitle()
        startAdBreaks()
        startProgressSaving()
        #if DEBUG
        timeObserver = player.addPeriodicTimeObserver(forInterval: CMTime(seconds: 5, preferredTimescale: 1), queue: .main) { [player] time in
            let date = player.currentItem?.currentDate().map { "\($0)" } ?? "-"
            let access = player.currentItem?.accessLog()?.events.last
            let ahead = player.currentItem?.loadedTimeRanges.last.map { CMTimeRangeGetEnd($0.timeRangeValue).seconds - time.seconds } ?? -1
            playerLog.info("tick rate=\(player.rate) time=\(time.seconds, format: .fixed(precision: 1)) date=\(date, privacy: .public) dropped=\(access?.numberOfDroppedVideoFrames ?? -1) stalls=\(access?.numberOfStalls ?? -1) ahead=\(ahead, format: .fixed(precision: 1))")
        }
        #endif
    }

    /// Zapping (live only): plays another channel's already resolved live stream in this player; `resolve` and
    /// `liveTitle` belong to the new channel from now on (re-resolve after a failure, info bar title).
    func switchLive(to source: PlaybackSource, resolve: @escaping @MainActor () async throws -> PlaybackSource,
                    liveTitle: @escaping @MainActor () async -> PlayerTitle, offAir: (@MainActor () -> Void)? = nil) {
        guard started, !stopped, self.source.mode == .live, source.mode == .live else { return }
        titleTask?.cancel()
        self.source = source
        self.resolve = resolve
        self.liveTitle = liveTitle
        self.offAir = offAir
        hasRetried = false
        title = PlayerTitle(title: source.title)
        applyAudioPreference(language: nil, describesVideo: false)
        load(source, resume: nil)
        startLiveTitle()
    }

    /// Timeline playback: plays another window of the channel's recording in this player, from
    /// `source.startPosition`; `resolve` belongs to the new window from now on.
    func switchSource(to source: PlaybackSource, resolve: @escaping @MainActor () async throws -> PlaybackSource) {
        guard started, !stopped else { return }
        saveProgress()
        keepSelectedAudio()
        self.source = source
        self.resolve = resolve
        hasRetried = false
        load(source, resume: nil)
    }

    /// The info bar title without reloading (timeline: the programme at the playhead).
    func updateTitle(_ next: PlayerTitle) {
        guard next != title else { return }
        title = next
        player.currentItem?.externalMetadata = Self.metadata(for: next)
    }

    /// Safe to call more than once (fatal error, then the screen disappearing).
    func stop() {
        stopped = true
        saveProgress()
        if let progressObserver { player.removeTimeObserver(progressObserver) }
        progressObserver = nil
        if let backgroundObserver { NotificationCenter.default.removeObserver(backgroundObserver) }
        backgroundObserver = nil
        positionApplied = false
        audioGroup = nil
        statusObservation = nil
        if let failureObserver { NotificationCenter.default.removeObserver(failureObserver) }
        failureObserver = nil
        diagnosticObservers.forEach(NotificationCenter.default.removeObserver)
        diagnosticObservers = []
        if let timeObserver { player.removeTimeObserver(timeObserver) }
        timeObserver = nil
        adBreakTask?.cancel()
        adBreakTask = nil
        titleTask?.cancel()
        titleTask = nil
        if let skipObserver { player.removeTimeObserver(skipObserver) }
        skipObserver = nil
        adBreaks = []
        shownSkipTarget = nil
        playerViewController?.contextualActions = []
        player.pause()
        player.replaceCurrentItem(with: nil)
    }

    // MARK: progress ("Pokračovat")

    private func startProgressSaving() {
        guard progressTarget != nil || progressAt != nil else { return }
        let interval = CMTime(seconds: ResumeRules.saveInterval, preferredTimescale: 1)
        progressObserver = player.addPeriodicTimeObserver(forInterval: interval, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self, self.player.timeControlStatus == .playing else { return }
                self.saveProgress()
            }
        }
        backgroundObserver = NotificationCenter.default.addObserver(
            forName: UIApplication.didEnterBackgroundNotification, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.saveProgress() }
        }
    }

    /// Seconds from the programme's start: timeshift by the frame's wall clock, otherwise the position.
    private func saveProgress() {
        guard let store = resumeStore, positionApplied, let item = player.currentItem, item.status == .readyToPlay else { return }
        let position = item.currentTime().seconds
        if let progressAt {
            guard position.isFinite, let (target, progress) = progressAt(position) else { return }
            store.save(target, progress: progress)
            return
        }
        guard let target = progressTarget else { return }
        let liveEdge = item.seekableTimeRanges.last.map { CMTimeRangeGetEnd($0.timeRangeValue).seconds }
        guard let progress = ResumeRules.progress(mode: source.mode, position: position, frameDate: item.currentDate(),
                                                  liveEdgePosition: liveEdge, programmeStart: target.programmeStart,
                                                  now: Date()) else { return }
        store.save(target, progress: progress)
    }

    // MARK: ad breaks

    /// Fetches the breaks now and every minute while playing; failures are silent (no button).
    private func startAdBreaks() {
        guard fetchAdBreaks != nil else { return }
        skipObserver = player.addPeriodicTimeObserver(forInterval: CMTime(seconds: 0.5, preferredTimescale: 600), queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.updateSkipButton() }
        }
        adBreakTask = Task { [weak self] in
            await self?.refreshAdBreaks()
            while !Task.isCancelled {
                try? await Task.sleep(for: Self.adBreakRefreshInterval)
                guard let self, !Task.isCancelled else { return }
                if self.player.timeControlStatus == .playing { await self.refreshAdBreaks() }
            }
        }
    }

    private func refreshAdBreaks() async {
        guard let fetchAdBreaks else { return }
        do {
            let breaks = try await fetchAdBreaks()
            guard !Task.isCancelled else { return }
            if breaks != adBreaks { playerLog.info("ad breaks: \(breaks.count)") }
            adBreaks = breaks
            updateSkipButton()
        } catch {
            playerLog.info("ad breaks unavailable: \(String(describing: error), privacy: .public)")
        }
    }

    /// Shows the button while the position is inside a break (and not in its last seconds).
    private func updateSkipButton() {
        guard let viewController = playerViewController, !isSkipping else { return }
        let position = player.currentItem?.currentTime().seconds ?? .nan
        let target = AdBreaks.skipTarget(adBreaks, position: position)
        guard target != shownSkipTarget else { return }
        shownSkipTarget = target
        if let target {
            viewController.contextualActions = [
                UIAction(title: "Přeskočit reklamu") { [weak self] _ in self?.skipAd(to: target) },
            ]
        } else {
            viewController.contextualActions = []
        }
    }

    private func skipAd(to target: Double) {
        guard let item = player.currentItem else { return }
        playerLog.info("skip ad break to \(target, format: .fixed(precision: 1))")
        isSkipping = true
        shownSkipTarget = nil
        playerViewController?.contextualActions = []
        Task {
            await item.seek(to: CMTime(seconds: target, preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero)
            isSkipping = false
            updateSkipButton()
        }
    }

    // MARK: loading

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
        item.externalMetadata = Self.metadata(for: title)
        positionApplied = false
        player.replaceCurrentItem(with: item)
        observe(item, source: source, resume: resume)
        // Live has no start position: let AVPlayer start as soon as it has enough data instead of
        // waiting for readyToPlay first (that round trip delayed every channel start).
        if source.mode == .live { player.play() }
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
                            await self.loadAudioOptions(item)
                            await self.applyStartPosition(item, source: source, resume: resume)
                            if self.player.currentItem === item { self.positionApplied = true }
                            self.player.play()
                            await self.keepRecordingStart(item)
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
        observeDiagnostics(item)
    }

    /// What the player does when the picture glitches: jumps in time, stalls, dropped frames, load errors.
    private func observeDiagnostics(_ item: AVPlayerItem) {
        diagnosticObservers.forEach(NotificationCenter.default.removeObserver)
        let center = NotificationCenter.default
        diagnosticObservers = [
            center.addObserver(forName: AVPlayerItem.timeJumpedNotification, object: item, queue: .main) { _ in
                playerLog.info("diag time jumped to \(item.currentTime().seconds, format: .fixed(precision: 2))")
            },
            center.addObserver(forName: AVPlayerItem.playbackStalledNotification, object: item, queue: .main) { _ in
                playerLog.error("diag playback stalled at \(item.currentTime().seconds, format: .fixed(precision: 2))")
            },
            center.addObserver(forName: AVPlayerItem.newAccessLogEntryNotification, object: item, queue: .main) { _ in
                guard let event = item.accessLog()?.events.last else { return }
                playerLog.info("diag access: stalls=\(event.numberOfStalls) dropped=\(event.numberOfDroppedVideoFrames) segments=\(event.numberOfMediaRequests) observed=\(Int(event.observedBitrate / 1000)) kbit/s buffered-ahead=\(item.loadedTimeRanges.last.map { CMTimeRangeGetEnd($0.timeRangeValue).seconds - item.currentTime().seconds } ?? -1, format: .fixed(precision: 1)) s")
            },
            center.addObserver(forName: AVPlayerItem.newErrorLogEntryNotification, object: item, queue: .main) { _ in
                guard let event = item.errorLog()?.events.last else { return }
                playerLog.error("diag error: \(event.errorStatusCode) \(event.errorDomain, privacy: .public) \(event.errorComment ?? "", privacy: .public) \(event.uri ?? "", privacy: .public)")
            },
        ]
    }

    private func applyStartPosition(_ item: AVPlayerItem, source: PlaybackSource, resume: Resume?) async {
        switch (source.mode, resume) {
        case (.vod, .seconds(let seconds)?):
            await item.seek(to: CMTime(seconds: seconds, preferredTimescale: 600))
        case (.recording, .seconds(let seconds)?):
            await seekRecording(item, to: seconds)
        case (.startOver, .date(let date)?):
            await seek(item, to: date, fallbackOffset: nil)
        case (.startOver(let date, let offset), _):
            await seek(item, to: date, fallbackOffset: offset)
        case (.vod, _):
            // "Pokračovat" in the archive, else from the beginning.
            if let start = source.startPosition {
                await item.seek(to: CMTime(seconds: start, preferredTimescale: 600))
            }
        case (.recording, _):
            // "Od začátku" of a growing recording too: players open a growing playlist at its live edge.
            await seekRecording(item, to: source.startPosition ?? 0)
        default:
            break  // live edge
        }
    }

    /// Where a recording / timeline was asked to start; checked once it plays (`keepRecordingStart`).
    private var recordingStart: (item: AVPlayerItem, seconds: Double)?

    /// Recordings and timelines start exactly at `seconds` (zero tolerance), the result logged.
    private func seekRecording(_ item: AVPlayerItem, to seconds: Double) async {
        recordingStart = (item, seconds)
        let finished = await item.seek(to: CMTime(seconds: seconds, preferredTimescale: 600),
                                       toleranceBefore: .zero, toleranceAfter: .zero)
        let now = item.currentTime().seconds
        let ranges = item.seekableTimeRanges.map { range -> String in
            let r = range.timeRangeValue
            return String(format: "%.1f-%.1f", r.start.seconds, CMTimeRangeGetEnd(r).seconds)
        }.joined(separator: ",")
        playerLog.info("start seek to \(seconds, format: .fixed(precision: 1)): finished=\(finished) now=\(now, format: .fixed(precision: 1)) seekable=\(ranges, privacy: .public)")
    }

    /// AVPlayer moves a growing (EVENT) playlist without `EXT-X-START` to its live edge when playback starts, after
    /// an already finished seek (tvOS 26 simulator, 2026-10-24: seek to 0 finished, then playing at 132 of 132).
    /// For the first seconds of playback: if the position left the start, seek there again.
    private func keepRecordingStart(_ item: AVPlayerItem) async {
        guard let start = recordingStart, start.item === item else { return }
        recordingStart = nil
        for attempt in 1...3 {
            for _ in 0..<10 {
                try? await Task.sleep(for: .milliseconds(200))
                guard player.currentItem === item, !stopped else { return }
                if player.timeControlStatus == .playing { break }
            }
            let now = item.currentTime().seconds
            playerLog.info("start check \(attempt): now=\(now, format: .fixed(precision: 1)) wanted=\(start.seconds, format: .fixed(precision: 1))")
            // Playing on from the start is fine; only a jump ahead of it is not.
            guard now.isFinite, now - start.seconds > 5 else { return }
            await item.seek(to: CMTime(seconds: start.seconds, preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero)
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
        if let offAir, Self.isNotFound(error, item: player.currentItem) {
            playerLog.info("stream not found: broadcast over")
            stop()
            offAir()
            onFatalError?(PSPStreams.offAirMessage)
            return
        }
        guard !hasRetried else {
            stop()
            onFatalError?("Přehrávání se nezdařilo. Zkuste to prosím znovu.")
            return
        }
        hasRetried = true
        let resume = currentResume()
        keepSelectedAudio()
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

    /// HTTP 404 for the playlist: in the item's error log, or CoreMedia's -12938 (HTTP 404) in the error chain.
    private static func isNotFound(_ error: Error?, item: AVPlayerItem?) -> Bool {
        if item?.errorLog()?.events.contains(where: { $0.errorStatusCode == 404 }) == true { return true }
        var current = error.map { $0 as NSError }
        while let nsError = current {
            if nsError.code == -12938 || (nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorFileDoesNotExist) {
                return true
            }
            current = nsError.userInfo[NSUnderlyingErrorKey] as? NSError
        }
        return false
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

    // MARK: audio ("Audio choice for Anténa", spec/apps.md)

    /// The audio renditions of the current item with the selected one (timeline player's own audio list).
    var audioChoices: (options: [AVMediaSelectionOption], selected: AVMediaSelectionOption?) {
        guard let group = audioGroup, let item = player.currentItem else { return ([], nil) }
        return (group.options, item.currentMediaSelection.selectedMediaOption(in: group))
    }

    func selectAudio(_ option: AVMediaSelectionOption) {
        guard let group = audioGroup, let item = player.currentItem else { return }
        item.select(option, in: group)
        playerLog.info("audio selected \(option.displayName, privacy: .public)")
    }

    static func audioTitle(_ option: AVMediaSelectionOption) -> String {
        AVMetadataItem.metadataItems(from: option.commonMetadata, filteredByIdentifier: .commonIdentifierTitle)
            .first?.stringValue ?? option.displayName
    }

    /// Czech first, or the viewer's own pick when the stream is reloaded. Applies to the next items too,
    /// so AVPlayerViewController's audio menu starts on that track and still lists all of them.
    private func applyAudioPreference(language: String?, describesVideo: Bool) {
        let criteria = AVPlayerMediaSelectionCriteria(
            preferredLanguages: AudioPreference.languages(selected: language),
            preferredMediaCharacteristics: describesVideo ? [.describesVideoForAccessibility] : nil
        )
        player.setMediaSelectionCriteria(criteria, forMediaCharacteristic: .audible)
    }

    private func loadAudioOptions(_ item: AVPlayerItem) async {
        audioGroup = nil
        guard let group = try? await item.asset.loadMediaSelectionGroup(for: .audible),
              player.currentItem === item else { return }
        audioGroup = group
        let names = group.options.map { option in
            "\(Self.audioTitle(option)) [\(option.extendedLanguageTag ?? "-")]"
        }.joined(separator: ", ")
        let selected = item.currentMediaSelection.selectedMediaOption(in: group)?.displayName ?? "-"
        playerLog.info("audio options: \(names, privacy: .public); selected \(selected, privacy: .public)")
    }

    /// Before a reload: the new item should start on the track the viewer was listening to.
    private func keepSelectedAudio() {
        guard let item = player.currentItem, let group = audioGroup,
              let option = item.currentMediaSelection.selectedMediaOption(in: group) else { return }
        applyAudioPreference(language: option.extendedLanguageTag ?? option.locale?.identifier,
                             describesVideo: option.hasMediaCharacteristic(.describesVideoForAccessibility))
    }

    // MARK: info bar title

    private func startLiveTitle() {
        guard let liveTitle else { return }
        titleTask = Task { [weak self] in
            while !Task.isCancelled {
                let next = await liveTitle()
                guard let self, !Task.isCancelled else { return }
                if next != self.title {
                    self.title = next
                    // in place: the item (and the playback) stays
                    self.player.currentItem?.externalMetadata = Self.metadata(for: next)
                    playerLog.info("title \(next.title, privacy: .public) / \(next.subtitle, privacy: .public)")
                }
                let wait = next.nextCheck(after: Date()).timeIntervalSinceNow
                try? await Task.sleep(for: .seconds(max(1, wait)))
            }
        }
    }

    private static func metadata(for title: PlayerTitle) -> [AVMetadataItem] {
        var items = [metadata(.commonIdentifierTitle, title.title)]
        if !title.subtitle.isEmpty { items.append(metadata(.iTunesMetadataTrackSubTitle, title.subtitle)) }
        return items
    }

    private static func metadata(_ identifier: AVMetadataIdentifier, _ value: String) -> AVMetadataItem {
        let item = AVMutableMetadataItem()
        item.identifier = identifier
        item.value = value as NSString
        item.extendedLanguageTag = "cs"
        return item.copy() as! AVMetadataItem
    }
}
