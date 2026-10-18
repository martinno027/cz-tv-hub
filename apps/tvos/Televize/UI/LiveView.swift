import CTCore
import SwiftUI

/// "Živě": the six CT channels with what is on air now, plus the "Anténa" group when a
/// DVB-T2 gateway is configured. Select → live playback.
struct LiveView: View {
    @Environment(AppModel.self) private var model
    @State private var onAir: [String: Programme] = [:]
    @State private var antennaChannels: [DVBChannel] = []
    @State private var antennaOnAir: [String: DVBEvent] = [:]
    @State private var antennaError: String?

    private let columns = Array(repeating: GridItem(.flexible(), spacing: 48), count: 3)

    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { context in
            ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 36) {
                    if model.antenna != nil { sectionTitle("Internet ČT") }
                    LazyVGrid(columns: columns, spacing: 56) {
                        ForEach(Channels.all) { channel in
                            let programme = onAir[channel.slug]
                            Button {
                                model.play(.live(channel))
                            } label: {
                                ChannelCard(name: channel.name, title: programme?.title,
                                            start: programme?.start, end: programme?.end, now: context.date)
                            }
                            .buttonStyle(.card)
                        }
                    }
                    if model.antenna != nil {
                        sectionTitle("Anténa")
                            .id("antenna")
                        if let antennaError {
                            Text(antennaError).font(.callout).foregroundStyle(.secondary)
                        }
                        LazyVGrid(columns: columns, spacing: 56) {
                            ForEach(antennaChannels) { channel in
                                let event = antennaOnAir[channel.id]
                                Button {
                                    model.playAntennaLive(channel)
                                } label: {
                                    ChannelCard(name: channel.name, title: event?.title,
                                                start: event?.start, end: event?.end, now: context.date)
                                }
                                .buttonStyle(.card)
                            }
                        }
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 40)
            }
            #if DEBUG
            // `-liveScroll antenna` shows the Anténa group, for simulator screenshots.
            .onChange(of: antennaChannels.count) { _, count in
                if count > 0, UserDefaults.standard.string(forKey: "liveScroll") == "antenna" {
                    proxy.scrollTo("antenna", anchor: .top)
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

    private func sectionTitle(_ text: String) -> some View {
        Text(text).font(.title3.weight(.bold)).foregroundStyle(.secondary)
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
    let title: String?
    let start: Date?
    let end: Date?
    let now: Date

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(name)
                .font(.title2.weight(.bold))
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
