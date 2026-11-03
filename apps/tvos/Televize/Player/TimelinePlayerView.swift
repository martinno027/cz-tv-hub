import AVKit
import CTCore
import MediaPlayer
import SwiftUI

/// The video of a timeline playback without AVKit's controls; keys and swipes go to `TimelineController`.
struct TimelinePlayerView: UIViewControllerRepresentable {
    let controller: PlayerController
    let timeline: TimelineController

    func makeUIViewController(context: Context) -> TimelineHostViewController {
        let host = TimelineHostViewController(timeline: timeline, controller: controller)
        host.player.player = controller.player
        return host
    }

    func updateUIViewController(_ host: TimelineHostViewController, context: Context) {
        if host.player.player !== controller.player { host.player.player = controller.player }
    }
}

/// A focusable view, so the remote's presses reach the host controller (nothing else on screen takes focus).
private final class FocusableView: UIView {
    override var canBecomeFocused: Bool { true }
}

/// Keys of the timeline player (spec/apps.md "Anténa timeline player"): OK and play/pause pause, a ← / → click
/// steps 10 s, held 0.5 s it starts rewind / fast-forward (`TrickPlay`: more clicks change the speed, OK plays
/// there, Back returns), ⏪ / ⏩ media commands do the same, a horizontal swipe scrubs, ▲ / ▼ jump between
/// programmes, a long OK press opens the audio list (with „Vypnout zvuk“ / „Zapnout zvuk“), Back closes the player.
/// While the bar is hidden the first key only shows it, except play/pause, Back, the long OK, ⏪ / ⏩, a held ← / →
/// (they show it and act at once) and OK while "Přeskočit reklamu" is shown.
final class TimelineHostViewController: UIViewController, UIGestureRecognizerDelegate {
    let player = AVPlayerViewController()
    private let timeline: TimelineController
    private let controller: PlayerController

