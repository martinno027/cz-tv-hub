import CTCore
import SwiftUI

/// The archive's loader (T52, spec/apps.md "Loading is always visible"): a spinning ring in the accent colour,
/// 64 px, or half of it below a list waiting for its next page. Takes no focus and no presses.
struct LoaderRing: View {
    var small = false
    var identifier = "archive.loader"
    @State private var spinning = false

    var body: some View {
        let size = small ? LoadingState.smallRingSize : LoadingState.ringSize
        Circle()
            .trim(from: 0, to: 0.75)
            .stroke(Color.accentColor, style: StrokeStyle(lineWidth: size / 10, lineCap: .round))
            .frame(width: size, height: size)
            .rotationEffect(.degrees(spinning ? 360 : 0))
            .animation(.linear(duration: 0.9).repeatForever(autoreverses: false), value: spinning)
            .onAppear { spinning = true }
            .allowsHitTesting(false)
            .accessibilityIdentifier(identifier)
    }
}

extension View {
    /// Sets `shown` once `state` has waited `LoadingState.delay`, back to false when the wait ends. A newer request
    /// during the wait keeps the same `since`, so the loader stays.
    func loaderDelay(_ state: LoadingState, shown: Binding<Bool>) -> some View {
        task(id: state.since) {
            guard let wait = state.untilShown() else {
                shown.wrappedValue = false
                return
            }
            if wait > .zero {
                do { try await Task.sleep(for: wait) } catch { return }
            }
            shown.wrappedValue = true
        }
    }

    /// The old content under a shown loader: dimmed, still focusable.
    func dimmedWhileLoading(_ shown: Bool) -> some View {
        opacity(shown ? LoadingState.dimmedOpacity : 1)
            .animation(.easeInOut(duration: 0.15), value: shown)
    }
}

/// A small loader below a list waiting for its next page (after the same 150 ms).
struct NextPageLoader: View {
    let state: LoadingState
    @State private var shown = false

    var body: some View {
        ZStack {
            if shown { LoaderRing(small: true, identifier: "archive.loader.more") }
        }
        .frame(maxWidth: .infinity, minHeight: shown ? LoadingState.smallRingSize + 20 : 0)
        .loaderDelay(state, shown: $shown)
    }
}
