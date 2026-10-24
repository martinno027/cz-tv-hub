import CTCore
import SwiftUI

/// "Nastavení": a choice of three sections, "Kanály" (channels shown on this TV), "Profil" (settings in a profile on
/// the gateway) and "Server" (the DVB-T2 gateway). The chosen one opens full screen; the remote's Menu/‹ returns to the
/// choice (spec/apps.md).
struct SettingsView: View {
    enum Section: String, CaseIterable, Identifiable {
        case channels = "Kanály"
        case profile = "Profil"
        case server = "Server"
        var id: String { rawValue }

        @MainActor
        func detail(_ model: AppModel) -> String {
            switch self {
            case .channels: "Které kanály se zobrazují v Živě a v TV průvodci, a v jakém pořadí"
            case .profile: model.profile.profileName.map { "Přihlášen: \($0)" } ?? "Nepřihlášen"
            case .server: "Brána Anténa (DVB-T2) na Raspberry Pi: adresa a klíč"
            }
        }
    }

    @Environment(AppModel.self) private var model

    @State private var open: Section?
    /// Focus returns to the item of the section just closed.
    @State private var lastOpened: Section?
    @FocusState private var focused: Section?

    var body: some View {
        VStack(alignment: .leading, spacing: 40) {
            Text(BuildInfo.versionLine)
                .font(.callout)
                .foregroundStyle(.secondary)
                .frame(width: 1000, alignment: .leading)
            ForEach(Section.allCases) { section in
                Button {
                    open = section
                } label: {
                    VStack(alignment: .leading, spacing: 12) {
                        Text(section.rawValue).font(.title2.weight(.bold))
                        Text(section.detail(model)).font(.callout).foregroundStyle(.secondary)
                    }
                    .padding(32)
                    .frame(width: 1000, alignment: .leading)
                }
                .buttonStyle(.card)
                .focused($focused, equals: section)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .fullScreenCover(item: $open, onDismiss: { focused = lastOpened }) { section in
            SettingsPage(section: section)
                .onAppear { lastOpened = section }
        }
        // Nastavení opens: a pending profile change is sent, a newer profile version applied.
        .task { await model.profile.refresh() }
        #if DEBUG
        // `-settingsSection channels|profile|server` opens that section, for simulator screenshots.
        .onAppear {
            if let raw = UserDefaults.standard.string(forKey: "settingsSection") {
                open = Section.allCases.first { "\($0)" == raw } ?? .channels
            }
        }
        #endif
    }
}

/// One Nastavení section, full screen; Menu/‹ goes back to the choice.
private struct SettingsPage: View {
    let section: SettingsView.Section
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 32) {
            Text(section.rawValue)
                .font(.title.weight(.bold))
            switch section {
            case .channels:
                ChannelSettingsSection()
            case .profile:
                ProfileSettingsSection()
            case .server:
                ServerSettingsSection()
            }
        }
        .padding(.horizontal, 80)
        .padding(.top, 60)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Color(white: 0.08).ignoresSafeArea())
        .onExitCommand { dismiss() }
    }
}

/// Nastavení → Server: the DVB-T2 gateway (address + key); without it the Anténa group stays hidden.
/// "O aplikaci" at the end.
struct ServerSettingsSection: View {
    static let disclaimer = "Neoficiální projekt, není spojen s Českou televizí. ČT, ČT1 … a iVysílání jsou ochranné známky České televize."

    @Environment(AppModel.self) private var model
    @State private var address = ""
    @State private var key = ""
    @State private var statusText: String?
    @State private var isChecking = false
    @State private var browser = GatewayBrowser()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                Text("Anténa (DVB-T2)")
                    .font(.title2.weight(.bold))
                Text("Brána cz-tv-hub na Raspberry Pi doma. V místní síti ji aplikace najde sama, "
                     + "přes Tailscale zadejte její jméno (např. raspberrypi.vase-sit.ts.net).")
                    .font(.callout)
                    .foregroundStyle(.secondary)

                TextField("Adresa brány", text: $address)
                    .textContentType(.URL)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                SecureField("Klíč brány", text: $key)

                HStack(spacing: 32) {
                    Button(isChecking ? "Ověřuji…" : "Uložit a ověřit") { Task { await saveAndCheck() } }
                        .disabled(isChecking)
                    if model.gateway != nil {
                        Button("Odebrat bránu", role: .destructive) {
                            model.setGateway(nil)
                            address = ""
                            key = ""
                            statusText = "Brána odebrána. Skupina Anténa je skrytá."
                        }
                    }
                }
                if let statusText {
                    Text(statusText).font(.callout)
                }

                if model.gateway != nil, model.images.gatewayAllows {
                    @Bindable var images = model.images
                    Toggle(isOn: $images.tvAllows) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Náhledy anténních pořadů")
                            Text("Snímek z nahrávky v detailu TV průvodce. Jen v paměti, nic se neukládá.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.top, 10)
                }

                Text("Nalezeno v místní síti")
                    .font(.headline)
                    .padding(.top, 20)
                if browser.services.isEmpty {
                    Text("Žádná brána zatím nenalezena.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                ForEach(browser.services) { service in
                    Button(service.name) {
                        Task {
                            if let found = await browser.resolve(service) {
                                address = found
                                statusText = "Adresa doplněna. Zadejte klíč a uložte."
                            } else {
                                statusText = "Bránu \(service.name) se nepodařilo spojit."
                            }
                        }
                    }
                }

                Text("O aplikaci")
                    .font(.headline)
                    .padding(.top, 40)
                Text(Self.disclaimer)
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: 1100, alignment: .leading)
            .padding(.vertical, 40)
        }
        .onAppear {
            if let gateway = model.gateway, address.isEmpty {
                address = gateway.address
                key = gateway.key
            }
            browser.start()
        }
        .onDisappear { browser.stop() }
    }

    private func saveAndCheck() async {
        guard let config = GatewayConfig(address: address, key: key) else {
            statusText = "Zadejte adresu brány i klíč."
            return
        }
        isChecking = true
        defer { isChecking = false }
        do {
            let status = try await DVBClient(config: config).status()
            model.setGateway(config)
            await model.images.refreshStatus(force: true)
            let tuners = status.tuners.count
            statusText = status.tvheadendReachable
                ? "Uloženo. Brána odpovídá · tunery: \(tuners) · záznamy \(status.retentionDays) dny."
                : "Uloženo, ale brána nevidí TVHeadend (tuner). Zkontrolujte Raspberry Pi."
        } catch {
            statusText = "Neuloženo: \(AppModel.message(for: error))"
        }
    }
}

/// "Verze 1.0 (build 123 · abc1234 · 23. 10. 15:20)"; `BuildStamp` is generated at build time (project.yml).
enum BuildInfo {
    static var versionLine: String {
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "?"
        return "Verze \(version) (build \(BuildStamp.number) · \(BuildStamp.commit) · \(BuildStamp.time))"
    }
}
