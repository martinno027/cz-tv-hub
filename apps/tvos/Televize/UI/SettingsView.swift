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
        // Nastavení opens: a newer household default and a newer profile version are applied, a pending
        // profile change is sent.
        .task { await model.refreshFromGateway() }
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
        // full screen, so over the tab bar's clock: the same clock at the same place
        .clockTopRight()
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

                TailnetSettings(address: address)

                HStack(spacing: 32) {
                    Button(isChecking ? "Ověřuji…" : "Uložit a ověřit") { Task { await saveAndCheck() } }
                        .disabled(isChecking)
                    if model.savedGateway != nil {
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

                if model.savedGateway != nil, model.images.gatewayAllows {
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
            if let gateway = model.savedGateway, address.isEmpty {
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
            // With Tailscale on, the bridge moves to the new address and the check runs through it (once connected).
            if model.tailnet.enabled, await model.tailnet.start(target: TailnetRules.target(address: address)) {
                await model.tailnet.waitUntilRunning()
            }
            let status = try await DVBClient(config: model.effectiveGateway(config)!).status()
            model.setGateway(config)
            await model.images.refreshStatus(force: true)
            let tuners = status.tuners.count
            statusText = status.tvheadendReachable
                ? "Uloženo. Brána odpovídá · tunery: \(tuners) · záznamy \(status.retentionDays) dny."
                : "Uloženo, ale brána nevidí TVHeadend (tuner). Zkontrolujte Raspberry Pi."
        } catch {
            statusText = "Neuloženo: \(AppModel.message(for: error))"
            // Not saved: the bridge goes back to the saved gateway.
            if model.tailnet.enabled, let saved = model.savedGateway {
                await model.tailnet.start(target: TailnetRules.target(address: saved.address))
            }
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

/// "Připojovat přes Tailscale" in Nastavení → Server (spec/apps.md "Tailscale v aplikaci"): the switch, the node's status,
/// the login QR code, login with an auth key and logout. The status is polled every 2 s while this is on screen.
private struct TailnetSettings: View {
    /// The gateway address on screen: the bridge's target when the switch goes on.
    let address: String

    @Environment(AppModel.self) private var model
    @State private var showsKeyLogin = false
    @State private var authKey = ""
    @State private var isBusy = false

    var body: some View {
        let tailnet = model.tailnet
        let status = tailnet.status
        // The QR code beside the switch, so it is on screen without scrolling (it takes no focus).
        HStack(alignment: .top, spacing: 48) {
            controls(tailnet, status)
            if tailnet.enabled, status.qr, let modules = tailnet.qrModules, let url = tailnet.info?.loginURL {
                VStack(spacing: 12) {
                    QRCodeView(modules: modules)
                    Text(url)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.6)
                }
                .frame(width: max(CGFloat(modules.count) * QRCodeView.module, 420))
            }
        }
        .onAppear { tailnet.settingsOpen = true }
        .onDisappear { tailnet.settingsOpen = false }
    }

    private func controls(_ tailnet: TailnetController, _ status: TailnetStatus) -> some View {
        VStack(alignment: .leading, spacing: 20) {
            Toggle(isOn: Binding(get: { tailnet.enabled }, set: { on in
                Task {
                    isBusy = true
                    await model.setTailnet(on, address: address)
                    isBusy = false
                }
            })) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(TailnetRules.switchLabel)
                    Text(TailnetRules.hint)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .disabled(isBusy)
            if tailnet.enabled {
                Text(status.line).font(.callout)
                HStack(spacing: 32) {
                    if !showsKeyLogin {
                        Button("Přihlásit klíčem") { showsKeyLogin = true }
                    }
                    if status.logout {
                        Button("Odhlásit z Tailscale", role: .destructive) {
                            Task {
                                isBusy = true
                                await tailnet.logout()
                                await model.setTailnet(false, address: address)
                                isBusy = false
                            }
                        }
                        .disabled(isBusy)
                    }
                }
                if showsKeyLogin {
                    HStack(spacing: 32) {
                        SecureField("Klíč Tailscale (tskey-auth-…)", text: $authKey)
                        Button("Přihlásit") {
                            // Used once for this start, never stored (the node's state replaces it).
                            let key = authKey.trimmingCharacters(in: .whitespacesAndNewlines)
                            authKey = ""
                            showsKeyLogin = false
                            Task {
                                isBusy = true
                                await tailnet.start(target: TailnetRules.target(address: address), authKey: key)
                                isBusy = false
                            }
                        }
                        .disabled(isBusy || authKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                }
            }
        }
    }
}

/// The login URL's QR code: black modules on white, 8 pt per module (at least 8 px), quiet zone included, centred
/// above its URL.
private struct QRCodeView: View {
    let modules: [[Bool]]
    static let module: CGFloat = 8

    var body: some View {
        let side = CGFloat(modules.count) * Self.module
        Canvas { context, _ in
            context.fill(Path(CGRect(x: 0, y: 0, width: side, height: side)), with: .color(.white))
            for (row, line) in modules.enumerated() {
                for (column, dark) in line.enumerated() where dark {
                    let rect = CGRect(x: CGFloat(column) * Self.module, y: CGFloat(row) * Self.module,
                                      width: Self.module, height: Self.module)
                    context.fill(Path(rect), with: .color(.black))
                }
            }
        }
        .frame(width: side, height: side)
        .accessibilityLabel("QR kód pro přihlášení do Tailscale")
    }
}
