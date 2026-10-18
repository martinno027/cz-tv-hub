import CTCore
import Foundation
import Observation
import os

let log = Logger(subsystem: "cz.cztvhub.tv", category: "app")

/// One playback. The controller lives exactly as long as the session.
struct PlayerSession: Identifiable {
    let id = UUID()
    let controller: PlayerController
}

@MainActor
@Observable
final class AppModel {
    let schedules: ScheduleStore
    let planner: PlaybackPlanner

    var session: PlayerSession?
    var alertMessage: String?
    /// UNSUPPORTED_DRM from CT: shown as a full-screen notice with "Otevřít v iVysílání".
    var drmError: CTError?
    /// Shown once the player has been dismissed (an alert cannot appear during the dismissal).
    var messageAfterPlayer: String?
    private(set) var isResolving = false
    /// Set by the player screen; a session whose screen never appeared must not block later plays.
    var isPlayerOnScreen = false

    /// "Anténa" (DVB-T2 gateway). Nil when not configured: the Anténa group is hidden.
    private(set) var gateway: GatewayConfig?
    private(set) var antenna: AntennaStore?

    init(client: CTClient = CTClient()) {
        schedules = ScheduleStore(client: client)
        planner = PlaybackPlanner(client: client)
        setGateway(GatewayStore.load(), persist: false)
    }

    /// Applies (and by default stores in the Keychain) a gateway configuration; nil removes it.
    func setGateway(_ config: GatewayConfig?, persist: Bool = true) {
        if persist {
            if let config { GatewayStore.save(config) } else { GatewayStore.delete() }
        }
        gateway = config
        antenna = config.map { AntennaStore(client: DVBClient(config: $0)) }
    }

    /// Resolves the stream on the device right now, then opens the player. Every outcome is
    /// visible: the player, or a Czech alert.
    func play(_ request: PlaybackRequest) {
        log.info("play \(String(describing: request), privacy: .public)")
        let planner = planner
        open { try await planner.resolve(request) }
    }

    /// Live picture of an Anténa channel.
    func playAntennaLive(_ channel: DVBChannel) {
        guard let client = antenna?.client else { return }
        let url = client.liveURL(channelID: channel.id)
        open { try await Self.prepare(client, url: url, title: channel.name, mode: .live) }
    }

    /// A recording from the gateway; while it is still recording (`finished: false`) this is the
    /// start-over. Only finished recordings offer "Přeskočit reklamu" (spec/ad-breaks.md, v1).
    func playAntennaRecording(id: String, title: String, finished: Bool) {
        guard let client = antenna?.client else { return }
        let url = client.recordingURL(id: id)
        var adBreaks: (@Sendable () async throws -> [AdBreak])?
        if finished { adBreaks = { try await client.adBreaks(recordingID: id) } }
        open(adBreaks: adBreaks) { try await Self.prepare(client, url: url, title: title, mode: .recording) }
    }

    private static func prepare(_ client: DVBClient, url: URL, title: String, mode: PlaybackSource.Mode) async throws -> PlaybackSource {
        try await client.preparePlaylist(url)
        return PlaybackSource(url: url, title: title, mode: mode, headers: client.config.authorizationHeaders)
    }

    private func open(adBreaks: (@Sendable () async throws -> [AdBreak])? = nil,
                      _ resolve: @escaping @MainActor () async throws -> PlaybackSource) {
        guard !isResolving else {
            log.info("play ignored: still resolving the previous selection")
            return
        }
        if let stale = session, !isPlayerOnScreen {
            log.error("dropping a player session that never appeared")
            stale.controller.stop()
            session = nil
        }
        isResolving = true
        Task {
            defer { isResolving = false }
            do {
                let source = try await resolve()
                log.info("resolved \(source.title, privacy: .public) mode=\(String(describing: source.mode), privacy: .public) host=\(source.url.host() ?? "", privacy: .public)")
                let controller = PlayerController(source: source, resolve: resolve, adBreaks: adBreaks)
                controller.onFatalError = { [weak self] message in self?.closePlayer(showing: message) }
                let session = PlayerSession(controller: controller)
                self.session = session
                watchPresentation(of: session)
            } catch {
                log.error("resolve failed: \(String(describing: error), privacy: .public)")
                if let error = error as? CTError, error.code == CTError.unsupportedDRM {
                    drmError = error
                } else {
                    alertMessage = Self.message(for: error)
                }
            }
        }
    }

