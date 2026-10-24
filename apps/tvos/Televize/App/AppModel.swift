import CTCore
import Foundation
import Observation
import os

let log = Logger(subsystem: "cz.cztvhub.tv", category: "app")

/// One playback. The controller lives exactly as long as the session.
struct PlayerSession: Identifiable {
    let id = UUID()
    let controller: PlayerController
    /// Live only: ▲/▼ and digits switch channels (spec/apps.md "Channel numbers and zapping").
    var zapper: ChannelZapper?
    /// A `b…` recording in its channel's timeline (spec/apps.md "Anténa timeline player"): own bar and keys.
    var timeline: TimelineController?
}

/// What playing a channel live needs: a fresh source (also after a failure) and the info bar title.
struct LiveStart {
    let resolve: @MainActor () async throws -> PlaybackSource
    let liveTitle: @MainActor () async -> PlayerTitle
    /// Poslanecká sněmovna only: a 404 when opening means the broadcast just ended; called before the player
    /// closes with `PSPStreams.offAirMessage`.
    var offAir: (@MainActor () -> Void)? = nil
}

@MainActor
@Observable
final class AppModel {
    let schedules: ScheduleStore
    let planner: PlaybackPlanner
    /// "Pokračovat" positions, on this device only.
    let resume: ResumeStore
    /// ČT images and Anténa programme stills for the guide detail, memory only.
    let images = GuideImageStore()
    /// Channels hidden / reordered in Nastavení → Kanály, on this device or in the signed-in profile.
    let channelPrefs: ChannelPrefsStore
    /// Nastavení → Profil: this TV's settings in a profile on the gateway (spec/apps.md "Profil").
    let profile: ProfileSyncController
    /// "Is it on now?" of the Chamber of Deputies' streams, checked only while Živě or the guide is on screen.
    let parliament = PSPLiveMonitor()

    var session: PlayerSession?
    var alertMessage: String?
    /// UNSUPPORTED_DRM from CT: shown as a full-screen notice with "Otevřít v iVysílání".
    var drmError: CTError?
    /// Shown once the player has been dismissed (an alert cannot appear during the dismissal).
    var messageAfterPlayer: String?
    private(set) var isResolving = false
    /// Set by the player screen; a session whose screen never appeared must not block later plays.
    var isPlayerOnScreen = false
    /// The channels shown on this TV in its order (numbers = position + 1), refreshed by `refreshVisibleChannels`.
    private(set) var visibleChannels: [MixedChannel] = []
    /// Digits typed on Živě and the guide: start that channel live.
    @ObservationIgnored private(set) var numberEntry: NumberEntry!

    /// "Anténa" (DVB-T2 gateway). Nil when not configured: the Anténa group is hidden.
    private(set) var gateway: GatewayConfig?
    private(set) var antenna: AntennaStore?

    init(client: CTClient = CTClient()) {
        schedules = ScheduleStore(client: client)
        planner = PlaybackPlanner(client: client)
        resume = ResumeStore()
        channelPrefs = ChannelPrefsStore()
        profile = ProfileSyncController(
            current: ProfileDocument(channelPrefs: channelPrefs.prefs, thumbnails: images.tvAllows),
            credentials: ProfileKeychain()
        )
        wireProfile()
        setGateway(GatewayStore.load(), persist: false)
        numberEntry = NumberEntry { [weak self] number in self?.playLive(number: number) }
    }

    // MARK: profile

    private func wireProfile() {
        let channelPrefs = channelPrefs
        let images = images
        profile.apply = { document in
            channelPrefs.replace(document.channelPrefs)
            images.applyTVSwitch(document.thumbnails)
        }
        profile.onMessage = { [weak self] message in self?.alertMessage = message.text }
        channelPrefs.onChange = { [weak self] prefs in
            guard let self else { return }
            profile.settingsChanged(channelPrefs: prefs, thumbnails: images.tvAllows)
        }
        images.onTVSwitchChange = { [weak self] value in
            guard let self else { return }
            profile.settingsChanged(channelPrefs: channelPrefs.prefs, thumbnails: value)
        }
        // A session dropped at start (lost Keychain item) brings the TV's own settings back.
        profile.apply(profile.state.active)
    }

