import CTCore
import SwiftUI

/// The red "● ŽIVĚ" badge of a Chamber stream that is on.
struct LiveBadge: View {
    var body: some View {
        Text("● ŽIVĚ")
            .font(.system(size: 22, weight: .heavy))
            .foregroundStyle(.white)
            .padding(.horizontal, 14)
            .padding(.vertical, 5)
            .background(Capsule().fill(Color.red))
            .accessibilityLabel("Živě")
    }
}

/// Checks the given Chamber streams while this screen is on screen (spec/apps.md "Poslanecká sněmovna"):
/// not while the player covers it, not in the background, not for hidden channels or a source that is off.
private struct ParliamentWatch: ViewModifier {
    let streams: [PSPStream]
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase

    private struct Key: Equatable {
        let streams: [PSPStream]
        let active: Bool
    }

    func body(content: Content) -> some View {
        let active = scenePhase == .active && model.session == nil && model.drmError == nil
        content.task(id: Key(streams: streams, active: active)) {
            guard active, !streams.isEmpty else { return }
            log.info("psp checks on (\(streams.count) streams)")
            await model.parliament.watch(streams)
            log.info("psp checks off")
        }
    }
}

extension View {
    func watchesParliament(_ streams: [PSPStream]) -> some View {
        modifier(ParliamentWatch(streams: streams))
    }
}

extension MixedChannel {
    var parliamentStream: PSPStream? {
        if case .parliament(let stream) = self { return stream }
        return nil
    }
}
