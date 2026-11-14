import AVFoundation
import CTCore
import QuartzCore
import Observation
import os

private let timelineLog = Logger(subsystem: "cz.cztvhub.tv", category: "timeline")

/// The timeline player (spec/apps.md "Anténa timeline player", T05, and "ČT player bar", T62): the own bar and keys
/// over a window of a `TimelineWindowSource` (an Anténa window of the channel's recording, or the one window of a ČT
/// start-over / archive playback). Maps player positions to wall clock through the window, seeks (clicks, held keys,
/// swipes, ▲/▼ jumps), keeps ČT seeks inside the window's limits, opens the previous/next Anténa window at the edges,
/// offers "Přeskočit reklamu" for the programme at the playhead and tells the player what to remember for
/// "Pokračovat". The `PlayerController` keeps loading, re-resolving after failures, diagnostics and audio.
@MainActor
@Observable
final class TimelineController {
    /// The window the bar shows; nil until the source knows it (ČT: once the stream is ready).
    private(set) var window: BarWindow?
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

    @ObservationIgnored private let source: TimelineWindowSource
    @ObservationIgnored private weak var controller: PlayerController?
    @ObservationIgnored private var player: AVPlayer? { controller?.player }
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

    init(source: TimelineWindowSource) {
        self.source = source
        self.channelName = source.channelName
        source.onChange = { [weak self] in self?.refreshWindow() }
    }

    // MARK: resolving

    /// The source's stream (Anténa: the window's JSON and playlist, starting where it was asked to). Also the player's
    /// re-resolve after a failure.
    func resolve() async throws -> PlaybackSource {
        let next = try await source.resolve()
        refreshWindow()
        return next
    }

    /// The source's window with the player as it is now; redraws only when it changed.
    private func refreshWindow() {
        let next = source.window(item: player?.currentItem, now: Date())
        if next != window { window = next }
    }

    // MARK: lifecycle

    func attach(_ controller: PlayerController) {
        self.controller = controller
        // A re-resolve after a failure resumes at the playhead where the source says so (ČT); a movement ends there.
        controller.resumeOverride = { [weak self] in self?.resumeForReload() }
        // The own list offers subtitles; they start off (T62, all platforms alike).
        controller.subtitlesStartOff = true
        refreshWindow()
    }

    private func resumeForReload() -> PlayerController.Resume? {
        guard let window else { return nil }
        let here = trickPosition(at: CACurrentMediaTime())
        guard let resume = source.resume(at: here, in: window) else { return nil }
        if trick.isMoving {
            timelineLog.info("trick stopped: the stream is resolved again")
            _ = trick.interrupt()
            trickBadge = nil
            scrubPosition = nil
            pressDown = false
        }
        clearPendingSeek()
        position = here
        return resume
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
        [hideTask, breaksTask].forEach { $0?.cancel() }
        source.stop()
    }

    // MARK: what the bar shows

    /// Length of the window: what the JSON lists, or more while a growing window is ahead of the last fetch.
    var duration: Double {
        let listed = window?.map.duration ?? 0
        guard window?.complete == false, let range = player?.currentItem?.seekableTimeRanges.last?.timeRangeValue else {
            return listed
        }
        return max(listed, CMTimeRangeGetEnd(range).seconds.isFinite ? CMTimeRangeGetEnd(range).seconds : 0)
    }

    var displayPosition: Double { scrubPosition ?? pendingSeek ?? position }

    func time(at position: Double) -> Date? { window?.map.time(at: position) }

    /// The watch report of the window now playing (spec/dvb-gateway.md "Watch log (T53)"); nil = the player's own.
    var watchStart: WatchStart? { source.watchStart }

    func programme(at position: Double) -> DVBTimeline.Programme? {
        guard let window, let time = window.map.time(at: position),
              let index = TimelineRules.programmeAt(time, in: window.programmes) else { return nil }
        return window.programmes[index]
    }

    /// Title = the programme at the playhead (in a gap the channel), second line channel · episode.
    func title(at position: Double) -> PlayerTitle {
        guard let programme = programme(at: position), !programme.title.isEmpty else { return PlayerTitle(title: channelName) }
        return .programme(programme.title, channel: channelName, episode: programme.subtitle)
    }

    /// "Pokračovat": the programme at the playhead (with an id) and the seconds since its start.
    func progress(at position: Double) -> (target: ResumeTarget, progress: TimeInterval)? {
        guard let window else { return nil }
        return source.progress(at: position, in: window)
    }

