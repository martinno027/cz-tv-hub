import AVKit
import SwiftUI

/// Full-screen playback with the native tvOS transport controls (seeking, audio and
/// subtitle tracks from the HLS manifest).
struct PlayerScreen: View {
    let session: PlayerSession

    @Environment(AppModel.self) private var model

    var body: some View {
        PlayerViewControllerView(controller: session.controller)
            .ignoresSafeArea()
            .background(Color.black)
            .onAppear {
                model.isPlayerOnScreen = true
                session.controller.start()
            }
            .onDisappear {
                model.isPlayerOnScreen = false
                session.controller.stop()
            }
    }
}

private struct PlayerViewControllerView: UIViewControllerRepresentable {
    let controller: PlayerController

    func makeUIViewController(context: Context) -> AVPlayerViewController {
        let viewController = AVPlayerViewController()
        viewController.player = controller.player
        controller.playerViewController = viewController
        return viewController
    }

    func updateUIViewController(_ viewController: AVPlayerViewController, context: Context) {
        if viewController.player !== controller.player { viewController.player = controller.player }
        if controller.playerViewController !== viewController { controller.playerViewController = viewController }
    }
}
