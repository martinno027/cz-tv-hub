import AVFoundation
import CTCore
import Observation
import os

private let timelineLog = Logger(subsystem: "cz.cztvhub.tv", category: "timeline")

/// "Anténa timeline player" (spec/apps.md): a `b…` programme plays inside a window of its channel's recording.
/// Owns the window (`/dvb/timeline/<id>`), maps player positions to wall clock, seeks (clicks, held keys, swipes,
/// ▲/▼ jumps), opens the previous/next window at the edges, re-fetches a growing window every 60 s, offers
/// "Přeskočit reklamu" for the programme at the playhead and tells the player what to remember for "Pokračovat".
/// The `PlayerController` keeps loading, re-resolving after failures, diagnostics and audio.
@MainActor
@Observable
final class TimelineController {
    private(set) var timeline: DVBTimeline?
    /// The player's position, refreshed while the bar is on screen.
    private(set) var position: Double = 0
    /// A seek being chosen (held key, swipe): the playhead and its label follow it, the video seeks on release.
    private(set) var scrubPosition: Double?
    private(set) var barVisible = true
    private(set) var isPaused = false
    /// Where "Přeskočit reklamu" seeks to, while it is offered.
    private(set) var skipTarget: Double?
    /// The gateway's Czech text after a failed switch to `previous` / `next` (the old window keeps playing).
    private(set) var message: String?
    let channelName: String

    @ObservationIgnored private let client: DVBClient
    @ObservationIgnored private weak var controller: PlayerController?
    @ObservationIgnored private var player: AVPlayer? { controller?.player }
    /// Wall clock the next resolve starts at (the programme start − 30 s, "Pokračovat", or a window hand-off).
    @ObservationIgnored private var startTime: Date
    @ObservationIgnored private var firstID: String
    /// A seek on its way: the playhead stays there until the player has arrived. Observed like `position`:
    /// while it is set the bar does not read `position`, so clearing it must redraw the bar.
    private var pendingSeek: Double?
    @ObservationIgnored private var seekGeneration = 0
    @ObservationIgnored private var switching = false
    @ObservationIgnored private var currentPosition: Double = 0
    @ObservationIgnored private var timeObserver: Any?
    @ObservationIgnored private var endObserver: NSObjectProtocol?
    @ObservationIgnored private var rateObservation: NSKeyValueObservation?
    @ObservationIgnored private var hideTask: Task<Void, Never>?
    @ObservationIgnored private var refreshTask: Task<Void, Never>?
    @ObservationIgnored private var breaksTask: Task<Void, Never>?
    @ObservationIgnored private var breaksProgrammeID: String?
    @ObservationIgnored private var windowBreaks: [AdBreak] = []
    @ObservationIgnored private var titleShown: PlayerTitle?
    @ObservationIgnored private var stopped = false
    @ObservationIgnored private var playbackStarted = false

    static let barSeconds: Duration = .seconds(5)
    /// A full swipe across the touch surface scrubs about this far.
    static let swipeSeconds: Double = 600

    init(client: DVBClient, programmeID: String, startTime: Date, channelName: String) {
        self.client = client
        self.firstID = programmeID
        self.startTime = startTime
        self.channelName = channelName
    }

    // MARK: resolving

    /// Fetches the window's JSON and prepares its playlist; the source starts at `startTime` mapped into the window.
    /// Also the player's re-resolve after a failure (same window, it then resumes at its own position).
    func resolve() async throws -> PlaybackSource {
        let window = try await client.timeline(id: timeline?.id ?? firstID)
        let source = try await source(for: window)
        apply(window)
        return source
    }

    private func source(for window: DVBTimeline) async throws -> PlaybackSource {
        let url = client.timelinePlaylistURL(window)
        try await client.preparePlaylist(url)
        let start = window.map.position(at: startTime)
        timelineLog.info("window \(window.id, privacy: .public) pieces=\(window.map.pieces.count) duration=\(window.map.duration, format: .fixed(precision: 1)) complete=\(window.complete) start=\(start, format: .fixed(precision: 1))")
        return .antennaRecording(url: url, title: channelName, headers: client.config.authorizationHeaders, resumeAt: start)
    }

