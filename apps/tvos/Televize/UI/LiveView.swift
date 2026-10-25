import CTCore
import SwiftUI

/// "Živě": one mixed list of CT internet, Anténa and Poslanecká sněmovna channels with what is on air now, in this TV's
/// order (Nastavení → Kanály: hidden channels and switched-off sources left out). Select → live playback.
struct LiveView: View {
    @Environment(AppModel.self) private var model
    @State private var onAir: [String: Programme] = [:]
    @State private var antennaChannels: [DVBChannel] = []
    @State private var antennaOnAir: [String: DVBEvent] = [:]
    @State private var antennaError: String?

    private let columns = Array(repeating: GridItem(.flexible(), spacing: 48), count: 3)

    private struct LoadKey: Equatable {
        let gateway: GatewayConfig?
        let reloads: Int
    }

    var body: some View {
        let prefs = model.channelPrefs
        let channels = prefs.channels(antenna: antennaChannels).visible
        // Tailscale is being checked or not connected yet: "Připojuji se k Tailscale…" instead of the gateway's error
        // (spec/apps.md T18, T21).
        let connecting = model.tailnet.connecting && model.antenna != nil && prefs.prefs.isOn(.dvb)
        // "All hidden" only once the Anténa channels are known (or Anténa is off / not configured).
        let antennaPending = model.antenna != nil && prefs.prefs.isOn(.dvb) && antennaChannels.isEmpty
            && (antennaError == nil || connecting)
        return TimelineView(.periodic(from: .now, by: 30)) { context in
            ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 36) {
                    if channels.isEmpty && !antennaPending {
                        Text(ChannelPrefsUI.allHiddenMessage)
                            .font(.headline)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .center)
                            .padding(.top, 200)
                    }
                    if connecting {
                        Text(TailnetRules.connectingLine).font(.callout).foregroundStyle(.secondary)
                    } else if let antennaError, model.antenna != nil, prefs.prefs.isOn(.dvb) {
                        Text(antennaError).font(.callout).foregroundStyle(.secondary)
                    }
                    LazyVGrid(columns: columns, spacing: 56) {
                        ForEach(Array(channels.enumerated()), id: \.element.id) { index, channel in
                            card(channel, number: index + 1, now: context.date)
                                .id(channel.key)
                        }
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 40)
            }
            #if DEBUG
            // `-liveScroll psp` scrolls to the first Poslanecká sněmovna card, for simulator screenshots.
            .task {
                if UserDefaults.standard.string(forKey: "liveScroll") == "psp",
                   let first = channels.first(where: { $0.source == .psp }) {
                    try? await Task.sleep(for: .milliseconds(500))
                    proxy.scrollTo(first.key, anchor: .top)
                }
            }
            // `-liveScroll antenna` scrolls to the first Anténa card, for simulator screenshots.
            .onChange(of: antennaChannels.count) { _, count in
                if count > 0, UserDefaults.standard.string(forKey: "liveScroll") == "antenna",
                   let first = channels.first(where: { $0.source == .dvb }) {
                    proxy.scrollTo(first.key, anchor: .top)
                }
            }
            #endif
            }
        }
        // "Is it on now?" of the Chamber's streams shown here, only while this screen is on screen.
        .watchesParliament(channels.compactMap(\.parliamentStream))
        .task(id: LoadKey(gateway: model.gateway, reloads: model.gatewayReloads)) {
            // Schedules are cached, so this only hits the network when today's plan is stale.
            while !Task.isCancelled {
                onAir = await model.schedules.onAir(at: Date())
                await loadAntenna()
                try? await Task.sleep(for: .seconds(60))
            }
        }
    }

    private func card(_ channel: MixedChannel, number: Int, now: Date) -> some View {
        Button {
            model.playLive(channel)
        } label: {
            switch channel {
            case .ct(let ct):
                let programme = onAir[ct.slug]
                ChannelCard(number: number, name: ct.name, source: .ct, title: programme?.title,
                            start: programme?.start, end: programme?.end, now: now)
            case .antenna(let dvb):
                let event = antennaOnAir[dvb.id]
                ChannelCard(number: number, name: dvb.name, source: .dvb, title: event?.title,
                            start: event?.start, end: event?.end, now: now)
            case .parliament(let stream):
                ChannelCard(number: number, name: stream.name, source: .psp, title: nil, start: nil, end: nil, now: now,
                            parliament: model.parliament.state(of: stream.key))
            }
        }
        .buttonStyle(.card)
    }

    private func loadAntenna() async {
        guard let antenna = model.antenna else {
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

private struct ChannelCard: View {
    /// Channel number on this TV (spec/apps.md "Channel numbers and zapping").
    let number: Int
    let name: String
    let source: ChannelPrefs.Source
    let title: String?
    let start: Date?
    let end: Date?
    let now: Date
    /// Poslanecká sněmovna: whether the stream is on (badge) or not (dimmed card).
    var parliament: PSPLiveState? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 14) {
                Text("\(number)")
                    .font(.title3.weight(.semibold).monospacedDigit())
                    .foregroundStyle(.secondary)
                // Chamber stream names wrap ("Výbory a semináře 1/2/3" must stay distinguishable)
                Text(name)
                    .font(.title2.weight(.bold))
                    .lineLimit(parliament == nil ? 1 : 2)
                    .fixedSize(horizontal: false, vertical: parliament != nil)
                Spacer(minLength: 0)
                SourceIcon(source: source)
                    .font(.system(size: 26))
                    .foregroundStyle(.secondary)
            }
            if let parliament {
                Spacer(minLength: 0)
                switch parliament {
                case .live:
                    HStack(spacing: 16) {
                        LiveBadge()
                        Text(PSPStreams.liveText).font(.headline)
                    }
                case .off:
                    Text(PSPStreams.offText).font(.headline).foregroundStyle(.secondary)
                case .unknown:
                    Text(PSPStreams.unknownText).font(.headline).foregroundStyle(.secondary)
                }
            } else if let title, let start, let end, end > now {
                Text(title)
                    .font(.headline)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                HStack {
                    Text("\(Formatting.time(start))–\(Formatting.time(end))")
                    Spacer()
                    Text("Živě").foregroundStyle(.red)
                }
                .font(.callout)
                .foregroundStyle(.secondary)
                ProgressView(value: min(1, max(0, now.timeIntervalSince(start) / end.timeIntervalSince(start))))
                    .tint(.red)
            } else {
                Spacer(minLength: 0)
                Text("Živé vysílání")
                    .font(.headline)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(28)
        .frame(width: 500, height: 280, alignment: .topLeading)
        .opacity(parliament == .off ? 0.45 : 1)
    }
}
