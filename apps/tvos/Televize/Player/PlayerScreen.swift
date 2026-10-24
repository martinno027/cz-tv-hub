import AVKit
import SwiftUI

/// Full-screen playback with the native tvOS transport controls (seeking, audio and
/// subtitle tracks from the HLS manifest). Live adds zapping: up/down and digits (`ChannelZapper`).
struct PlayerScreen: View {
    let session: PlayerSession

    @Environment(AppModel.self) private var model

    var body: some View {
        if let timeline = session.timeline {
            timelineBody(timeline)
        } else {
            nativeBody
        }
    }

    /// `b…` recordings: the video without AVKit's controls, the own bar at the bottom.
    private func timelineBody(_ timeline: TimelineController) -> some View {
        TimelinePlayerView(controller: session.controller, timeline: timeline)
            .ignoresSafeArea()
            .background(Color.black)
            .overlay(alignment: .bottom) {
                if timeline.barVisible {
                    TimelineBar(timeline: timeline).transition(.opacity)
                }
            }
            .overlay(alignment: .bottomTrailing) {
                if timeline.skipTarget != nil {
                    TimelineSkipBadge().padding(.bottom, timeline.barVisible ? 330 : 80)
                }
            }
            .ignoresSafeArea()
            .animation(.easeInOut(duration: 0.25), value: timeline.barVisible)
            .onAppear {
                model.isPlayerOnScreen = true
                session.controller.start()
                timeline.start()
            }
            .onDisappear {
                model.isPlayerOnScreen = false
                timeline.stop()
                session.controller.stop()
            }
    }

    private var nativeBody: some View {
        PlayerViewControllerView(controller: session.controller, zapper: session.zapper)
            .ignoresSafeArea()
            .background(Color.black)
            .overlay(alignment: .topLeading) {
                if let banner = session.zapper?.banner { ZapBanner(banner: banner) }
            }
            .overlay(alignment: .topTrailing) {
                if let entry = session.zapper?.entry { NumberEntryBadge(entry: entry) }
            }
            .onAppear {
                model.isPlayerOnScreen = true
                session.controller.start()
            }
            .onDisappear {
                model.isPlayerOnScreen = false
                session.zapper?.stop()
                session.controller.stop()
            }
    }
}

private struct PlayerViewControllerView: UIViewControllerRepresentable {
    let controller: PlayerController
    let zapper: ChannelZapper?

    func makeUIViewController(context: Context) -> PlayerHostViewController {
        let host = PlayerHostViewController()
        host.player.player = controller.player
        if let zapper {
            host.onStep = { zapper.step(by: $0) }
            host.onDigit = { zapper.digit($0) }
        }
        controller.playerViewController = host.player
        return host
    }

    func updateUIViewController(_ host: PlayerHostViewController, context: Context) {
        if host.player.player !== controller.player { host.player.player = controller.player }
        if controller.playerViewController !== host.player { controller.playerViewController = host.player }
    }
}

/// Holds the AVPlayerViewController as a child and adds zapping to it: up/down presses while the transport bar
/// is hidden (with the bar shown they move through the controls as before) and digit key presses, which the
/// player does not handle, so the responder chain passes them up to this controller.
final class PlayerHostViewController: UIViewController, @preconcurrency AVPlayerViewControllerDelegate, UIGestureRecognizerDelegate {
    let player = AVPlayerViewController()
    /// −1 = up (previous channel), +1 = down (next channel).
    var onStep: ((Int) -> Void)?
    var onDigit: ((Int) -> Void)?
    private var transportBarVisible = false

    override func viewDidLoad() {
        super.viewDidLoad()
        addChild(player)
        player.view.frame = view.bounds
        player.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(player.view)
        player.didMove(toParent: self)
        player.delegate = self
        for (press, selector) in [(UIPress.PressType.upArrow, #selector(up)), (.downArrow, #selector(down))] {
            let recognizer = UITapGestureRecognizer(target: self, action: selector)
            recognizer.allowedPressTypes = [NSNumber(value: press.rawValue)]
            recognizer.allowedTouchTypes = []
            recognizer.delegate = self
            view.addGestureRecognizer(recognizer)
        }
    }

    override var preferredFocusEnvironments: [UIFocusEnvironment] { [player] }

    @objc private func up() { onStep?(-1) }
    @objc private func down() { onStep?(1) }

    /// AVKit's own up/down press handling (it shows the transport bar) waits until a zap press is not one.
    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                           shouldBeRequiredToFailBy other: UIGestureRecognizer) -> Bool {
        other.view?.isDescendant(of: player.view) == true
    }

    func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
        onStep != nil && !transportBarVisible
    }

    func playerViewController(_ playerViewController: AVPlayerViewController,
                              willTransitionToVisibilityOfTransportBar visible: Bool,
                              with coordinator: any AVPlayerViewControllerAnimationCoordinator) {
        transportBarVisible = visible
    }

    override func pressesBegan(_ presses: Set<UIPress>, with event: UIPressesEvent?) {
        var rest = presses
        if let onDigit {
            for press in presses {
                if let digit = NumberEntry.digit(of: press) {
                    onDigit(digit)
                    rest.remove(press)
                }
            }
        }
        if !rest.isEmpty { super.pressesBegan(rest, with: event) }
    }
}
