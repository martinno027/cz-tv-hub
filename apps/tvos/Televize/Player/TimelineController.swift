import AVFoundation
import CTCore
import QuartzCore
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
    /// A seek being chosen (rewind / fast-forward moving, a swipe): the playhead and its label follow it, the video
    /// seeks on the stop / release.
    private(set) var scrubPosition: Double?
    /// The bar's `◀◀` / `▶▶ 2` while rewind / fast-forward moves (`TrickPlay.badge`).
    private(set) var trickBadge: String?
    private(set) var barVisible = true
    private(set) var isPaused = false
    /// Where "Přeskočit reklamu" seeks to, while it is offered.
    private(set) var skipTarget: Double?
    /// The gateway's Czech text after a failed switch to `previous` / `next` (the old window keeps playing).
    private(set) var message: String?
    let channelName: String

    @ObservationIgnored private let client: DVBClient
    /// What the Archiv row shows for the programme the player was opened for (its show's title and image).
    @ObservationIgnored private let firstCard: ResumeCard?
    @ObservationIgnored private weak var controller: PlayerController?
    @ObservationIgnored private var player: AVPlayer? { controller?.player }
    /// Wall clock the next resolve starts at (the programme start − 30 s, "Pokračovat", or a window hand-off).
    @ObservationIgnored private var startTime: Date
    @ObservationIgnored private var firstID: String
    /// A seek on its way: the playhead stays there until the player has arrived. Observed like `position`:
    /// while it is set the bar does not read `position`, so clearing it must redraw the bar.
    private var pendingSeek: Double?
    /// When the player seek behind `pendingSeek` was asked for; nil while a window switch owns `pendingSeek`. The
    /// target stays on the bar until the player is within 3 s of it, at most 10 s (`showsSeekTarget`, T55).
    @ObservationIgnored private var pendingSince: Date?
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
    /// Rewind / fast-forward (spec/apps.md "Keys", T55); times are `CACurrentMediaTime()`.
    @ObservationIgnored private var trick = TrickPlay()
    /// Where the movement started (window and wall clock): Back returns there, after crossing into another window
    /// by opening the start window again.
    @ObservationIgnored private var trickOriginTime: Date?
    @ObservationIgnored private var trickOriginWindow: String?
    @ObservationIgnored private var trickBegan: CFTimeInterval = 0
    @ObservationIgnored private var trickFrames = 0
    /// The paused picture following the moving playhead: when the last fast seek was asked for, whether it runs.
    @ObservationIgnored private var followAt: CFTimeInterval?
    @ObservationIgnored private var followInFlight = false
    @ObservationIgnored private var followSeeks = 0

    static let barSeconds: Duration = .seconds(5)
    /// A full swipe across the touch surface scrubs about this far.
    static let swipeSeconds: Double = 600

    init(client: DVBClient, programmeID: String, startTime: Date, channelName: String, firstCard: ResumeCard? = nil) {
        self.client = client
        self.firstCard = firstCard
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

    /// The watch report of the window now playing (spec/dvb-gateway.md "Watch log (T53)"): its id and channel, the
    /// programme at the start time.
    var watchStart: WatchStart? {
        guard let timeline else { return nil }
        let title = TimelineRules.programmeAt(startTime, in: timeline.programmes).map { timeline.programmes[$0].title }
        return WatchStart(.dvb(.timeline, recordingID: timeline.id, channelID: timeline.channel, title: title))
    }

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
        // the opened programme with what the archive showed, the neighbours with their own title and still
        let card = firstCard != nil && id == firstID ? firstCard : .programme(
            title: programme.title, episode: programme.subtitle,
            image: ArchiveImage.thumbnail(recordingID: id).stored, source: .dvb, start: programme.start,
            channel: channelName)
        let target = ResumeTarget(key: ResumeRules.dvbKey(recordingID: id), programmeStart: programme.start,
                                  length: programme.length, card: card)
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
        if let target = pendingSeek, let since = pendingSince,
           !TimelineRules.showsSeekTarget(target, position: seconds, elapsed: Date().timeIntervalSince(since)) {
            clearPendingSeek()
        }
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

    /// Play/pause; while rewind / fast-forward moves it stops there and plays.
    func togglePlay() {
        if trickOK() { return }
        guard let player else { return }
        if player.timeControlStatus == .paused { player.play() } else { player.pause() }
        showBar()
    }

    /// ← / → click: −10 s / +10 s.
    func step(_ sign: Double) {
        showBar()
        seek(to: displayPosition + sign * TimelineRules.stepSeconds, kind: .step)
    }

    // MARK: rewind / fast-forward

    /// A press is waiting to become a hold, or the playhead moves: the host's display link calls `trickFrame`.
    var needsFrames: Bool { trick.isMoving || pressDown }
    @ObservationIgnored private var pressDown = false
    var isTrickMoving: Bool { trick.isMoving }

    /// ← / → down (`sign` −1 / 1). With the bar hidden it shows it: a click is only that, a hold moves at once.
    func trickKeyDown(_ sign: Int) {
        let now = CACurrentMediaTime()
        let barHidden = !barVisible
        showBar()
        pressDown = !trick.isMoving
        handle(trick.keyDown(sign, position: trickPosition(at: now), at: now, barHidden: barHidden), at: now)
    }

    /// ← / → up; `cancelled` (the system took the press) is neither a click nor a hold.
    func trickKeyUp(_ sign: Int, cancelled: Bool) {
        let now = CACurrentMediaTime()
        pressDown = false
        if cancelled {
            trick.cancelPress()
            return
        }
        handle(trick.keyUp(sign, at: now), at: now)
    }

    /// ⏪ / ⏩ (media keys): start at speed 1, or ± 1 while moving.
    func trickMedia(_ sign: Int) {
        let now = CACurrentMediaTime()
        showBar()
        handle(trick.media(sign, position: trickPosition(at: now), at: now), at: now)
    }

    /// OK / play-pause while moving: stop and play from the playhead. False when not moving (the key does its own).
    func trickOK() -> Bool { trickStop { $0.ok() } }

    /// Back while moving: stop and return to where it started (playing). False when not moving (Back closes).
    func trickBack() -> Bool { trickStop { $0.back() } }

    private func trickStop(_ event: (inout TrickPlay) -> TrickPlay.Action) -> Bool {
        guard trick.isMoving else { return false }
        let now = CACurrentMediaTime()
        let here = trickPosition(at: now)
        finishTrick(event(&trick), at: here)
        return true
    }

    /// The playhead now: the moving one, else what the bar shows.
    private func trickPosition(at time: CFTimeInterval) -> Double { trick.position(at: time) ?? displayPosition }

    /// Every display frame while `needsFrames`: a press held 0.5 s starts moving; a moving playhead goes on, opens
    /// the neighbour at an edge (moving on there) or stops at an edge without one, and the paused picture follows.
    func trickFrame(_ now: CFTimeInterval) {
        let action = trick.update(at: now)
        if action != .none {
            pressDown = false
            handle(action, at: now)
        }
        guard trick.isMoving, !switching, let timeline, let target = trick.position(at: now) else { return }
        trickFrames += 1
        let duration = duration
        switch TimelineRules.edge(target: target, duration: duration, complete: timeline.complete,
                                  hasPrevious: timeline.previous != nil, hasNext: timeline.next != nil) {
        case .seek:
            break
        case .clamp:
            // No neighbour, or a growing window's live edge: stop there and play.
            timelineLog.info("trick reached the window's \(target < 0 ? "start" : "end", privacy: .public)")
            finishTrick(trick.interrupt(.playHere), at: target)
            return
        case .previous, .next:
            // Moves on in the neighbour (openWindow rebases the movement there).
            timelineLog.info("trick reached the window's edge, opening the neighbour")
            seek(to: target, kind: .trick)
            return
        }
        if target != scrubPosition { scrubPosition = target }
        guard let player, let item = player.currentItem, controller?.isStartApplied == true,
              item.status == .readyToPlay else { return }
        // The video stays paused while it moves (a window switch's new item starts playing by itself).
        if player.rate != 0 { player.pause() }
        if TimelineRules.picturesFollow(last: followAt, now: now, inFlight: followInFlight) { follow(item, to: target) }
    }

    private func follow(_ item: AVPlayerItem, to target: Double) {
        followAt = CACurrentMediaTime()
        followInFlight = true
        followSeeks += 1
        let began = CACurrentMediaTime()
        let margin = CMTime(seconds: TimelineRules.seekTolerance(.follow), preferredTimescale: 600)
        item.seek(to: CMTime(seconds: target, preferredTimescale: 600), toleranceBefore: margin, toleranceAfter: margin) { [weak self] finished in
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    guard let self else { return }
                    self.followInFlight = false
                    let ms = Int((CACurrentMediaTime() - began) * 1000)
                    timelineLog.debug("trick picture to \(target, format: .fixed(precision: 1)): \(finished ? "done" : "not finished", privacy: .public) in \(ms) ms at \(item.currentTime().seconds, format: .fixed(precision: 1))")
                }
            }
        }
    }

    private func handle(_ action: TrickPlay.Action, at now: CFTimeInterval) {
        switch action {
        case .none:
            break
        case .step(let seconds):
            // 0: the click only showed the bar.
            if seconds != 0 { step(seconds < 0 ? -1 : 1) } else { timelineLog.info("click with the bar hidden: only shows it") }
        case .started:
            trickBegan = now
            trickFrames = 0
            followSeeks = 0
            followAt = nil
            trickOriginTime = trick.origin.flatMap { time(at: $0) }
            trickOriginWindow = timeline?.id
            clearPendingSeek()
            player?.pause()
            trickBadge = trick.badge
            scrubPosition = trick.position(at: now)
            showBar()
            timelineLog.info("trick \(self.trick.badge ?? "", privacy: .public) from \(self.trick.origin ?? 0, format: .fixed(precision: 1))")
        case .changed:
            trickBadge = trick.badge
            timelineLog.info("trick \(self.trick.badge ?? "", privacy: .public)")
        case .stopped:
            break  // finishTrick does the seek
        }
    }

    /// The movement stopped with the playhead at `here`: one exact seek (through the edge rule) and play. While a
    /// neighbouring window opens nothing is seeked: the new window plays where it opened.
    private func finishTrick(_ action: TrickPlay.Action, at here: Double) {
        guard case .stopped(let kind) = action else { return }
        trickBadge = nil
        let seconds = CACurrentMediaTime() - trickBegan
        timelineLog.info("trick stopped (\(kind.rawValue, privacy: .public)) at \(here, format: .fixed(precision: 1)) after \(seconds, format: .fixed(precision: 1)) s, \(self.trickFrames) frames, \(self.followSeeks) picture seeks\(self.switching ? " while a window opens" : "", privacy: .public)")
        if kind != .none { scrubPosition = nil }
        showBar()
        // The new item starts playing by itself once it has its start position.
        if switching { return }
        switch kind {
        case .playHere:
            seek(to: here, kind: .trick)
        case .return:
            if let id = trickOriginWindow, id != timeline?.id, let time = trickOriginTime {
                // Crossed into another window: open the one it started in, at the start position.
                openWindow(id, at: time)
            } else {
                let back = trickOriginTime.flatMap { timeline?.map.unclampedPosition(at: $0) } ?? trick.origin ?? here
                seek(to: back, kind: .trick)
            }
        case .none:
            break  // the caller seeks (▲ / ▼); the playhead stays at `here` until then
        }
        player?.play()
    }

    // MARK: swipe

    /// While a swipe moves: the playhead moves, nothing is seeked yet. No redraw when nothing changed.
    func scrub(from base: Double, offset: Double) {
        let target = min(max(base + offset, -1), duration + 1)
        if !barVisible { showBar() }
        if target != scrubPosition { scrubPosition = target }
    }

    /// Swipe released: a fast seek to `base + offset` (the edges may open another window); the playhead stays at the
    /// target until the player is there.
    func endScrub(from base: Double, offset: Double) {
        // The target takes over before the scrub ends, so the bar never shows the old position in between.
        seek(to: base + offset, kind: .scrub)
        scrubPosition = nil
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
        // While rewind / fast-forward moves: stop, then jump from the playhead (nothing there = play here).
        var stoppedAt: Double?
        if trick.isMoving {
            let here = trickPosition(at: CACurrentMediaTime())
            scrubPosition = here
            finishTrick(trick.interrupt(), at: here)
            stoppedAt = here
        }
        defer { if stoppedAt != nil { scrubPosition = nil } }
        guard let timeline, let target = TimelineRules.jumpTarget(from: displayPosition, direction, map: timeline.map,
                                                                  programmes: timeline.programmes) else {
            timelineLog.info("jump \(direction.rawValue, privacy: .public): nothing there")
            if let stoppedAt { seek(to: stoppedAt, kind: .trick) }
            return
        }
        timelineLog.info("jump \(direction.rawValue, privacy: .public) to \(target, format: .fixed(precision: 1))")
        seek(to: target, kind: .jump)
    }

    func skipAd() {
        guard let target = skipTarget else { return }
        timelineLog.info("skip ad break to \(target, format: .fixed(precision: 1))")
        skipTarget = nil
        seek(to: target, kind: .skip)
    }

    // MARK: seeking and edges

    /// `kind` decides the tolerance (spec/apps.md "Fast seek"): a scrub lands on the nearest keyframe within ±2 s,
    /// a click, a jump and "Přeskočit reklamu" exactly.
    func seek(to target: Double, kind: TimelineRules.SeekKind) {
        guard let timeline, !switching else { return }
        let duration = duration
        switch TimelineRules.edge(target: target, duration: duration, complete: timeline.complete,
                                  hasPrevious: timeline.previous != nil, hasNext: timeline.next != nil) {
        case .seek:
            seekPlayer(to: target, tolerance: TimelineRules.seekTolerance(kind))
        case .clamp:
            // The end of a finished window stops just before it (exactly: a tolerance could land past the end);
            // a growing one goes to its live edge.
            if target < 0 {
                seekPlayer(to: 0, tolerance: 0)
            } else {
                seekPlayer(to: max(0, duration - (timeline.complete ? 1 : 0)), tolerance: 0)
            }
        case .previous:
            if let id = timeline.previous, let first = timeline.map.startTime {
                // The playhead waits at the window's start (the map clamps) while the neighbour loads.
                pendingSeek = target
                pendingSince = nil
                openWindow(id, at: first.addingTimeInterval(target))
            }
        case .next:
            if let id = timeline.next, let last = timeline.map.endTime {
                pendingSeek = target
                pendingSince = nil
                openWindow(id, at: last.addingTimeInterval(max(0, target - timeline.map.duration)))
            }
        }
    }

    private func seekPlayer(to target: Double, tolerance: Double) {
        guard let item = player?.currentItem else { return }
        let began = Date()
        pendingSeek = target
        pendingSince = began
        position = target
        seekGeneration += 1
        let generation = seekGeneration
        let margin = CMTime(seconds: tolerance, preferredTimescale: 600)
        item.seek(to: CMTime(seconds: target, preferredTimescale: 600), toleranceBefore: margin, toleranceAfter: margin) { [weak self] finished in
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    guard let self, generation == self.seekGeneration else { return }
                    self.currentPosition = item.currentTime().seconds
                    self.position = self.currentPosition
                    let elapsed = Date().timeIntervalSince(began)
                    let ms = Int(elapsed * 1000)
                    timelineLog.info("seek to \(target, format: .fixed(precision: 1)) ±\(tolerance, format: .fixed(precision: 0)) s: \(finished ? "done" : "not finished", privacy: .public) in \(ms) ms at \(self.currentPosition, format: .fixed(precision: 1))")
                    // An interrupted seek never gets there; else the target stays until the player is within 3 s
                    // (the ticks check), at most 10 s (also while paused, when no tick comes).
                    if !finished || !TimelineRules.showsSeekTarget(target, position: self.currentPosition, elapsed: elapsed) {
                        self.clearPendingSeek()
                    } else {
                        self.expirePendingSeek(generation, after: TimelineRules.targetShownMaxSeconds - elapsed)
                    }
                }
            }
        }
    }

    private func clearPendingSeek() {
        pendingSeek = nil
        pendingSince = nil
    }

    private func expirePendingSeek(_ generation: Int, after seconds: Double) {
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(max(0, seconds)))
            guard let self, generation == self.seekGeneration, self.pendingSince != nil else { return }
            timelineLog.info("seek target not reached in \(TimelineRules.targetShownMaxSeconds, format: .fixed(precision: 0)) s, at \(self.currentPosition, format: .fixed(precision: 1))")
            self.position = self.currentPosition
            self.clearPendingSeek()
        }
    }

    private func reachedEnd() {
        guard let timeline, !trick.isMoving else { return }
        timelineLog.info("window \(timeline.id, privacy: .public) played to its end")
        if timeline.complete, timeline.next != nil { seek(to: timeline.map.duration, kind: .jump) } else { showBar() }
    }

    /// Opens `id`'s window and continues at `time` there. One switch at a time; the bar stays on screen.
    private func openWindow(_ id: String, at time: Date) {
        guard !switching, let controller else {
            clearPendingSeek()
            return
        }
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
                pendingSince = nil
                position = start
                controller.switchSource(to: next) { [weak self] in
                    guard let self else { throw CancellationError() }
                    return try await self.resolve()
                }
                // Another window = a new watch session.
                controller.restartWatch(watchStart)
                // The new item applies the start position itself; the playhead waits there until it plays.
                clearPendingSeek()
                currentPosition = start
                // Rewind / fast-forward moves on in the new window from where it enters it.
                if trick.isMoving {
                    trick.rebase(position: start, at: CACurrentMediaTime())
                    scrubPosition = start
                    followAt = nil
                }
            } catch {
                timelineLog.error("window \(id, privacy: .public) failed: \(String(describing: error), privacy: .public)")
                guard !stopped else { return }
                // The old window keeps playing: the playhead goes back to it.
                clearPendingSeek()
                position = currentPosition
                if trick.isMoving {
                    // Stops at the edge it reached and plays there (directly: `seek` waits while `switching`).
                    let edge = trick.direction < 0 ? 0 : max(0, duration - 1)
                    timelineLog.info("trick stopped at the window's edge: the neighbour failed")
                    _ = trick.interrupt()
                    trickBadge = nil
                    scrubPosition = nil
                    seekPlayer(to: edge, tolerance: 0)
                    player?.play()
                }
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
