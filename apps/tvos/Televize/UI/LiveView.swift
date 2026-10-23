import CTCore
import SwiftUI

/// "Živě": one mixed list of CT internet and Anténa channels with what is on air now, in this TV's
/// order (Nastavení → Kanály: hidden channels and switched-off sources left out). Select → live playback.
struct LiveView: View {
    @Environment(AppModel.self) private var model
    @State private var onAir: [String: Programme] = [:]
    @State private var antennaChannels: [DVBChannel] = []
    @State private var antennaOnAir: [String: DVBEvent] = [:]
    @State private var antennaError: String?

    private let columns = Array(repeating: GridItem(.flexible(), spacing: 48), count: 3)

    var body: some View {
        let prefs = model.channelPrefs
        let channels = prefs.channels(antenna: antennaChannels).visible
        // "All hidden" only once the Anténa channels are known (or Anténa is off / not configured).
        let antennaPending = model.antenna != nil && prefs.prefs.isOn(.dvb) && antennaChannels.isEmpty && antennaError == nil
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
                    if let antennaError, model.antenna != nil, prefs.prefs.isOn(.dvb) {
                        Text(antennaError).font(.callout).foregroundStyle(.secondary)
                    }
                    LazyVGrid(columns: columns, spacing: 56) {
                        ForEach(channels) { channel in
                            card(channel, now: context.date)
                                .id(channel.key)
                        }
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 40)
            }
            #if DEBUG
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
        .task(id: model.gateway) {
            // Schedules are cached, so this only hits the network when today's plan is stale.
            while !Task.isCancelled {
                onAir = await model.schedules.onAir(at: Date())
                await loadAntenna()
                try? await Task.sleep(for: .seconds(60))
            }
        }
    }

    private func card(_ channel: MixedChannel, now: Date) -> some View {
        Button {
            switch channel {
            case .ct(let ct): model.play(.live(ct))
            case .antenna(let dvb): model.playAntennaLive(dvb)
            }
        } label: {
            switch channel {
            case .ct(let ct):
                let programme = onAir[ct.slug]
                ChannelCard(name: ct.name, source: .ct, title: programme?.title,
                            start: programme?.start, end: programme?.end, now: now)
            case .antenna(let dvb):
                let event = antennaOnAir[dvb.id]
                ChannelCard(name: dvb.name, source: .dvb, title: event?.title,
                            start: event?.start, end: event?.end, now: now)
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
    let name: String
    let source: ChannelPrefs.Source
    let title: String?
    let start: Date?
    let end: Date?
    let now: Date

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 14) {
                Text(name)
                    .font(.title2.weight(.bold))
                    .lineLimit(1)
                Spacer(minLength: 0)
                SourceIcon(source: source)
                    .font(.system(size: 26))
                    .foregroundStyle(.secondary)
            }
            if let title, let start, let end, end > now {
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
    }
}
