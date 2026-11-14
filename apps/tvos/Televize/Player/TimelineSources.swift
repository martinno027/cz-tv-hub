import AVFoundation
import CTCore
import os

private let sourceLog = Logger(subsystem: "cz.cztvhub.tv", category: "timeline")

/// What the timeline player plays in: an Anténa window of the continuous recording (spec/apps.md "Anténa timeline
/// player", T05) or the one window of a ČT start-over / archive playback ("ČT player bar", T62). The source resolves,
/// says where the window is and what to remember; `TimelineController` does the bar, the keys and the seeks over it.
@MainActor
protocol TimelineWindowSource: AnyObject {
    /// The bar's second line and its title in a gap.
    var channelName: String { get }
    /// The window changed outside a player tick (a growing Anténa window re-fetched).
    var onChange: (() -> Void)? { get set }
    /// The stream; also the player's re-resolve after a failure.
    func resolve() async throws -> PlaybackSource
    /// The window with the player as it is now; nil until it is known.
    func window(item: AVPlayerItem?, now: Date) -> BarWindow?
    /// Opens the neighbouring window `id` at `time` (Anténa `previous` / `next`); afterwards the source is that window.
    func openNeighbour(_ id: String, at time: Date) async throws -> PlaybackSource
    /// "Pokračovat": the programme at the playhead and its progress.
    func progress(at position: Double, in window: BarWindow) -> (target: ResumeTarget, progress: TimeInterval)?
    func adBreaks(programmeID: String) async throws -> [AdBreak]
    /// The watch report of the window now playing; nil = the one the player was opened with.
    var watchStart: WatchStart? { get }
    /// Where a re-resolve after a failure resumes with the playhead at `position`; nil = the player's own (Anténa:
    /// the same window, its own position; a movement goes on).
    func resume(at position: Double, in window: BarWindow) -> PlayerController.Resume?
    func stop()
}

// MARK: - Anténa

/// A `b…` programme in its channel's recording (`GET /dvb/timeline/<id>`): "Od začátku" at programme start − 30 s,
/// "Pokračovat" at start + progress, the neighbours through `previous` / `next`, a growing window re-fetched every 60 s.
@MainActor
final class AntennaTimelineSource: TimelineWindowSource {
    let channelName: String
    var onChange: (() -> Void)?

    private let client: DVBClient
    /// What the Archiv row shows for the programme the player was opened for (its show's title and image).
    private let firstCard: ResumeCard?
    private let firstID: String
    /// Wall clock the next resolve starts at (the programme start − 30 s, "Pokračovat", or a window hand-off).
    private var startTime: Date
    private var timeline: DVBTimeline?
    private var barWindow: BarWindow?
    private var refreshTask: Task<Void, Never>?
    private var stopped = false

    init(client: DVBClient, programmeID: String, startTime: Date, channelName: String, firstCard: ResumeCard? = nil) {
        self.client = client
        self.firstCard = firstCard
        self.firstID = programmeID
        self.startTime = startTime
        self.channelName = channelName
    }

    /// Fetches the window's JSON and prepares its playlist; the source starts at `startTime` mapped into the window.
    /// Also the player's re-resolve after a failure (same window, it then resumes at its own position).
    func resolve() async throws -> PlaybackSource {
        let window = try await client.timeline(id: timeline?.id ?? firstID)
        let source = try await source(for: window)
        apply(window)
        return source
    }

    func openNeighbour(_ id: String, at time: Date) async throws -> PlaybackSource {
        let window = try await client.timeline(id: id)
        let previousStart = startTime
        startTime = time
        do {
            let next = try await source(for: window)
            refreshTask?.cancel()
            apply(window)
            return next
        } catch {
            startTime = previousStart
            throw error
        }
    }

    private func source(for window: DVBTimeline) async throws -> PlaybackSource {
        let url = client.timelinePlaylistURL(window)
        try await client.preparePlaylist(url)
        let start = window.map.position(at: startTime)
        sourceLog.info("window \(window.id, privacy: .public) pieces=\(window.map.pieces.count) duration=\(window.map.duration, format: .fixed(precision: 1)) complete=\(window.complete) start=\(start, format: .fixed(precision: 1))")
        return .antennaRecording(url: url, title: channelName, headers: client.config.authorizationHeaders, resumeAt: start)
    }

    private func apply(_ window: DVBTimeline) {
        timeline = window
        barWindow = BarWindow(window)
        if !window.complete, !stopped { startRefreshing() }
    }