    private func apply(_ window: DVBTimeline) {
        timeline = window
        if !window.complete { startRefreshing() }
    }

    // MARK: lifecycle

    func attach(_ controller: PlayerController) {
        self.controller = controller
    }

    func start() {
        guard let player else { return }
        timeObserver = player.addPeriodicTimeObserver(forInterval: CMTime(seconds: 0.25, preferredTimescale: 600),
                                                      queue: .main) { [weak self] time in
            MainActor.assumeIsolated { self?.tick(time.seconds) }
        }
        endObserver = NotificationCenter.default.addObserver(forName: AVPlayerItem.didPlayToEndTimeNotification,
                                                             object: nil, queue: .main) { [weak self] note in
            let ended = ObjectIdentifier(note.object as AnyObject)
            MainActor.assumeIsolated {
                guard let self, let item = self.player?.currentItem, ObjectIdentifier(item) == ended else { return }
                self.reachedEnd()
            }
        }
        rateObservation = player.observe(\.timeControlStatus, options: [.new]) { [weak self] player, _ in
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    guard let self else { return }
                    let paused = player.timeControlStatus == .paused
                    guard paused != self.isPaused else { return }
                    self.isPaused = paused
                    if paused { self.showBar() } else { self.scheduleHide() }
                }
            }
        }
        showBar()
    }

    func stop() {
        stopped = true
        if let timeObserver { player?.removeTimeObserver(timeObserver) }
        timeObserver = nil
        if let endObserver { NotificationCenter.default.removeObserver(endObserver) }
        endObserver = nil
        rateObservation = nil
        [hideTask, refreshTask, breaksTask].forEach { $0?.cancel() }
    }

    // MARK: what the bar shows

    /// Length of the window: what the JSON lists, or more while a growing window is ahead of the last fetch.
    var duration: Double {
        let listed = timeline?.map.duration ?? 0
        guard timeline?.complete == false, let range = player?.currentItem?.seekableTimeRanges.last?.timeRangeValue else {
            return listed
        }
        return max(listed, CMTimeRangeGetEnd(range).seconds.isFinite ? CMTimeRangeGetEnd(range).seconds : 0)
    }

    var displayPosition: Double { scrubPosition ?? pendingSeek ?? position }

    func time(at position: Double) -> Date? { timeline?.map.time(at: position) }

    func programme(at position: Double) -> DVBTimeline.Programme? {
        guard let timeline, let time = timeline.map.time(at: position),
              let index = TimelineRules.programmeAt(time, in: timeline.programmes) else { return nil }
        return timeline.programmes[index]
    }

    /// Title = the programme at the playhead (in a gap the channel), second line channel · episode.
    func title(at position: Double) -> PlayerTitle {
        guard let programme = programme(at: position), !programme.title.isEmpty else { return PlayerTitle(title: channelName) }
        return .programme(programme.title, channel: channelName, episode: programme.subtitle)
    }

    /// "Pokračovat": the programme at the playhead (with an id) and the seconds since its start.
    func progress(at position: Double) -> (target: ResumeTarget, progress: TimeInterval)? {
        guard let programme = programme(at: position), let id = programme.id, let time = time(at: position) else { return nil }
        let target = ResumeTarget(key: ResumeRules.dvbKey(recordingID: id), programmeStart: programme.start,
                                  length: programme.length)
        return (target, TimelineRules.resumeProgress(time: time, programmeStart: programme.start))
    }

    private func tick(_ seconds: Double) {
        // Before the start position is applied the new item still reports 0.
        guard seconds.isFinite, !stopped, controller?.isStartApplied == true else { return }
        if !playbackStarted {
            // "Shown at the start": the 5 s count from the first picture, not from the screen opening.
            playbackStarted = true
            showBar()
        }
        currentPosition = seconds
        if barVisible || scrubPosition != nil { position = seconds }
        let title = title(at: seconds)
        if title != titleShown {
            titleShown = title
            controller?.updateTitle(title)
            if !barVisible { position = seconds }
        }
        updateBreaks(at: seconds)
    }

    // MARK: bar

    func showBar() {
        position = currentPosition
        if !barVisible { barVisible = true }
        scheduleHide()
    }

    private func scheduleHide() {
        hideTask?.cancel()
        hideTask = Task { [weak self] in
            try? await Task.sleep(for: Self.barSeconds)
            guard let self, !Task.isCancelled else { return }
            // Stays while paused, while a seek is chosen and across a window switch.
            if self.isPaused || self.scrubPosition != nil || self.switching { self.scheduleHide(); return }
            self.barVisible = false
        }
    }

    // MARK: keys

    func togglePlay() {
        guard let player else { return }
        if player.timeControlStatus == .paused { player.play() } else { player.pause() }
        showBar()
    }

    /// ← / → click: −10 s / +10 s.
    func step(_ sign: Double) {
        showBar()
        seek(to: displayPosition + sign * TimelineRules.stepSeconds)
    }

    /// While a left/right press is held: the playhead moves, nothing is seeked yet.
    func scrub(from base: Double, offset: Double) {
        scrubPosition = min(max(base + offset, -1), duration + 1)
        position = currentPosition
        showBar()
    }

    /// Released: seek to `base + offset` (the edges may open another window).
    func endScrub(from base: Double, offset: Double) {
        scrubPosition = nil
        seek(to: base + offset)
        showBar()
    }

    func cancelScrub() {
        scrubPosition = nil
    }

    /// Swipe on the touch surface: `fraction` of the surface width (signed).
    func swipeOffset(fraction: Double) -> Double { fraction * Self.swipeSeconds }

    /// ▲ / ▼: start of this programme / of the next one, through the map; outside the window the edge rule.
    func jump(_ direction: TimelineRules.Direction) {
        showBar()
        guard let timeline, let target = TimelineRules.jumpTarget(from: displayPosition, direction, map: timeline.map,
                                                                  programmes: timeline.programmes) else {
            timelineLog.info("jump \(direction.rawValue, privacy: .public): nothing there")
            return
        }
        timelineLog.info("jump \(direction.rawValue, privacy: .public) to \(target, format: .fixed(precision: 1))")
        seek(to: target)
    }

    func skipAd() {
        guard let target = skipTarget else { return }
        timelineLog.info("skip ad break to \(target, format: .fixed(precision: 1))")
        skipTarget = nil
        seek(to: target)
    }

    // MARK: seeking and edges

    func seek(to target: Double) {
        guard let timeline, !switching else { return }
        let duration = duration
        switch TimelineRules.edge(target: target, duration: duration, complete: timeline.complete,
                                  hasPrevious: timeline.previous != nil, hasNext: timeline.next != nil) {
        case .seek:
            seekPlayer(to: target)
        case .clamp:
            // The end of a finished window stops just before it; a growing one goes to its live edge.
            seekPlayer(to: target < 0 ? 0 : max(0, duration - (timeline.complete ? 1 : 0)))
        case .previous:
            if let id = timeline.previous, let first = timeline.map.startTime {
                openWindow(id, at: first.addingTimeInterval(target))
            }
        case .next:
            if let id = timeline.next, let last = timeline.map.endTime {
                openWindow(id, at: last.addingTimeInterval(max(0, target - timeline.map.duration)))
            }
        }
    }

    private func seekPlayer(to target: Double) {
        guard let item = player?.currentItem else { return }
        pendingSeek = target
        position = target
        seekGeneration += 1
        let generation = seekGeneration
        item.seek(to: CMTime(seconds: target, preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero) { [weak self] _ in
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    guard let self, generation == self.seekGeneration else { return }
                    self.pendingSeek = nil
                    self.currentPosition = item.currentTime().seconds
                    self.position = self.currentPosition
                }
            }
        }
    }

    private func reachedEnd() {
        guard let timeline else { return }
        timelineLog.info("window \(timeline.id, privacy: .public) played to its end")
        if timeline.complete, timeline.next != nil { seek(to: timeline.map.duration) } else { showBar() }
    }

    /// Opens `id`'s window and continues at `time` there. One switch at a time; the bar stays on screen.
    private func openWindow(_ id: String, at time: Date) {
        guard !switching, let controller else { return }
        switching = true
        showBar()
        timelineLog.info("open window \(id, privacy: .public) at \(time, privacy: .public)")
        Task {
            defer { switching = false; scheduleHide() }
            do {
                message = nil
                let window = try await client.timeline(id: id)
                let previousStart = startTime
                startTime = time
                let next: PlaybackSource
                do {
                    next = try await source(for: window)
                } catch {
                    startTime = previousStart
                    throw error
                }
                guard !stopped else { return }
                refreshTask?.cancel()
                breaksProgrammeID = nil
                windowBreaks = []
                skipTarget = nil
                apply(window)
                let start = next.startPosition ?? 0
                pendingSeek = start
                position = start
                controller.switchSource(to: next) { [weak self] in
                    guard let self else { throw CancellationError() }
                    return try await self.resolve()
                }
                // The new item applies the start position itself; the playhead waits there until it plays.
                pendingSeek = nil
                currentPosition = start
            } catch {
                timelineLog.error("window \(id, privacy: .public) failed: \(String(describing: error), privacy: .public)")
                guard !stopped else { return }
                message = AppModel.message(for: error)
                showBar()
                Task { [weak self] in
                    try? await Task.sleep(for: .seconds(8))
                    self?.message = nil
                }
            }
        }
    }

    // MARK: growing window

    private func startRefreshing() {
        refreshTask?.cancel()
        refreshTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(DVBTimeline.refreshInterval))
                guard let self, !Task.isCancelled, let id = self.timeline?.id else { return }
                do {
                    let window = try await self.client.timeline(id: id)
                    guard !Task.isCancelled, window.id == self.timeline?.id else { return }
                    self.timeline = window
                    if window.complete { return }
                } catch {
                    timelineLog.info("refresh failed: \(String(describing: error), privacy: .public)")
                }
            }
        }
    }

    // MARK: "Přeskočit reklamu"

    /// Breaks of the programme at the playhead (finished, with an id and stored breaks), fetched when it changes
    /// and every minute while playing; failures are silent.
    private func updateBreaks(at position: Double) {
        let programme = programme(at: position)
        let eligible = programme.flatMap { $0.breaks && $0.end <= Date() ? $0.id : nil }
        if eligible != breaksProgrammeID {
            breaksProgrammeID = eligible
            windowBreaks = []
            breaksTask?.cancel()
            if let id = eligible, let programme { fetchBreaks(id: id, programmeStart: programme.start) }
        }
        let target = AdBreaks.skipTarget(windowBreaks, position: position)
        if target != skipTarget { skipTarget = target }
    }

    private func fetchBreaks(id: String, programmeStart: Date) {
        breaksTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                do {
                    let breaks = try await self.client.adBreaks(recordingID: id)
                    guard !Task.isCancelled, let map = self.timeline?.map else { return }
                    self.windowBreaks = TimelineRules.windowBreaks(breaks, programmeStart: programmeStart, map: map)
                    timelineLog.info("ad breaks of \(id, privacy: .public): \(breaks.count)")
                } catch {
                    timelineLog.info("ad breaks unavailable: \(String(describing: error), privacy: .public)")
                }
                try? await Task.sleep(for: .seconds(60))
                while !Task.isCancelled, self.player?.timeControlStatus != .playing {
                    try? await Task.sleep(for: .seconds(5))
                }
            }
        }
    }
}
