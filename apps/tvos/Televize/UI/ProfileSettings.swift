import CTCore
import SwiftUI

/// Nastavení → Profil (spec/apps.md "Profil"): sign in to a profile on the gateway or create one; signed in, the name,
/// whether the settings are saved, and Odhlásit. Passwords are typed into masked fields and never kept.
struct ProfileSettingsSection: View {
    enum Mode: String, CaseIterable, Identifiable {
        case signIn = "Přihlásit se"
        case create = "Vytvořit profil"
        var id: String { rawValue }
    }

    @Environment(AppModel.self) private var model
    @State private var mode: Mode = .signIn
    @State private var name = ""
    @State private var password = ""
    @State private var repeated = ""
    @State private var errorText: String?

    var body: some View {
        let profile = model.profile
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                if let signedIn = profile.profileName {
                    signedInView(signedIn, profile: profile)
                } else if !profile.hasGateway {
                    Text(ProfileRules.needsGateway)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                } else {
                    signedOutView(profile)
                }
            }
            .frame(maxWidth: 1100, alignment: .leading)
            .padding(.vertical, 40)
        }
        .onDisappear(perform: clearPasswords)
    }

    private func signedInView(_ signedIn: String, profile: ProfileSyncController) -> some View {
        VStack(alignment: .leading, spacing: 28) {
            Text("Přihlášen: \(signedIn)")
                .font(.title2.weight(.bold))
            Text(profile.pushFailed ? ProfileRules.waitingText : ProfileRules.savedText)
                .font(.callout)
                .foregroundStyle(.secondary)
            Text("Kanály a náhledy anténních pořadů se ukládají do profilu. Po odhlášení platí zase nastavení této televize.")
                .font(.callout)
                .foregroundStyle(.secondary)
            Button(profile.isBusy ? "Odhlašuji…" : "Odhlásit") {
                Task { await profile.signOut() }
            }
            .disabled(profile.isBusy)
        }
    }

    private func signedOutView(_ profile: ProfileSyncController) -> some View {
        VStack(alignment: .leading, spacing: 28) {
            Text("Nastavení této televize (kanály, náhledy) můžete uložit do profilu na bráně a načíst na jiné televizi.")
                .font(.callout)
                .foregroundStyle(.secondary)
            HStack(spacing: 32) {
                ForEach(Mode.allCases) { item in
                    Button {
                        mode = item
                        errorText = nil
                        clearPasswords()
                    } label: {
                        Text(item.rawValue).fontWeight(mode == item ? .bold : .regular)
                    }
                    .opacity(mode == item ? 1 : 0.6)
                }
            }
            TextField("Jméno", text: $name)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
            SecureField("Heslo", text: $password)
            if mode == .create {
                SecureField("Heslo znovu", text: $repeated)
            }
            Button(profile.isBusy ? "Čekám na bránu…" : (mode == .signIn ? "Přihlásit" : "Vytvořit")) {
                Task { await submit(profile) }
            }
            .disabled(profile.isBusy)
            if let errorText {
                Text(errorText).font(.callout)
            }
        }
    }

    private func submit(_ profile: ProfileSyncController) async {
        errorText = nil
        do {
            switch mode {
            case .signIn: try await profile.signIn(name: name, password: password)
            case .create: try await profile.create(name: name, password: password, repeated: repeated)
            }
            name = ""
            clearPasswords()
        } catch {
            errorText = AppModel.message(for: error)
        }
    }

    private func clearPasswords() {
        password = ""
        repeated = ""
    }
}
