import CTCore
import os
import SwiftUI

private let zapLog = Logger(subsystem: "cz.cztvhub.tv", category: "zap")

/// Zapping in the live player (spec/apps.md "Channel numbers and zapping"): the remote's up/down (down = next,
/// while the transport bar is hidden) and digits step through the channels shown on this TV,
/// in the list as it was when the player opened. Every press moves the target and shows the banner at once;
/// the stream is resolved only once the presses stop, and the old picture stays until the new one is ready.
@MainActor
@Observable
final class ChannelZapper {
    struct Banner: Equatable {
        let key: String
        let number: Int
        let name: String
        var airing: String?
    }

    private(set) var banner: Banner?
    @ObservationIgnored private(set) var entry: NumberEntry!

    @ObservationIgnored private weak var model: AppModel?
    @ObservationIgnored private weak var controller: PlayerController?
    @ObservationIgnored private var channels: [MixedChannel] = []
    @ObservationIgnored private var playingKey: String
    @ObservationIgnored private var targetKey: String
    @ObservationIgnored private var zapTask: Task<Void, Never>?
    @ObservationIgnored private var bannerTask: Task<Void, Never>?
    @ObservationIgnored private var stopped = false

    init(model: AppModel, controller: PlayerController, playing channel: MixedChannel, channels: [MixedChannel]) {
        self.model = model
        self.controller = controller
        self.channels = channels
        playingKey = channel.key
        targetKey = channel.key
        entry = NumberEntry { [weak self] number in self?.pick(number: number) }
    }

    private var keys: [String] { channels.map(\.key) }

    /// Only when the list was not known yet as the player opened.
    func setChannels(_ list: [MixedChannel]) {
        if channels.isEmpty { channels = list }
    }

    /// ▲/▼: `by` = −1 previous, +1 next.
    func step(by delta: Int) {
        guard !stopped, let key = ChannelNumbers.step(keys, from: targetKey, by: delta) else { return }
        entry.cancel()
        target(key, after: ChannelNumbers.zapDelay)
    }

    func digit(_ digit: Int) {
        guard !stopped else { return }
        entry.type(digit, count: channels.count)
    }

    func stop() {
        stopped = true
        zapTask?.cancel()
        bannerTask?.cancel()
        entry.cancel()
    }

    private func pick(number: Int) {
        guard let key = ChannelNumbers.resolve(keys, number: number) else {
            entry.show(missing: number)
            return
        }
        target(key, after: nil)
    }

    private func target(_ key: String, after delay: Duration?) {
        targetKey = key
        showBanner(for: key)
        zapTask?.cancel()
        zapTask = Task { [weak self] in
            if let delay { try? await Task.sleep(for: delay) }
            guard !Task.isCancelled else { return }
            await self?.switchTo(key)
        }
    }

    private func switchTo(_ key: String) async {
        guard key != playingKey, let model, let controller, let channel = channels.first(where: { $0.key == key }),
              let start = model.liveStart(channel) else { return }
        zapLog.info("zap to \(key, privacy: .public)")
        do {
            let source = try await start.resolve()
            // A later press took over.
            guard !stopped, targetKey == key else { return }
            controller.switchLive(to: source, resolve: start.resolve, liveTitle: start.liveTitle, offAir: start.offAir)
            playingKey = key
        } catch let error as CTError where error.code == PSPStreams.offAirCode {
            // A Chamber stream that is not on: the banner says "Nevysílá se" with the message; the old picture
            // stays and ▲/▼ steps on from this channel.
            guard !stopped, targetKey == key else { return }
            zapLog.info("zap to \(key, privacy: .public): not on air")
            showBanner(for: key)
            entry.show(message: error.userMessage)
        } catch {
            guard !stopped, targetKey == key else { return }
            zapLog.error("zap failed: \(String(describing: error), privacy: .public)")
            targetKey = playingKey
            showBanner(for: playingKey)
            entry.show(message: AppModel.message(for: error))
        }
    }

    private func showBanner(for key: String) {
        guard let index = keys.firstIndex(of: key) else { return }
        let channel = channels[index]
        banner = Banner(key: key, number: index + 1, name: channel.name)
        bannerTask?.cancel()
        bannerTask = Task { [weak self, weak model] in
            if let airing = await model?.airingTitle(channel), !Task.isCancelled, self?.banner?.key == key {
                self?.banner?.airing = airing
            }
            try? await Task.sleep(for: ChannelNumbers.overlayDuration)
            guard !Task.isCancelled else { return }
            self?.banner = nil
        }
    }
}

/// `<number>  <channel>` and the programme airing now, top left, for a few seconds after a zap.
struct ZapBanner: View {
    let banner: ChannelZapper.Banner

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 24) {
                Text("\(banner.number)").monospacedDigit().foregroundStyle(.secondary)
                Text(banner.name)
            }
            .font(.system(size: 48, weight: .bold))
            if let airing = banner.airing {
                Text(airing).font(.title3).lineLimit(1)
            }
        }
        .padding(.horizontal, 36)
        .padding(.vertical, 24)
        .frame(maxWidth: 900, alignment: .leading)
        .background(.black.opacity(0.7), in: RoundedRectangle(cornerRadius: 16))
        .padding(60)
        .allowsHitTesting(false)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("zapBanner")
    }
}