    /// App start (and the app back in front): pending changes are sent, a newer profile version applied; a failed
    /// send is tried again every 60 s.
    func runProfileSync() async {
        await profile.refresh()
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(ProfileSyncController.retryInterval))
            await profile.retryPending()
        }
    }

    // MARK: channel numbers

    /// Re-reads the Anténa channel list (cached by the store) and applies Nastavení → Kanály.
    func refreshVisibleChannels() async {
        let antennaChannels = (try? await antenna?.channels()) ?? []
        visibleChannels = channelPrefs.channels(antenna: antennaChannels).visible
    }

    /// Number of a channel on this TV, nil when it is not shown.
    func number(of key: String) -> Int? {
        ChannelNumbers.number(of: key, in: visibleChannels.map(\.key))
    }

    private func playLive(number: Int) {
        guard let key = ChannelNumbers.resolve(visibleChannels.map(\.key), number: number),
              let channel = visibleChannels.first(where: { $0.key == key }) else {
            numberEntry.show(missing: number)
            return
        }
        playLive(channel)
    }

    /// Title of the programme airing now on `channel`, from the cached guide data.
    func airingTitle(_ channel: MixedChannel) async -> String? {
        switch channel {
        case .ct(let ct): return await schedules.onAir(at: Date())[ct.slug]?.title
        case .antenna(let dvb): return await antenna?.onAir(at: Date())[dvb.id]?.title
        case .parliament(let stream): return Self.parliamentText(parliament.state(of: stream.key))
        }
    }

    /// "Přímý přenos" / "Nevysílá se" / "Zjišťuji…".
    static func parliamentText(_ state: PSPLiveState) -> String {
        switch state {
        case .live: return PSPStreams.liveText
        case .off: return PSPStreams.offText
        case .unknown: return PSPStreams.unknownText
        }
    }

    /// How to play `channel` live; nil for an Anténa channel without a gateway.
    func liveStart(_ channel: MixedChannel) -> LiveStart? {
        switch channel {
        case .ct(let ct):
            let planner = planner
            let schedules = schedules
            return LiveStart(resolve: { try await planner.resolve(.live(ct), resumeAt: nil) }, liveTitle: {
                let airing = await schedules.onAir(at: Date())[ct.slug]
                return .live(channel: ct.name, airing: airing.map { ($0.title, $0.end) })
            })
        case .antenna(let dvb):
            guard let antenna else { return nil }
            let client = antenna.client
            let url = client.liveURL(channelID: dvb.id)
            return LiveStart(resolve: { try await Self.prepare(client, url: url, title: dvb.name, mode: .live) }, liveTitle: {
                let airing = await antenna.onAir(at: Date())[dvb.id]
                return .live(channel: dvb.name, airing: airing.map { ($0.title, $0.end) })
            })
        case .parliament(let stream):
            // The published URL as it is: no extra or faked headers, live only (no resume, start-over, thumbnails).
            // Right before playback: a check younger than 60 s, else one more check of the same playlist, so an
            // off stream gets the message instead of a player error.
            let parliament = parliament
            let source = PlaybackSource(url: stream.url, title: stream.name, mode: .live)
            return LiveStart(resolve: {
                if await parliament.stateBeforePlaying(stream) == .off {
                    throw CTError(PSPStreams.offAirMessage, code: PSPStreams.offAirCode)
                }
                return source
            }, liveTitle: {
                PlayerTitle(title: stream.name, subtitle: PSPStreams.sourceLabel)
            }, offAir: { parliament.markOff(stream.key) })
        }
    }


    /// Live playback of any channel; the player can zap from it.
    func playLive(_ channel: MixedChannel) {
        guard let start = liveStart(channel) else { return }
        log.info("play live \(channel.key, privacy: .public)")
        open(liveTitle: start.liveTitle, offAir: start.offAir, zapFrom: channel, start.resolve)
    }

    /// Applies (and by default stores in the Keychain) a gateway configuration; nil removes it.
    func setGateway(_ config: GatewayConfig?, persist: Bool = true) {
        if persist {
            if let config { GatewayStore.save(config) } else { GatewayStore.delete() }
        }
        // Odebrat bránu while signed in signs out locally first (spec/apps.md "Profil").
        profile.setAPI(config.map { DVBClient(config: $0, transport: URLSessionTransport.memoryOnly()) })
        gateway = config
        antenna = config.map { AntennaStore(client: DVBClient(config: $0)) }
        images.setGateway(config)
    }

    /// Resolves the stream on the device right now, then opens the player. Every outcome is
    /// visible: the player, or a Czech alert. `resumeAt` ("Pokračovat") is seconds from the
    /// programme's start; programmes save their progress, live never does.
    func play(_ request: PlaybackRequest, resumeAt: TimeInterval? = nil) {
        log.info("play \(String(describing: request), privacy: .public) resume=\(resumeAt ?? -1)")
        let planner = planner
        var target: ResumeTarget?
        var title: PlayerTitle?
        switch request {
        case .programme(let programme, let channel):
            target = .ct(programme)
            title = .programme(programme.title, channel: channel.name, episode: programme.episodeTitle)
        case .live(let channel):
            playLive(.ct(channel))
            return
        }
        open(resumeTarget: target, title: title) {
            try await planner.resolve(request, resumeAt: resumeAt)
        }
    }

    /// Offered "Pokračovat" position of a CT programme, if any.
    func resumeOffer(for programme: Programme) -> TimeInterval? {
        let target = ResumeTarget.ct(programme)
        return resume.offer(for: target.key, length: target.length)
    }

    /// Offered "Pokračovat" position of an Anténa recording, if any.
    func resumeOffer(recordingID: String, event: DVBEvent) -> TimeInterval? {
        let target = ResumeTarget.antenna(recordingID: recordingID, event: event)
        return resume.offer(for: target.key, length: target.length)
    }

    /// Live picture of an Anténa channel.
    func playAntennaLive(_ channel: DVBChannel) {
        playLive(.antenna(channel))
    }

    /// A recording from the gateway; while it is still recording (`finished: false`) this is the
    /// start-over. Only finished recordings offer "Přeskočit reklamu" (spec/ad-breaks.md, v1).
    /// `resumeAt` ("Pokračovat") is seconds into the recording.
    func playAntennaRecording(id: String, event: DVBEvent, finished: Bool, resumeAt: TimeInterval? = nil,
                              channelName: String? = nil) {
        guard let client = antenna?.client else { return }
        if TimelineIDs.isContinuous(id) {
            playAntennaTimeline(id: id, event: event, resumeAt: resumeAt, channelName: channelName, client: client)
            return
        }
        let url = client.recordingURL(id: id)
        let title = event.title
        var adBreaks: (@Sendable () async throws -> [AdBreak])?
        if finished { adBreaks = { try await client.adBreaks(recordingID: id) } }
        open(adBreaks: adBreaks, resumeTarget: .antenna(recordingID: id, event: event),
             title: .programme(event.title, channel: channelName, episode: event.subtitle)) {
            try await client.preparePlaylist(url)
            return .antennaRecording(url: url, title: title, headers: client.config.authorizationHeaders, resumeAt: resumeAt)
        }
    }

    /// A continuous-recording programme (`b…`, airing or ended) in its channel's timeline: "Od začátku" at the
    /// programme start − 30 s, "Pokračovat" at start + progress, through the window's map.
    private func playAntennaTimeline(id: String, event: DVBEvent, resumeAt: TimeInterval?, channelName: String?,
                                     client: DVBClient) {
        let timeline = TimelineController(
            client: client, programmeID: id,
            startTime: TimelineRules.startTime(programmeStart: event.start, resumeAt: resumeAt),
            channelName: channelName ?? event.title
        )
        open(progressAt: { [weak timeline] position in timeline?.progress(at: position) },
             title: .programme(event.title, channel: channelName, episode: event.subtitle), timeline: timeline) {
            try await timeline.resolve()
        }
    }

    private static func prepare(_ client: DVBClient, url: URL, title: String, mode: PlaybackSource.Mode) async throws -> PlaybackSource {
        try await client.preparePlaylist(url)
        return PlaybackSource(url: url, title: title, mode: mode, headers: client.config.authorizationHeaders)
    }

    private func open(adBreaks: (@Sendable () async throws -> [AdBreak])? = nil, resumeTarget: ResumeTarget? = nil,
                      progressAt: (@MainActor (Double) -> (target: ResumeTarget, progress: TimeInterval)?)? = nil,
                      title: PlayerTitle? = nil, liveTitle: (@MainActor () async -> PlayerTitle)? = nil,
                      timeline: TimelineController? = nil, offAir: (@MainActor () -> Void)? = nil,
                      zapFrom channel: MixedChannel? = nil, _ resolve: @escaping @MainActor () async throws -> PlaybackSource) {
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
                let controller = PlayerController(source: source, resolve: resolve, adBreaks: adBreaks,
                                                  progress: resumeTarget.map { ($0, resume) },
                                                  progressAt: progressAt.map { (resume, $0) },
                                                  title: title, liveTitle: liveTitle, offAir: offAir)
                controller.onFatalError = { [weak self] message in self?.closePlayer(showing: message) }
                var session = PlayerSession(controller: controller)
                if let timeline {
                    timeline.attach(controller)
                    session.timeline = timeline
                }
                if let channel, source.mode == .live {
                    // The list as it is now (kept current by the main screens); a settings change applies to
                    // the next player. Only a first start without it waits for the list in the background.
                    let zapper = ChannelZapper(model: self, controller: controller, playing: channel,
                                               channels: visibleChannels)
                    session.zapper = zapper
                    if visibleChannels.isEmpty {
                        Task {
                            await refreshVisibleChannels()
                            zapper.setChannels(visibleChannels)
                        }
                    }
                }
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
            if let (event, id) = recorded.last { playAntennaRecording(id: id, event: event, finished: true, channelName: channel.name) }
        case "dvbstartover":
            for event in events {
                if case .airing(let id?) = AntennaAction.decide(event, now: now) {
                    playAntennaRecording(id: id, event: event, finished: false, channelName: channel.name)
                }
            }
        default:
            break
        }
    }
}
#endif