    /// Closes the player and shows `message`; directly if the player never made it on screen,
    /// otherwise after the dismissal (an alert cannot appear while the player is dismissing).
    func closePlayer(showing message: String) {
        if isPlayerOnScreen {
            messageAfterPlayer = message
        } else {
            alertMessage = message
        }
        session?.controller.stop()
        session = nil
    }

    /// If the full-screen player fails to present (SwiftUI drops presentations that collide
    /// with another one), say so instead of silently doing nothing.
    private func watchPresentation(of session: PlayerSession) {
        Task {
            try? await Task.sleep(for: .seconds(4))
            guard self.session?.id == session.id, !isPlayerOnScreen else { return }
            log.error("player did not appear")
            closePlayer(showing: "Přehrávač se nepodařilo otevřít. Zkuste to prosím znovu.")
        }
    }

    static func message(for error: Error) -> String {
        (error as? CTError)?.userMessage ?? "Něco se nepovedlo. Zkuste to prosím znovu."
    }
}

#if DEBUG
extension AppModel {
    /// Launch-argument hook for simulator checks, e.g. `-play live:CH_1`, `-play startover:CH_24`,
    /// `-play archive:CH_1` (latest ended programme that is in the archive), `-play unavailable:CH_1`
    /// (latest programme the decision rules expect to be unavailable), `-play buffer:CH_1` (latest
    /// ended programme that still plays from the "Zpět v čase" buffer).
    func debugPlay(_ argument: String) async {
        let parts = argument.split(separator: ":").map(String.init)
        if parts.first?.hasPrefix("dvb") == true {
            await debugPlayAntenna(parts)
            return
        }
        guard parts.count == 2, let channel = Channels.byID(parts[1]) else { return }
        let now = Date()
        switch parts[0] {
        case "live":
            play(.live(channel))
        case "startover":
            if let programme = await schedules.onAir(at: now)[channel.slug] {
                play(.programme(programme, channel: channel))
            }
        case "archive", "unavailable", "buffer":
            let today = BroadcastDay(containing: now)
            var candidates: [Programme] = []
            for day in [today, today.adding(days: -1)] {
                candidates += (try? await schedules.schedule(for: day))?[channel.slug] ?? []
            }
            let matching = candidates.filter { programme in
                switch (parts[0], DecisionRules.action(for: programme, now: now)) {
                case ("archive", .vod), ("unavailable", .vodUnavailable): return true
                case ("buffer", .startOver): return programme.end <= now
                default: return false
                }
            }
            if let programme = matching.max(by: { $0.start < $1.start }) {
                play(.programme(programme, channel: channel))
            }
        default:
            break
        }
    }

    /// `-play dvblive:1`, `-play dvbrec:1` (latest completed recording), `-play dvbstartover:1`
    /// (airing programme that is being recorded); the number is the gateway channel number.
    private func debugPlayAntenna(_ parts: [String]) async {
        guard parts.count == 2, let number = Int(parts[1]), let antenna,
              let channel = try? await antenna.channels().first(where: { $0.number == number }) else { return }
        let now = Date()
        let events = (try? await antenna.epg(for: BroadcastDay(containing: now)))?[channel.id] ?? []
        switch parts[0] {
        case "dvblive":
            playAntennaLive(channel)
        case "dvbrec":
            let recorded = events.compactMap { event -> (DVBEvent, String)? in
                if case .recording(let id) = AntennaAction.decide(event, now: now) { return (event, id) }
                return nil
            }
            if let (event, id) = recorded.last { playAntennaRecording(id: id, title: event.title, finished: true) }
        case "dvbstartover":
            for event in events {
                if case .airing(let id?) = AntennaAction.decide(event, now: now) {
                    playAntennaRecording(id: id, title: event.title, finished: false)
                }
            }
        default:
            break
        }
    }
}
#endif
