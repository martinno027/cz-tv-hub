import CTCore
import SwiftUI

struct RootView: View {
    enum Tab: Hashable { case live, guide, settings }

    @Environment(AppModel.self) private var model
    @State private var tab: Tab = .live
    /// Every open of the guide (its tab chosen, the app back in front on it) shows now again (spec/apps.md).
    @State private var guideOpens = 0
    @Environment(\.scenePhase) private var scenePhase

    private struct VisibleKey: Equatable {
        let prefs: ChannelPrefs
        let gateway: GatewayConfig?
    }

    var body: some View {
        @Bindable var model = model
        TabView(selection: $tab) {
            LiveView()
                .tabItem { Text("Živě") }
                .tag(Tab.live)
            GuideView(opens: guideOpens)
                .tabItem { Text("TV průvodce") }
                .tag(Tab.guide)
            SettingsView()
                .tabItem { Text("Nastavení") }
                .tag(Tab.settings)
        }
        .onChange(of: tab) { _, tab in
            if tab == .guide { guideOpens += 1 }
        }
        .onChange(of: scenePhase) { _, phase in
            // back in front on the guide is an open; the player closing is not (the guide stays where it was)
            if phase == .active, tab == .guide, model.session == nil { guideOpens += 1 }
        }
        // Digits on Živě and the guide start that channel live (spec/apps.md "Number keys").
        .onKeyPress(characters: .decimalDigits, phases: .down) { press in
            guard tab != .settings, model.session == nil, let digit = Int(press.characters) else { return .ignored }
            model.numberEntry.type(digit, count: model.visibleChannels.count)
            return .handled
        }
        .overlay(alignment: .topTrailing) {
            if tab != .settings { NumberEntryBadge(entry: model.numberEntry) }
        }
        // Profile: pending changes sent and a newer version read at start, failed sends again every 60 s.
        .task { await model.runProfileSync() }
        .task(id: VisibleKey(prefs: model.channelPrefs.prefs, gateway: model.gateway)) {
            // Channel numbers: kept current for the screens, the zapping list and number entry.
            while !Task.isCancelled {
                await model.refreshVisibleChannels()
                try? await Task.sleep(for: .seconds(600))
            }
        }
        .overlay {
            if model.isResolving {
                ProgressView()
                    .scaleEffect(1.6)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(.black.opacity(0.4))
            }
        }
        .fullScreenCover(item: $model.session, onDismiss: {
            model.alertMessage = model.messageAfterPlayer
            model.messageAfterPlayer = nil
        }) { session in
            PlayerScreen(session: session)
        }
        .fullScreenCover(isPresented: Binding(get: { model.drmError != nil }, set: { if !$0 { model.drmError = nil } })) {
            if let error = model.drmError {
                DRMErrorView(error: error, onBack: { model.drmError = nil }) {
                    model.drmError = nil
                    OfficialApp.open { model.alertMessage = OfficialApp.fallbackMessage }
                }
            }
        }
        .alert(
            model.alertMessage ?? "",
            isPresented: Binding(get: { model.alertMessage != nil }, set: {
                if !$0 { model.alertMessage = nil }
            })
        ) {
            Button("OK", role: .cancel) {}
        }
        #if DEBUG
        .task {
            let arguments = UserDefaults.standard
            // `-gatewayAddress 127.0.0.1:8098 -gatewayKey …` configures the dev gateway.
            // `-showDRMError YES` shows the DRM notice with CT's real text (fixture vod_drm.json).
            if arguments.bool(forKey: "showDRMError") {
                model.drmError = CTError("Video vyžaduje prohlížeč s\u{00A0}ochranou DRM\n" + CTError.drmHint,
                                         code: CTError.unsupportedDRM)
            }
            if let address = arguments.string(forKey: "gatewayAddress"), let key = arguments.string(forKey: "gatewayKey") {
                model.setGateway(GatewayConfig(address: address, key: key))
            }
            // `-profileName Jana -profilePassword …` signs in to a dev gateway's profile (simulator checks only).
            if let name = arguments.string(forKey: "profileName"), let password = arguments.string(forKey: "profilePassword"),
               !model.profile.isSignedIn {
                do { try await model.profile.signIn(name: name, password: password) } catch {
                    model.alertMessage = AppModel.message(for: error)
                }
            }
            switch arguments.string(forKey: "tab") {
            case "guide": tab = .guide
            case "settings": tab = .settings
            default: break
            }
            if let play = arguments.string(forKey: "play") { await model.debugPlay(play) }
            if arguments.bool(forKey: "openOfficialApp") {
                OfficialApp.open { model.alertMessage = OfficialApp.fallbackMessage }
            }
        }
        #endif
    }
}
