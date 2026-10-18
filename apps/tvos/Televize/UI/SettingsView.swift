import CTCore
import SwiftUI

/// "Nastavení": the DVB-T2 gateway (address + key). Without it the Anténa group stays hidden.
struct SettingsView: View {
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
            let tuners = status.tuners.count
            statusText = status.tvheadendReachable
                ? "Uloženo. Brána odpovídá · tunery: \(tuners) · záznamy \(status.retentionDays) dny."
                : "Uloženo, ale brána nevidí TVHeadend (tuner). Zkontrolujte Raspberry Pi."
        } catch {
            statusText = "Neuloženo: \(AppModel.message(for: error))"
        }
    }
}
