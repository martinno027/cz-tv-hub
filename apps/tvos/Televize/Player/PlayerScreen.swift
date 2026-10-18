import AVKit
import SwiftUI

/// Full-screen playback with the native tvOS transport controls (seeking, audio and
/// subtitle tracks from the HLS manifest).
struct PlayerScreen: View {
    let session: PlayerSession

    @Environment(AppModel.self) private var model

    var body: some View {
        PlayerViewControllerView(player: session.controller.player)
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
    let player: AVPlayer

    func makeUIViewController(context: Context) -> AVPlayerViewController {
        let controller = AVPlayerViewController()
        controller.player = player
        return controller
    }

    func updateUIViewController(_ controller: AVPlayerViewController, context: Context) {
        if controller.player !== player { controller.player = player }
    }
}