    private func tick(_ seconds: Double) {
        // Before the start position is applied the new item still reports 0.
        guard seconds.isFinite, !stopped, controller?.isStartApplied == true else { return }
        currentPosition = seconds
        refreshWindow()
        if !playbackStarted {
            // "Shown at the start": the 5 s count from the first picture, not from the screen opening.
            playbackStarted = true
            showBar()
            // A ČT window's start position is kept inside its limits too (start-over of a programme that began near
            // the buffer's start: not in the oldest minute).
            if let window, let range = window.positionLimits(now: Date()), !range.contains(seconds) {
                timelineLog.info("start position \(seconds, format: .fixed(precision: 1)) outside the limits")
                seek(to: window.clamped(seconds, now: Date()), kind: .jump)
            }
        }
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
        if player.timeControlStatus == .paused {
            // A timeshift playhead left below the lower clamp by a long pause goes to the clamp first.
            if let window, let range = window.positionLimits(now: Date()), displayPosition < range.lowerBound {
                timelineLog.info("resume below the lower clamp: to \(range.lowerBound, format: .fixed(precision: 1))")
                seek(to: range.lowerBound, kind: .jump)
            }
            player.play()
        } else {
            player.pause()
        }
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
        guard trick.isMoving, !switching, let window, let target = trick.position(at: now) else { return }
        trickFrames += 1
        switch edge(target, in: window) {
        case .seek:
            break
        case .clamp:
            // No neighbour, a growing window's live edge, a ČT limit: stop there and play.
            timelineLog.info("trick reached the window's \(self.trick.direction < 0 ? "start" : "end", privacy: .public)")
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
            trickOriginWindow = window?.id
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
            if let id = trickOriginWindow, id != window?.id, let time = trickOriginTime {
                // Crossed into another window: open the one it started in, at the start position.
                openWindow(id, at: time)
            } else {
                let back = trickOriginTime.flatMap { window?.map.unclampedPosition(at: $0) } ?? trick.origin ?? here
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
        let range = window?.positionLimits(now: Date()) ?? -1...(duration + 1)
        let target = min(max(base + offset, range.lowerBound), range.upperBound)
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
        guard let window, let target = TimelineRules.jumpTarget(from: displayPosition, direction, map: window.map,
                                                                programmes: window.programmes) else {
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
        guard let window, !switching else { return }
        let duration = duration
        switch edge(target, in: window) {
        case .seek:
            seekPlayer(to: target, tolerance: TimelineRules.seekTolerance(kind))
        case .clamp:
            // The end of a finished window stops just before it (exactly: a tolerance could land past the end);
            // a growing one goes to its live edge; a ČT window to its limit.
            if window.positionLimits(now: Date()) != nil {
                seekPlayer(to: window.clamped(target, now: Date()), tolerance: 0)
            } else if target < 0 {
                seekPlayer(to: 0, tolerance: 0)
            } else {
                seekPlayer(to: max(0, duration - (window.complete ? 1 : 0)), tolerance: 0)
            }
        case .previous:
            if let id = window.previous, let first = window.map.startTime {
                // The playhead waits at the window's start (the map clamps) while the neighbour loads.
                pendingSeek = target
                pendingSince = nil
                openWindow(id, at: first.addingTimeInterval(target))
            }
        case .next:
            if let id = window.next, let last = window.map.endTime {
                pendingSeek = target
                pendingSince = nil
                openWindow(id, at: last.addingTimeInterval(max(0, target - window.map.duration)))
            }
        }
    }

    /// The edge rule of an Anténa window; a ČT window (T62) has limits instead (clamp outside them, sliding with now).
    private func edge(_ target: Double, in window: BarWindow) -> TimelineRules.Edge {
        if let range = window.positionLimits(now: Date()) { return range.contains(target) ? .seek : .clamp }
        return TimelineRules.edge(target: target, duration: duration, complete: window.complete,
                                  hasPrevious: window.previous != nil, hasNext: window.next != nil)
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
        guard let window, !trick.isMoving else { return }
        timelineLog.info("window \(window.id, privacy: .public) played to its end")
        // A ČT archive item stays at its end with the bar, as the native player stayed on its last picture before
        // T62 (checked in the simulator 2026-11-14: AVPlayerViewController did not close).
        if window.complete, window.next != nil { seek(to: window.map.duration, kind: .jump) } else { showBar() }
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
                let next = try await source.openNeighbour(id, at: time)
                guard !stopped else { return }
                breaksProgrammeID = nil
                windowBreaks = []
                skipTarget = nil
                refreshWindow()
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
                    let breaks = try await self.source.adBreaks(programmeID: id)
                    guard !Task.isCancelled, let map = self.window?.map else { return }
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