    /// The sign of the ← / → press down (key repeats of it are ignored).
    private var arrow: Int?
    /// Drives `TimelineController.trickFrame` while a press may become a hold or the playhead moves (spec/apps.md
    /// "Keys", T55: the playhead moves every frame, not in key-repeat steps).
    private var frameLink: CADisplayLink?
    /// The Menu press down began while rewind / fast-forward moved: `backRecognizer` takes it instead of closing the
    /// player (set on every Menu key-down).
    private var menuConsumed = false
    /// Back while moving (spec/apps.md "Keys", T55). A recognizer, not only the responder chain: the presentation
    /// closes the player on Menu through a recognizer of its own (seen in the simulator: consuming the press in
    /// `pressesBegan`/`Ended` still closed it). The others wait for this one to fail.
    private lazy var backRecognizer: UITapGestureRecognizer = {
        let recognizer = UITapGestureRecognizer(target: self, action: #selector(backWhileMoving))
        recognizer.allowedPressTypes = [NSNumber(value: UIPress.PressType.menu.rawValue)]
        recognizer.allowedTouchTypes = []
        recognizer.delegate = self
        return recognizer
    }()
    private var mediaTargets: [(MPRemoteCommand, Any)] = []
    private var selectBegan: Date?
    private var selectTimer: Timer?
    private var selectConsumed = false
    /// A short OK with the bar hidden only shows it (unless "Přeskočit reklamu" is offered).
    private var selectShowsBar = false
    private var swipeBase: Double?

    static let longPressSeconds: TimeInterval = 0.6

    init(timeline: TimelineController, controller: PlayerController) {
        self.timeline = timeline
        self.controller = controller
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not used") }

    override func loadView() {
        view = FocusableView()
        view.backgroundColor = .black
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        player.showsPlaybackControls = false
        addChild(player)
        player.view.frame = view.bounds
        player.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        player.view.isUserInteractionEnabled = false
        view.addSubview(player.view)
        player.didMove(toParent: self)
        let pan = UIPanGestureRecognizer(target: self, action: #selector(swiped(_:)))
        pan.allowedTouchTypes = [NSNumber(value: UITouch.TouchType.indirect.rawValue)]
        view.addGestureRecognizer(pan)
        view.addGestureRecognizer(backRecognizer)
    }

    @objc private func backWhileMoving() {
        _ = timeline.trickBack()
    }

    func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
        gestureRecognizer === backRecognizer ? menuConsumed : true
    }

    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                           shouldBeRequiredToFailBy other: UIGestureRecognizer) -> Bool {
        // Only the other Menu recognizers wait (all of them would hold up the arrows too).
        gestureRecognizer === backRecognizer && other !== backRecognizer
            && other.allowedPressTypes.contains(NSNumber(value: UIPress.PressType.menu.rawValue))
    }

    override var preferredFocusEnvironments: [UIFocusEnvironment] { [view] }

    // MARK: presses

    override func pressesBegan(_ presses: Set<UIPress>, with event: UIPressesEvent?) {
        var rest = Set<UIPress>()
        for press in presses {
            switch press.type {
            case .leftArrow: arrowDown(-1)
            case .rightArrow: arrowDown(1)
            case .upArrow: arrowJump(.up)
            case .downArrow: arrowJump(.down)
            case .select: beginSelect()
            case .playPause: timeline.togglePlay()
            case .menu where timeline.isTrickMoving:
                menuConsumed = true  // `backRecognizer` stops it on the release
            case .menu:
                menuConsumed = false
                rest.insert(press)
            default: rest.insert(press)
            }
        }
        if !rest.isEmpty { super.pressesBegan(rest, with: event) }
    }

    override func pressesEnded(_ presses: Set<UIPress>, with event: UIPressesEvent?) {
        var rest = Set<UIPress>()
        for press in presses {
            switch press.type {
            case .leftArrow: arrowUp(-1, cancelled: false)
            case .rightArrow: arrowUp(1, cancelled: false)
            case .select: endSelect()
            case .upArrow, .downArrow, .playPause: break
            case .menu where menuConsumed:
                break  // reset by the next Menu press: the recognizer may still ask
            default: rest.insert(press)
            }
        }
        if !rest.isEmpty { super.pressesEnded(rest, with: event) }
    }

    override func pressesCancelled(_ presses: Set<UIPress>, with event: UIPressesEvent?) {
        var rest = Set<UIPress>()
        for press in presses {
            switch press.type {
            case .leftArrow: arrowUp(-1, cancelled: true)
            case .rightArrow: arrowUp(1, cancelled: true)
            case .select:
                selectTimer?.invalidate()
                selectBegan = nil
            case .upArrow, .downArrow, .playPause: break
            case .menu where menuConsumed:
                break  // reset by the next Menu press: the recognizer may still ask
            default: rest.insert(press)
            }
        }
        if !rest.isEmpty { super.pressesCancelled(rest, with: event) }
    }

    /// With the bar hidden a press shows it: a click only shows it, a hold starts moving at once (spec/apps.md
    /// "Keys", T55; `TrickPlay`).
    private func arrowDown(_ sign: Int) {
        guard arrow == nil else { return }  // key repeat of the same press
        arrow = sign
        timeline.trickKeyDown(sign)
        startFrames()
    }

    private func arrowUp(_ sign: Int, cancelled: Bool) {
        guard arrow == sign else { return }
        arrow = nil
        timeline.trickKeyUp(sign, cancelled: cancelled)
    }

    private func startFrames() {
        guard frameLink == nil, timeline.needsFrames else { return }
        // The display link retains its target: a proxy keeps this controller free; `frame` / `viewWillDisappear`
        // invalidate it.
        let link = CADisplayLink(target: DisplayLinkProxy(self), selector: #selector(DisplayLinkProxy.frame(_:)))
        link.add(to: .main, forMode: .common)
        frameLink = link
    }

    /// One frame: `targetTimestamp` = when this frame will be on screen.
    fileprivate func frame(_ link: CADisplayLink) {
        timeline.trickFrame(link.targetTimestamp)
        if !timeline.needsFrames { stopFrames() }
    }

    private func stopFrames() {
        frameLink?.invalidate()
        frameLink = nil
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        addMediaCommands()
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        stopFrames()
        arrow = nil
        removeMediaCommands()
    }

    /// ⏪ / ⏩ of remotes that have them (they arrive as remote seek commands; the Siri Remote has none): a press =
    /// start (speed 1) / ± 1, as the clicks while moving.
    private func addMediaCommands() {
        guard mediaTargets.isEmpty else { return }
        let center = MPRemoteCommandCenter.shared()
        for (command, sign) in [(center.seekBackwardCommand, -1), (center.seekForwardCommand, 1)] {
            let target = command.addTarget { [weak self] event in
                guard let self, (event as? MPSeekCommandEvent)?.type != .endSeeking else { return .success }
                self.timeline.trickMedia(sign)
                self.startFrames()
                return .success
            }
            mediaTargets.append((command, target))
        }
    }

    private func removeMediaCommands() {
        for (command, target) in mediaTargets { command.removeTarget(target) }
        mediaTargets = []
    }

    private func arrowJump(_ direction: TimelineRules.Direction) {
        guard timeline.barVisible else {
            timeline.showBar()
            return
        }
        timeline.jump(direction)
    }

    private func beginSelect() {
        // While rewind / fast-forward moves OK stops it and plays there (no long press, no pause).
        if timeline.trickOK() {
            selectBegan = nil
            return
        }
        selectBegan = Date()
        selectConsumed = false
        selectShowsBar = !timeline.barVisible && timeline.skipTarget == nil
        selectTimer?.invalidate()
        selectTimer = Timer.scheduledTimer(withTimeInterval: Self.longPressSeconds, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self, self.selectBegan != nil else { return }
                self.selectConsumed = true
                self.showAudioList()
            }
        }
    }

    /// OK: "Přeskočit reklamu" while it is offered, else pause/play; with the bar hidden it only shows the bar.
    private func endSelect() {
        selectTimer?.invalidate()
        selectTimer = nil
        guard selectBegan != nil else { return }
        selectBegan = nil
        guard !selectConsumed else { return }
        if timeline.skipTarget != nil {
            timeline.skipAd()
        } else if selectShowsBar {
            timeline.showBar()
        } else {
            timeline.togglePlay()
        }
    }

    // MARK: swipe

    @objc private func swiped(_ pan: UIPanGestureRecognizer) {
        let fraction = Double(pan.translation(in: view).x / max(1, view.bounds.width))
        // Not while rewind / fast-forward moves: the clickpad clicks that change its speed touch the surface too.
        if timeline.isTrickMoving {
            swipeBase = nil
            return
        }
        switch pan.state {
        case .began:
            swipeBase = timeline.displayPosition
            timeline.showBar()
        case .changed:
            guard let base = swipeBase else { return }
            timeline.scrub(from: base, offset: timeline.swipeOffset(fraction: fraction))
        case .ended:
            guard let base = swipeBase else { return }
            swipeBase = nil
            // A touch without a real swipe is not a seek.
            if abs(fraction) < 0.02 { timeline.cancelScrub() } else {
                timeline.endScrub(from: base, offset: timeline.swipeOffset(fraction: fraction))
            }
        case .cancelled, .failed:
            swipeBase = nil
            timeline.cancelScrub()
        default:
            break
        }
    }

    // MARK: audio

    /// The audio renditions; the Czech default is selected by the player's criteria, the list only changes it.
    private func showAudioList() {
        let (options, selected) = controller.audioChoices
        let sheet = UIAlertController(title: "Zvuk", message: options.isEmpty ? "Pořad má jen jednu zvukovou stopu." : nil,
                                      preferredStyle: .actionSheet)
        for option in options {
            let action = UIAlertAction(title: PlayerController.audioTitle(option), style: .default) { [weak self] _ in
                self?.controller.selectAudio(option)
            }
            sheet.addAction(action)
            if option == selected { sheet.preferredAction = action }
        }
        // No transport bar here: the mute action sits with the audio choice (spec/apps.md "Mute (T34)").
        if let mute = controller.mute {
            sheet.addAction(UIAlertAction(title: mute.actionTitle, style: .default) { [weak self] _ in
                self?.controller.toggleMute()
            })
        }
        sheet.addAction(UIAlertAction(title: options.isEmpty ? "OK" : "Zrušit", style: .cancel))
        present(sheet, animated: true)
    }
}

/// Forwards display-link frames without the link retaining the view controller. The link runs on the main run
/// loop (`frameLink` is added to `.main`).
@MainActor
private final class DisplayLinkProxy: NSObject {
    private weak var host: TimelineHostViewController?

