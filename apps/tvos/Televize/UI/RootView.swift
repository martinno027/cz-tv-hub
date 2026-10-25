import CTCore
import SwiftUI

struct RootView: View {
    enum Tab: Hashable { case live, guide, archive, settings }

    @Environment(AppModel.self) private var model
    @State private var tab: Tab = .live
    /// Every open of the guide (its tab chosen, the app back in front on it) shows now again (spec/apps.md).
    @State private var guideOpens = 0
    /// Every open of Archiv starts its menu afresh (spec/apps.md "Archiv"); the show page and the player keep it.
    @State private var archiveOpens = 0
    @Environment(\.scenePhase) private var scenePhase

    private struct VisibleKey: Equatable {
        let prefs: ChannelPrefs
        let gateway: GatewayConfig?
    }

    var body: some View {
        TabView(selection: $tab) {
            LiveView()
                .tabItem { Text("Živě") }
                .tag(Tab.live)
            GuideView(opens: guideOpens)
                .tabItem { Text("TV průvodce") }
                .tag(Tab.guide)
            ArchiveView(opens: archiveOpens)
                .tabItem { Text("Archiv") }
                .tag(Tab.archive)
            SettingsView()
                .tabItem { Text("Nastavení") }
                .tag(Tab.settings)
        }
        .onChange(of: tab) { _, tab in
            if tab == .guide { guideOpens += 1 }
            if tab == .archive { archiveOpens += 1 }
        }
        .onChange(of: scenePhase) { _, phase in
            // back in front on the guide is an open; the player closing is not (the guide stays where it was)
            if phase == .active, tab == .guide, model.session == nil { guideOpens += 1 }
            // the Tailscale node's newest state into the Keychain (status polling stops once it runs)
            if phase == .background { Task { await model.tailnet.refresh() } }
            // its connections may have died while suspended (spec/apps.md "Back in front (T18)")
            if phase == .active { Task { await model.tailnet.checkBridge() } }
        }
        // Digits on Živě and the guide start that channel live (spec/apps.md "Number keys").
        .onKeyPress(characters: .decimalDigits, phases: .down) { press in
            guard tab == .live || tab == .guide, model.session == nil, let digit = Int(press.characters) else { return .ignored }
            model.numberEntry.type(digit, count: model.visibleChannels.count)
            return .handled
        }
        .clockTopRight()
        .overlay(alignment: .topTrailing) {
            if tab == .live || tab == .guide { NumberEntryBadge(entry: model.numberEntry) }
        }
        // Household default and profile read at start (pending profile changes sent first), failed sends again every 60 s.
        .task { await model.runProfileSync() }
        .task(id: VisibleKey(prefs: model.channelPrefs.prefs, gateway: model.gateway)) {
            // Channel numbers: kept current for the screens, the zapping list and number entry.
            while !Task.isCancelled {
                await model.refreshVisibleChannels()
                try? await Task.sleep(for: .seconds(600))
            }
        }
        // While the programme page is open it presents these itself (nothing can be presented from under it).
        .modifier(PlaybackPresentations(active: !model.isProgrammePageOpen))
        #if DEBUG
        .task {
            let arguments = UserDefaults.standard
            // `-gatewayAddress 127.0.0.1:8098 -gatewayKey …` configures the dev gateway.
            // `-showDRMError YES` shows the DRM notice with CT's real text (fixture vod_drm.json).
            if arguments.bool(forKey: "showDRMError") {
                model.drmError = CTError("Video vyžaduje prohlížeč s\u{00A0}ochranou DRM\n" + CTError.drmHint,
                                         code: CTError.unsupportedDRM)
            }
            if let json = arguments.string(forKey: "tailnetInfo") { model.tailnet.debugShow(json) }
            if let address = arguments.string(forKey: "gatewayAddress"),
               arguments.object(forKey: "tailnetFakeRunningAfter") != nil {
                model.tailnet.debugFakeStart(address: address, runningAfter: arguments.double(forKey: "tailnetFakeRunningAfter"),
                                             state: arguments.string(forKey: "tailnetFakeState") ?? "Starting")
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
            case "archive": tab = .archive
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

/// The player, the DRM notice, the messages and the spinner while a stream is resolved. RootView presents them, or the
/// programme page while it is open, so the page stays under the player (spec/apps.md "Programme page (T15)").
struct PlaybackPresentations: ViewModifier {
    let active: Bool
    @Environment(AppModel.self) private var model

    func body(content: Content) -> some View {
        content
            .overlay {
                if active, model.isResolving {
                    ProgressView()
                        .scaleEffect(1.6)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .background(.black.opacity(0.4))
                }
            }
            .fullScreenCover(item: Binding(get: { active ? model.session : nil }, set: { model.session = $0 }), onDismiss: {
                model.alertMessage = model.messageAfterPlayer
                model.messageAfterPlayer = nil
            }) { session in
                PlayerScreen(session: session)
            }
            .fullScreenCover(isPresented: Binding(get: { active && model.drmError != nil }, set: { if !$0 { model.drmError = nil } })) {
                if let error = model.drmError {
                    DRMErrorView(error: error, onBack: { model.drmError = nil }) {
                        model.drmError = nil
                        OfficialApp.open { model.alertMessage = OfficialApp.fallbackMessage }
                    }
                }
            }
            .alert(
                model.alertMessage ?? "",
                isPresented: Binding(get: { active && model.alertMessage != nil }, set: {
                    if !$0 { model.alertMessage = nil }
                })
            ) {
                Button("OK", role: .cancel) {}
            }
    }
}
