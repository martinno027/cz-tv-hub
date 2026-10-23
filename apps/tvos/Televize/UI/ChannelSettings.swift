import CTCore
import SwiftUI

enum ChannelPrefsUI {
    static let allHiddenMessage = "Všechny kanály jsou skryté. Zobrazíte je v Nastavení → Kanály."
}

/// Where a channel comes from: internet (CT) or the household's antenna (spec/apps.md "Source icon").
struct SourceIcon: View {
    let source: ChannelPrefs.Source

    var body: some View {
        Image(systemName: source == .ct ? "globe" : "antenna.radiowaves.left.and.right")
            .accessibilityLabel(source == .ct ? "internet" : "anténa")
    }
}

/// Nastavení → Kanály (its own section, see SettingsView): hide and reorder channels on this TV (spec/apps.md "Kanály").
/// Source switches first, then one mixed list; each row is `[icon ☑ name] [▲] [▼]`.
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
        let source: ChannelPrefs.Source
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

            VStack(alignment: .leading, spacing: 12) {
                sourceSwitch(.ct, title: "Internetová TV (ČT)")
                if model.antenna != nil { sourceSwitch(.dvb, title: "Anténa") }
            }
            .focusSection()

            groupTitle("Pořadí kanálů")
            if model.antenna != nil, prefs.prefs.isOn(.dvb) {
                if let antennaError {
                    Text(antennaError).font(.callout).foregroundStyle(.secondary)
                } else if antennaChannels.isEmpty {
                    Text("Načítám kanály antény…").font(.callout).foregroundStyle(.secondary)
                }
            }
            let items = prefs.channels(antenna: antennaChannels).all.map { Item(id: $0.key, name: $0.name, source: $0.source) }
            if items.isEmpty {
                Text("Žádný zdroj není zapnutý.").font(.callout).foregroundStyle(.secondary)
            }
            list(items, proxy: proxy)

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

    /// Switches a whole source (all CT internet / all Anténa channels) on or off.
    private func sourceSwitch(_ source: ChannelPrefs.Source, title: String) -> some View {
        let isOn = model.channelPrefs.prefs.isOn(source)
        return Button {
            model.channelPrefs.update { $0.setOn(source, !isOn) }
        } label: {
            HStack(spacing: 16) {
                Image(systemName: isOn ? "checkmark.square.fill" : "square")
                SourceIcon(source: source)
                Text(title)
            }
            .frame(width: 560, alignment: .leading)
        }
        .accessibilityValue(isOn ? "zapnuto" : "vypnuto")
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
                        HStack(spacing: 16) {
                            Image(systemName: hidden ? "square" : "checkmark.square.fill")
                            SourceIcon(source: item.source)
                                .foregroundStyle(.secondary)
                                .frame(width: 34)
                            Text(item.name)
                        }
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
