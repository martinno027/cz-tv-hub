import CTCore
import SwiftUI

enum ChannelPrefsUI {
    static let allHiddenMessage = "Všechny kanály jsou skryté. Zobrazíte je v Nastavení → Kanály."
}

/// Nastavení → Kanály (its own section, see SettingsView): hide and reorder channels on this TV (spec/apps.md "Kanály").
/// One list per group; each row is `[☑ name] [▲] [▼]`.
struct ChannelSettingsSection: View {
    @Environment(AppModel.self) private var model
    @State private var antennaChannels: [DVBChannel] = []
    @State private var antennaError: String?
    @FocusState private var focused: Control?

    private enum Control: Hashable {
        case toggle(String), up(String), down(String)
    }

    private struct Item: Identifiable {
        let id: String  // ChannelPrefs key
        let name: String
    }

    var body: some View {
        let prefs = model.channelPrefs
        // The list scrolls itself: focus stays on the moved row's arrow, so tvOS would not scroll for it.
        ScrollViewReader { proxy in
        ScrollView {
        VStack(alignment: .leading, spacing: 20) {
            Text("Které kanály tato televize ukazuje v Živě a v TV průvodci, a v jakém pořadí. Platí jen pro toto zařízení.")
                .font(.callout)
                .foregroundStyle(.secondary)

            if model.antenna != nil { groupTitle("Internet ČT") }
            list(prefs.internet().all.map { Item(id: ChannelPrefs.ctKey($0), name: $0.name) }, proxy: proxy)

            if model.antenna != nil {
                groupTitle("Anténa")
                if let antennaError {
                    Text(antennaError).font(.callout).foregroundStyle(.secondary)
                } else if antennaChannels.isEmpty {
                    Text("Načítám kanály antény…").font(.callout).foregroundStyle(.secondary)
                }
                list(prefs.antenna(antennaChannels).all.map { Item(id: ChannelPrefs.dvbKey($0), name: $0.name) }, proxy: proxy)
            }

            Button("Obnovit výchozí pořadí") {
                prefs.update { $0.reset() }
            }
            .disabled(prefs.prefs == ChannelPrefs())
            .padding(.top, 10)
        }
        .frame(maxWidth: 1100, alignment: .leading)
        .padding(.vertical, 20)
        }
        }
        .task(id: model.gateway) { await loadAntenna() }
    }

    private func groupTitle(_ text: String) -> some View {
        Text(text).font(.headline).foregroundStyle(.secondary).padding(.top, 10)
    }

    private func list(_ items: [Item], proxy: ScrollViewProxy) -> some View {
        let keys = items.map(\.id)
        return VStack(alignment: .leading, spacing: 12) {
            ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                let hidden = model.channelPrefs.prefs.isHidden(item.id)
                HStack(spacing: 24) {
                    Button {
                        model.channelPrefs.update { $0.toggleHidden(item.id) }
                    } label: {
                        Label(item.name, systemImage: hidden ? "square" : "checkmark.square.fill")
                            .frame(width: 560, alignment: .leading)
                            .opacity(hidden ? 0.5 : 1)
                    }
                    .focused($focused, equals: .toggle(item.id))
                    .accessibilityValue(hidden ? "skrytý" : "zobrazený")

                    Button {
                        move(item.id, by: -1, in: keys, proxy: proxy)
                    } label: {
                        Image(systemName: "chevron.up")
                    }
                    .disabled(index == 0)
                    .focused($focused, equals: .up(item.id))
                    .accessibilityLabel("Posunout \(item.name) nahoru")

                    Button {
                        move(item.id, by: 1, in: keys, proxy: proxy)
                    } label: {
                        Image(systemName: "chevron.down")
                    }
                    .disabled(index == items.count - 1)
                    .focused($focused, equals: .down(item.id))
                    .accessibilityLabel("Posunout \(item.name) dolů")
                }
                .id(item.id)
            }
        }
        .focusSection()
    }

    /// Focus follows the moved row (rows are identified by key); at the edge the pressed button
    /// becomes disabled, so focus goes to the row's other arrow.
    private func move(_ key: String, by delta: Int, in keys: [String], proxy: ScrollViewProxy) {
        model.channelPrefs.update { $0.move(key, by: delta, in: keys) }
        withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo(key, anchor: .center) }
        guard let index = keys.firstIndex(of: key) else { return }
        let newIndex = index + delta
        if delta < 0, newIndex == 0 {
            focused = .down(key)
        } else if delta > 0, newIndex == keys.count - 1 {
            focused = .up(key)
        } else {
            focused = delta < 0 ? .up(key) : .down(key)
        }
    }

    private func loadAntenna() async {
        guard let antenna = model.antenna else {
            antennaChannels = []
            antennaError = nil
            return
        }
        do {
            antennaChannels = try await antenna.channels()
            antennaError = nil
        } catch is CancellationError {
            return
        } catch {
            antennaError = "Kanály antény se nepodařilo načíst: \(AppModel.message(for: error))"
        }
    }
}
