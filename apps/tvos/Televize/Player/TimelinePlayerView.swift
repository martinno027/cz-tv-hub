import AVKit
import CTCore
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

/// Keys of the timeline player (spec/apps.md "Anténa timeline player"): OK and play/pause pause, ← / → step
/// 10 s or scrub while held, a horizontal swipe scrubs, ▲ / ▼ jump between programmes, a long OK press opens the
/// audio list, Back closes the player. While the bar is hidden the first key only shows it, except play/pause,
/// Back, the long OK and OK while "Přeskočit reklamu" is shown.
final class TimelineHostViewController: UIViewController {
    let player = AVPlayerViewController()
    private let timeline: TimelineController
    private let controller: PlayerController

    private struct Hold {
        let sign: Double
        let began: Date
        let base: Double
        /// The press only showed the bar.
        let ignored: Bool
    }

    private var hold: Hold?
    private var holdTimer: Timer?
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
    }

    override var preferredFocusEnvironments: [UIFocusEnvironment] { [view] }

    // MARK: presses

    override func pressesBegan(_ presses: Set<UIPress>, with event: UIPressesEvent?) {
        var rest = Set<UIPress>()
        for press in presses {
            switch press.type {
            case .leftArrow: beginHold(-1)
            case .rightArrow: beginHold(1)
            case .upArrow: arrowJump(.up)
            case .downArrow: arrowJump(.down)
            case .select: beginSelect()
            case .playPause: timeline.togglePlay()
            default: rest.insert(press)
            }
        }
        if !rest.isEmpty { super.pressesBegan(rest, with: event) }
    }

    override func pressesEnded(_ presses: Set<UIPress>, with event: UIPressesEvent?) {
        var rest = Set<UIPress>()
        for press in presses {
            switch press.type {
            case .leftArrow, .rightArrow: endHold(cancelled: false)
            case .select: endSelect()
            case .upArrow, .downArrow, .playPause: break
            default: rest.insert(press)
            }
        }
        if !rest.isEmpty { super.pressesEnded(rest, with: event) }
    }

    override func pressesCancelled(_ presses: Set<UIPress>, with event: UIPressesEvent?) {
        var rest = Set<UIPress>()
        for press in presses {
            switch press.type {
            case .leftArrow, .rightArrow: endHold(cancelled: true)
            case .select:
                selectTimer?.invalidate()
                selectBegan = nil
            case .upArrow, .downArrow, .playPause: break
            default: rest.insert(press)
            }
        }
        if !rest.isEmpty { super.pressesCancelled(rest, with: event) }
    }

    private func beginHold(_ sign: Double) {
        guard hold == nil else { return }  // key repeat of the same press
        let ignored = !timeline.barVisible
        timeline.showBar()
        hold = Hold(sign: sign, began: Date(), base: timeline.displayPosition, ignored: ignored)
        guard !ignored else { return }
        holdTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.holdTick() }
        }
    }

    private func holdTick() {
        guard let hold else { return }
        let held = Date().timeIntervalSince(hold.began)
        guard held >= TimelineRules.holdThreshold else { return }
        timeline.scrub(from: hold.base, offset: hold.sign * TimelineRules.holdOffset(held: held))
    }

    private func endHold(cancelled: Bool) {
        holdTimer?.invalidate()
        holdTimer = nil
        guard let hold else { return }
        self.hold = nil
        guard !hold.ignored else { return }
        if cancelled {
            timeline.cancelScrub()
            return
        }
        let held = Date().timeIntervalSince(hold.began)
        timeline.endScrub(from: hold.base, offset: hold.sign * TimelineRules.holdOffset(held: held))
    }

    private func arrowJump(_ direction: TimelineRules.Direction) {
        guard timeline.barVisible else {
            timeline.showBar()
            return
        }
        timeline.jump(direction)
    }

    private func beginSelect() {
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
        sheet.addAction(UIAlertAction(title: options.isEmpty ? "OK" : "Zrušit", style: .cancel))
        present(sheet, animated: true)
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
                    if timeline.isPaused {
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
                              label: TimelineRules.label(playhead, now: now))
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

    private static let height: CGFloat = 14
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
                Capsule().stroke(.white.opacity(0.35), lineWidth: 2)
                    .frame(width: width, height: Self.height)
                ForEach(Array(window.map.pieces.enumerated()), id: \.offset) { index, piece in
                    // The last piece of a growing window reaches the player's end.
                    let pieceEnd = index == window.map.pieces.count - 1 ? last : piece.endTime
                    Rectangle().fill(.white.opacity(0.3))
                        .frame(width: max(1, x(pieceEnd) - x(piece.time)), height: Self.height)
                        .offset(x: x(piece.time))
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
                ForEach(Array(window.programmes.enumerated()), id: \.offset) { _, programme in
                    if programme.start > first, programme.start < last {
                        Rectangle().fill(.white)
                            .frame(width: 3, height: Self.height + 12)
                            .offset(x: x(programme.start) - 1.5, y: -6)
                    }
                }
                Circle().fill(.white)
                    .frame(width: 28, height: 28)
                    .shadow(radius: 4)
                    .offset(x: x(playhead) - 14, y: Self.height / 2 - 14)
                Text(label)
                    .accessibilityIdentifier("timeline.clock")
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