    func window(item: AVPlayerItem?, now: Date) -> BarWindow? { barWindow }

    /// The window now playing (spec/dvb-gateway.md "Watch log (T53)"): its id and channel, the programme at the start.
    var watchStart: WatchStart? {
        guard let timeline else { return nil }
        let title = TimelineRules.programmeAt(startTime, in: timeline.programmes).map { timeline.programmes[$0].title }
        return WatchStart(.dvb(.timeline, recordingID: timeline.id, channelID: timeline.channel, title: title))
    }

    /// The programme at the playhead (with an id) and the seconds since its start.
    func progress(at position: Double, in window: BarWindow) -> (target: ResumeTarget, progress: TimeInterval)? {
        guard let time = window.map.time(at: position), let index = TimelineRules.programmeAt(time, in: window.programmes)
        else { return nil }
        let programme = window.programmes[index]
        guard let id = programme.id else { return nil }
        // the opened programme with what the archive showed, the neighbours with their own title and still
        let card = firstCard != nil && id == firstID ? firstCard : .programme(
            title: programme.title, episode: programme.subtitle,
            image: ArchiveImage.thumbnail(recordingID: id).stored, source: .dvb, start: programme.start,
            channel: channelName)
        let target = ResumeTarget(key: ResumeRules.dvbKey(recordingID: id), programmeStart: programme.start,
                                  length: programme.length, card: card)
        return (target, TimelineRules.resumeProgress(time: time, programmeStart: programme.start))
    }

    func adBreaks(programmeID: String) async throws -> [AdBreak] {
        try await client.adBreaks(recordingID: programmeID)
    }

    func resume(at position: Double, in window: BarWindow) -> PlayerController.Resume? { nil }

    func stop() {
        stopped = true
        refreshTask?.cancel()
    }

    private func startRefreshing() {
        refreshTask?.cancel()
        refreshTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(DVBTimeline.refreshInterval))
                guard let self, !Task.isCancelled, let id = self.timeline?.id else { return }
                do {
                    let window = try await self.client.timeline(id: id)
                    guard !Task.isCancelled, window.id == self.timeline?.id else { return }
                    self.timeline = window
                    self.barWindow = BarWindow(window)
                    self.onChange?()
                    if window.complete { return }
                } catch {
                    sourceLog.info("refresh failed: \(String(describing: error), privacy: .public)")
                }
            }
        }
    }
}

// MARK: - ČT

/// ČT start-over / "Zpět v čase" (the 3 h buffer sliding with now) or an archive item (`spec/apps.md` "ČT player
/// bar", T62). The stream is resolved as before (`PlaybackPlanner`, redirect only); this only maps the player's
/// positions to wall clock and says what "Pokračovat" remembers.
@MainActor
final class CTTimelineSource: TimelineWindowSource {
    enum Kind { case timeshift, vod }

    let channelName: String
    var onChange: (() -> Void)?

    private let kind: Kind
    private let programme: Programme
    private let card: ResumeCard?
    private let resolveStream: @MainActor () async throws -> PlaybackSource
    /// Timeshift: the channel's guide around the buffer (empty until loaded, or without a guide).
    private var programmes: [DVBTimeline.Programme]
    /// The guide's programmes by idec, for "Pokračovat" of the one at the playhead.
    private var byIdec: [String: Programme] = [:]
    private var guideTask: Task<Void, Never>?
    /// Wall clock of position 0 of `originItem` (`PROGRAM-DATE-TIME`, else estimated once from the live edge).
    private var origin: Date?
    private weak var originItem: AVPlayerItem?
    private var originFromDates = false
    private var loggedLag: TimeInterval?

    init(mode: PlaybackSource.Mode, programme: Programme, channel: Channel?, card: ResumeCard?,
         resolve: @escaping @MainActor () async throws -> PlaybackSource,
         guide: (@Sendable () async -> [Programme])?) {
        if case .startOver = mode { kind = .timeshift } else { kind = .vod }
        self.programme = programme
        self.card = card
        self.channelName = channel?.name ?? ""
        self.resolveStream = resolve
        programmes = CTBarRules.programmes(guide: [], opened: programme)
        byIdec = [programme.idec: programme]
        if kind == .timeshift, let guide {
            guideTask = Task { [weak self] in
                let list = await guide()
                guard let self, !Task.isCancelled, !list.isEmpty else { return }
                self.programmes = CTBarRules.programmes(guide: list, opened: programme)
                for item in list where !item.idec.isEmpty { self.byIdec[item.idec] = item }
                self.byIdec[programme.idec] = programme
                sourceLog.info("ct bar guide: \(list.count) programmes")
                self.onChange?()
            }
        }
    }

