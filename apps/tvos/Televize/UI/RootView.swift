import CTCore
import SwiftUI

struct RootView: View {
    enum Tab: Hashable { case live, guide, settings }

    @Environment(AppModel.self) private var model
    @State private var tab: Tab = .live

    var body: some View {
        @Bindable var model = model
        TabView(selection: $tab) {
            LiveView()
                .tabItem { Text("Živě") }
                .tag(Tab.live)
            GuideView()
                .tabItem { Text("TV průvodce") }
                .tag(Tab.guide)
            SettingsView()
                .tabItem { Text("Nastavení") }
                .tag(Tab.settings)
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