    init(_ host: TimelineHostViewController) { self.host = host }

    @objc func frame(_ link: CADisplayLink) {
        guard let host else { link.invalidate(); return }
        host.frame(link)
    }
}

// MARK: - the bar

/// Own player bar of the timeline (spec/apps.md): title = programme at the playhead, the window with its recorded
/// pieces, programme ticks, the highlighted programme and the played part, the playhead's wall clock above it and
/// the window's ends below.
struct TimelineBar: View {
    let timeline: TimelineController

    var body: some View {
        if let window = timeline.timeline {
            let position = timeline.displayPosition
            let duration = max(timeline.duration, 1)
            let now = Date()
            let playhead = timeline.time(at: position) ?? now
            let title = timeline.title(at: position)
            let start = window.map.startTime ?? now
            let end = timeline.time(at: duration) ?? now
            let edges = TimelineRules.edgeLabels(start: start, end: end, playhead: playhead, now: now,
                                                 growing: !window.complete)
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .firstTextBaseline, spacing: 18) {
                    if timeline.isPaused && timeline.trickBadge == nil {
                        Image(systemName: "pause.fill").font(.title2)
                    }
                    Text(title.title).font(.title2.bold()).lineLimit(1).accessibilityIdentifier("timeline.title")
                }
                if !title.subtitle.isEmpty {
                    Text(title.subtitle).font(.headline).foregroundStyle(.secondary).lineLimit(1)
                        .accessibilityIdentifier("timeline.subtitle")
                }
                if let message = timeline.message {
                    Text(message).font(.headline).foregroundStyle(.yellow).lineLimit(2)
                        .accessibilityIdentifier("timeline.message")
                }
                TimelineTrack(window: window, timeline: timeline, position: position, duration: duration,
                              label: TimelineRules.label(playhead, now: now), badge: timeline.trickBadge)
                HStack {
                    Text(edges.start)
                    Spacer()
                    Text(edges.end)
                }
                .font(.callout.monospacedDigit())
                .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 80)
            .padding(.top, 120)
            .padding(.bottom, 60)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(LinearGradient(colors: [.clear, .black.opacity(0.85)], startPoint: .top, endPoint: .bottom))
        }
    }
}