    func resolve() async throws -> PlaybackSource { try await resolveStream() }

    func openNeighbour(_ id: String, at time: Date) async throws -> PlaybackSource {
        throw CTError("Přehrávání se nezdařilo. Zkuste to prosím znovu.", detail: "ct bar has no neighbours")
    }

    func window(item: AVPlayerItem?, now: Date) -> BarWindow? {
        guard let item, item.status == .readyToPlay else { return nil }
        switch kind {
        case .timeshift:
            guard let origin = timeshiftOrigin(item, now: now) else { return nil }
            // The window's "now" is the stream's live edge (its newest segment is behind the real now).
            var edgeNow = now
            if let range = item.seekableTimeRanges.last?.timeRangeValue, CMTimeRangeGetEnd(range).seconds.isFinite {
                edgeNow = min(now, origin.addingTimeInterval(CMTimeRangeGetEnd(range).seconds))
            }
            let lag = now.timeIntervalSince(edgeNow)
            if loggedLag == nil || abs(lag - (loggedLag ?? 0)) > 15 {
                loggedLag = lag
                sourceLog.info("ct bar live edge \(lag, format: .fixed(precision: 1)) s behind now")
            }
            return .ctTimeshift(id: programme.idec, channel: channelName, origin: origin, now: edgeNow, lag: lag,
                                programmes: programmes)
        case .vod:
            let length = item.duration.seconds
            let duration = length.isFinite && length > 0 ? length : programme.end.timeIntervalSince(programme.start)
            return .ctVOD(id: programme.idec, channel: channelName, start: CTBarRules.knownStart(programme.start),
                          duration: duration, title: programme.title, subtitle: programme.episodeTitle)
        }
    }

    /// `currentDate − currentTime` where the stream has `PROGRAM-DATE-TIME` (kept unless it moves by more than 1 s:
    /// no jitter of the clock); else, once per item, `now − the live edge's position` (the estimate "Pokračovat"
    /// already used, `ResumeRules.progress`).
    private func timeshiftOrigin(_ item: AVPlayerItem, now: Date) -> Date? {
        if originItem !== item {
            originItem = item
            origin = nil
            originFromDates = false
        }
        let position = item.currentTime().seconds
        if let date = item.currentDate(), position.isFinite {
            let fromDates = date.addingTimeInterval(-position)
            if !originFromDates || origin.map({ abs($0.timeIntervalSince(fromDates)) > 1 }) ?? true {
                if !originFromDates { sourceLog.info("ct bar clock from PROGRAM-DATE-TIME") }
                origin = fromDates
                originFromDates = true
            }
            return origin
        }
        if origin == nil, let range = item.seekableTimeRanges.last?.timeRangeValue {
            let edge = CMTimeRangeGetEnd(range).seconds
            guard edge.isFinite else { return nil }
            origin = now.addingTimeInterval(-edge)
            sourceLog.info("ct bar clock estimated from the live edge \(edge, format: .fixed(precision: 1))")
        }
        return origin
    }

    /// The programme at the playhead with an idec; progress = its wall clock − start (timeshift), the position (VOD).
    func progress(at position: Double, in window: BarWindow) -> (target: ResumeTarget, progress: TimeInterval)? {
        switch kind {
        case .vod:
            return (ResumeTarget.ct(programme, card: card), max(0, position))
        case .timeshift:
            guard let time = window.map.time(at: position),
                  let (idec, progress) = CTBarRules.resumeProgramme(at: time, in: window.programmes,
                                                                     opened: (programme.idec, programme.start)),
                  let item = byIdec[idec] else { return nil }
            return (ResumeTarget.ct(item, card: idec == programme.idec ? card : nil), progress)
        }
    }

    func adBreaks(programmeID: String) async throws -> [AdBreak] { [] }

    var watchStart: WatchStart? { nil }

    /// A re-resolve resumes at the playhead: its wall clock (timeshift), its position (VOD).
    func resume(at position: Double, in window: BarWindow) -> PlayerController.Resume? {
        switch kind {
        case .timeshift: return window.map.time(at: position).map { .date($0) }
        case .vod: return position > 0 ? .seconds(position) : nil
        }
    }

    func stop() { guideTask?.cancel() }
}
