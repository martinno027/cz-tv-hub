import CTCore
import Foundation
import Observation
import SwiftUI

/// What is on air now on every channel, for Živě and Domů (spec/apps.md "Domů": nothing new is fetched for Domů). One
/// loop, run by whichever of the two is on screen: the cached ČT schedules and Anténa EPG re-read every minute, a
/// failed ČT day asked again by `ScheduleRetry` (T42).
@MainActor
@Observable
final class LiveNowStore {
    private(set) var onAir = ScheduleStore.OnAir()
    private(set) var antennaChannels: [DVBChannel] = []
    private(set) var antennaOnAir: [String: DVBEvent] = [:]
    private(set) var antennaError: String?
    /// When ČT's schedule last failed and was asked (T42); kept while hidden, so back on screen it asks when due.
    @ObservationIgnored private var retry = ScheduleRetry()
    /// The newest run; an older one (the other screen's, still finishing) stops.
    @ObservationIgnored private var runs = 0

    /// Refreshes until cancelled (the screen left, the player opened, the app went to the background).
    func run(_ model: AppModel) async {
        runs += 1
        let run = runs
        if retry.failing, let wait = retry.delay(now: Date()), wait > 0 {
            do { try await Task.sleep(for: .seconds(wait)) } catch { return }  // back on screen before it is due
        }
        while !Task.isCancelled, run == runs {
            let onAir = await model.schedules.onAirNow(at: Date())
            guard !Task.isCancelled, run == runs else { return }
            self.onAir = onAir
            await loadAntenna(model.antenna)
            retry.record(failed: !onAir.failed.isEmpty, at: Date())
            do { try await Task.sleep(for: .seconds(retry.liveWait(now: Date()))) } catch { return }
        }
    }

    private func loadAntenna(_ antenna: AntennaStore?) async {
        guard let antenna else {
            antennaChannels = []
            antennaOnAir = [:]
            return
        }
        do {
            antennaChannels = try await antenna.channels()
            antennaOnAir = await antenna.onAir(at: Date())
            antennaError = nil
        } catch {
            antennaError = AppModel.message(for: error)
        }
    }
}

/// Runs `LiveNowStore` while this screen is in front with no player over it (T42: no asking behind the player or in
/// the background); a new gateway or a Tailscale restart loads again.
private struct LiveNowRefresh: ViewModifier {
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase

    private struct LoadKey: Equatable {
        let gateway: GatewayConfig?
        let reloads: Int
        let visible: Bool
    }

    func body(content: Content) -> some View {
        content.task(id: LoadKey(gateway: model.gateway, reloads: model.gatewayReloads,
                                 visible: scenePhase == .active && model.session == nil)) {
            guard scenePhase == .active, model.session == nil else { return }
            await model.liveNow.run(model)
        }
    }
}

extension View {
    func refreshesLiveNow() -> some View {
        modifier(LiveNowRefresh())
    }
}