/// The window on a wall-clock axis, so gaps in the recording show as empty stretches.
private struct TimelineTrack: View {
    let window: DVBTimeline
    let timeline: TimelineController
    let position: Double
    let duration: Double
    let label: String
    /// `◀◀` / `▶▶ 2` while rewind / fast-forward moves, next to the clock.
    let badge: String?

    private static let height = TrackAxis.height
    private static let highlight = Color(red: 0.35, green: 0.62, blue: 1)

    var body: some View {
        GeometryReader { geometry in
            let width = geometry.size.width
            let first = window.map.startTime ?? Date()
            let last = timeline.time(at: duration) ?? first
            let span = max(1, last.timeIntervalSince(first))
            let x = { (time: Date) in CGFloat(min(max(time.timeIntervalSince(first) / span, 0), 1)) * width }
            let playhead = timeline.time(at: position) ?? first
            let highlighted = timeline.programme(at: position)
            ZStack(alignment: .topLeading) {
                // Pieces and programme ticks do not depend on the playhead: not rebuilt on every scrub frame.
                TimelineTrackBackground(window: window, last: last, width: width).equatable()
                ForEach(Array(window.map.pieces.enumerated()), id: \.offset) { index, piece in
                    // The last piece of a growing window reaches the player's end.
                    let pieceEnd = index == window.map.pieces.count - 1 ? last : piece.endTime
                    if let highlighted {
                        let from = max(highlighted.start, piece.time), to = min(highlighted.end, pieceEnd)
                        if to > from {
                            Rectangle().fill(Self.highlight.opacity(0.6))
                                .frame(width: x(to) - x(from), height: Self.height)
                                .offset(x: x(from))
                        }
                    }
                    if playhead > piece.time {
                        let to = min(playhead, pieceEnd)
                        let from = highlighted.map { max($0.start, piece.time) } ?? piece.time
                        // Played: white before the highlighted programme, its colour inside it.
                        Rectangle().fill(.white.opacity(0.85))
                            .frame(width: max(0, x(min(to, from)) - x(piece.time)), height: Self.height)
                            .offset(x: x(piece.time))
                        if to > from {
                            Rectangle().fill(Self.highlight)
                                .frame(width: x(to) - x(from), height: Self.height)
                                .offset(x: x(from))
                        }
                    }
                }
                TimelineTrackTicks(window: window, last: last, width: width).equatable()
                Circle().fill(.white)
                    .frame(width: 28, height: 28)
                    .shadow(radius: 4)
                    .offset(x: x(playhead) - 14, y: Self.height / 2 - 14)
                HStack(spacing: 10) {
                    if let badge {
                        Text(badge).accessibilityIdentifier("timeline.trick")
                    }
                    Text(label).accessibilityIdentifier("timeline.clock")
                }
                    .font(.headline.monospacedDigit())
                    .fixedSize()
                    .padding(.horizontal, 12)
                    .padding(.vertical, 4)
                    .background(.black.opacity(0.6), in: Capsule())
                    .alignmentGuide(.leading) { [playheadX = x(playhead)] dimensions in
                        // Centred on the playhead, kept inside the bar.
                        let wanted = playheadX - dimensions.width / 2
                        return -min(max(wanted, 0), max(0, width - dimensions.width))
                    }
                    .offset(y: -58)
            }
        }
        .frame(height: Self.height + 8)
        .padding(.top, 58)
    }
}

/// The window's outline and recorded pieces (under the played part).
private struct TimelineTrackBackground: View, Equatable {
    let window: DVBTimeline
    let last: Date
    let width: CGFloat

    var body: some View {
        let axis = TrackAxis(window: window, last: last, width: width)
        ZStack(alignment: .topLeading) {
            Capsule().stroke(.white.opacity(0.35), lineWidth: 2)
                .frame(width: width, height: TrackAxis.height)
            ForEach(Array(window.map.pieces.enumerated()), id: \.offset) { index, piece in
                // The last piece of a growing window reaches the player's end.
                let pieceEnd = index == window.map.pieces.count - 1 ? last : piece.endTime
                Rectangle().fill(.white.opacity(0.3))
                    .frame(width: max(1, axis.x(pieceEnd) - axis.x(piece.time)), height: TrackAxis.height)
                    .offset(x: axis.x(piece.time))
            }
        }
    }
}

/// Programme boundaries as ticks (over the played part).
private struct TimelineTrackTicks: View, Equatable {
    let window: DVBTimeline
    let last: Date
    let width: CGFloat

    var body: some View {
        let axis = TrackAxis(window: window, last: last, width: width)
        ZStack(alignment: .topLeading) {
            ForEach(Array(window.programmes.enumerated()), id: \.offset) { _, programme in
                if programme.start > axis.first, programme.start < last {
                    Rectangle().fill(.white)
                        .frame(width: 3, height: TrackAxis.height + 12)
                        .offset(x: axis.x(programme.start) - 1.5, y: -6)
                }
            }
        }
    }
}

/// The wall-clock axis of the track: the first recorded time at 0, `last` at `width`.
private struct TrackAxis {
    static let height: CGFloat = 14
    let first: Date
    let span: TimeInterval
    let width: CGFloat

    init(window: DVBTimeline, last: Date, width: CGFloat) {
        first = window.map.startTime ?? last
        span = max(1, last.timeIntervalSince(first))
        self.width = width
    }

    func x(_ time: Date) -> CGFloat { CGFloat(min(max(time.timeIntervalSince(first) / span, 0), 1)) * width }
}

/// "Přeskočit reklamu" of the timeline player; OK presses it while it is shown.
struct TimelineSkipBadge: View {
    var body: some View {
        HStack(spacing: 12) {
            Text("Přeskočit reklamu").font(.headline)
            Text("OK").font(.caption.bold())
                .padding(.horizontal, 8).padding(.vertical, 2)
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(.white, lineWidth: 2))
        }
        .padding(.horizontal, 28)
        .padding(.vertical, 16)
        .background(.white.opacity(0.2), in: Capsule())
        .padding(.trailing, 80)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("timeline.skip")
    }
}
