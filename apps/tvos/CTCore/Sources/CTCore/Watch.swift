import Foundation

// Watch reports (spec/dvb-gateway.md "Watch log (T53)", spec/apps.md "Watch reports (T53)"): while the gateway's
// `/dvb/status` says `"watchLog": true`, the player tells the household gateway what plays — `start`, a `beat` every
// 60 s while the player is open (paused included), `stop`. Fire-and-forget: playback never waits for a report, errors
// are ignored, nothing is shown, and the reports go only to the gateway (never to ČT, the Chamber or TMDb).

/// What one player session plays, in the gateway's shape.
public struct WatchItem: Sendable, Equatable {
    public enum Source: String, Sendable { case ct, psp, dvb }
    /// One meaning on every platform: `live` the live edge, `startover` anything from ČT's 3-hour buffer (a programme
    /// that began < 3 h ago: Od začátku, Zpět v čase, a resume within the buffer), `archive` iVysílání VOD,
    /// `recording` / `timeline` the antenna's recording or timeline window.
    public enum Mode: String, Sendable { case live, startover, archive, recording, timeline }

    public let source: Source
    public let mode: Mode
    /// ČT `CH_*`, the Chamber's stream id, the gateway's DVB channel id; empty when the app does not know it
    /// (the gateway stores `unknown`).
    public let channel: String
    /// The programme as the app shows it, cut to `WatchRules.titleLimit`; nil when none.
    public var title: String?
    /// ČT archive only.
    public let idec: String?
    /// Anténa recording / timeline only.
    public let recording: String?

    public init(source: Source, mode: Mode, channel: String, title: String? = nil, idec: String? = nil,
                recording: String? = nil) {
        self.source = source
        self.mode = mode
        self.channel = channel
        self.title = WatchRules.clean(title)
        self.idec = idec.flatMap { $0.isEmpty ? nil : $0 }
        self.recording = recording.flatMap { $0.isEmpty ? nil : $0 }
    }

    /// ČT as it resolved: live, the 3-hour buffer (`startover`, airing or ended) or the archive (with its idec).
    public static func ct(channelID: String?, playing mode: PlaybackSource.Mode, idec: String?, title: String?) -> WatchItem {
        let watchMode: Mode
        switch mode {
        case .live: watchMode = .live
        case .startOver: watchMode = .startover
        case .vod, .recording: watchMode = .archive
        }
        return WatchItem(source: .ct, mode: watchMode, channel: channelID ?? "", title: title,
                         idec: watchMode == .archive ? idec : nil)
    }

    /// A stream of the Chamber of Deputies (live only): its id and name.
    public static func psp(_ stream: PSPStream) -> WatchItem {
        WatchItem(source: .psp, mode: .live, channel: stream.id, title: stream.name)
    }

    public static func dvbLive(channelID: String, title: String?) -> WatchItem {
        WatchItem(source: .dvb, mode: .live, channel: channelID, title: title)
    }

    /// A finished or growing recording (`recording`) or a continuous-recording programme in its timeline.
    public static func dvb(_ mode: Mode, recordingID: String, channelID: String, title: String?) -> WatchItem {
        WatchItem(source: .dvb, mode: mode, channel: channelID, title: title, recording: recordingID)
    }
}

/// What a player hands the reporter: the item, and (live) a lookup of the programme airing now, made after the player
/// started, never before it.
public struct WatchStart {
    public let item: WatchItem
    public let title: (@MainActor () async -> String?)?

    public init(_ item: WatchItem, title: (@MainActor () async -> String?)? = nil) {
        self.item = item
        self.title = title
    }
}

public enum WatchRules {
    public static let path = "/dvb/watch"
    public static let beatInterval: TimeInterval = 60
    /// Per report; no retry.
    public static let timeout: TimeInterval = 5
    /// `watchLog` older than this is read again (with `/dvb/status`) before the next start or beat.
    public static let statusMaxAge: TimeInterval = 5 * 60
    public static let titleLimit = 200
    /// Reports waiting behind a slow gateway; more are dropped (a report is never worth a queue).
    static let maxPending = 8

    public enum Event: String, Sendable { case start, beat, stop }

    /// 32 random letters and digits (the gateway takes 16–64 of `[A-Za-z0-9_-]`).
    public static func newSessionID() -> String {
        UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
    }

    /// The gateway's rule for a session id.
    public static func isValidSession(_ id: String) -> Bool {
        (16...64).contains(id.count) && id.unicodeScalars.allSatisfy {
            CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-").contains($0)
        }
    }

    /// `start` carries every field, `beat` and `stop` only `event` and `session`.
    public static func body(_ event: Event, session: String, item: WatchItem?) -> Data {
        var json: [String: Any] = ["event": event.rawValue, "session": session]
        if event == .start, let item {
            json["source"] = item.source.rawValue
            json["mode"] = item.mode.rawValue
            json["channel"] = item.channel
            if let title = item.title { json["title"] = title }
            if let idec = item.idec { json["idec"] = idec }
            if let recording = item.recording { json["recording"] = recording }
        }
        return (try? JSONSerialization.data(withJSONObject: json, options: [.sortedKeys])) ?? Data()
    }

    /// Control characters out, whitespace trimmed, at most 200 characters; empty = nil.
    static func clean(_ title: String?) -> String? {
        guard let title else { return nil }
        let kept = String(String.UnicodeScalarView(title.unicodeScalars.filter { !CharacterSet.controlCharacters.contains($0) }))
            .trimmingCharacters(in: .whitespaces)
        return kept.isEmpty ? nil : String(kept.prefix(titleLimit))
    }
}

/// Where reports go: the household gateway (its client, through the Tailscale bridge when in use) and the profile's
/// session token when signed in.
public struct WatchTarget: Sendable {
    public let client: DVBClient
    public let profileToken: String?

    public init(client: DVBClient, profileToken: String?) {
        self.client = client
        self.profileToken = profileToken
    }
}

/// When to send `start`/`beat`/`stop` for the one player that is open. Every method returns at once; the reports go
/// out in order in the background.
@MainActor
public final class WatchReporter {
    private let clock: @Sendable () -> Date
    private let target: @MainActor () -> WatchTarget?
    private let refreshStatus: @MainActor () async -> Void
    private let newSession: @Sendable () -> String
    private let sleep: @Sendable (TimeInterval) async -> Void

    /// Last known `watchLog` of the gateway and when it was read (or a read was started). A failed read changes
    /// nothing: the last answer stays (initially off; spec/dvb-gateway.md "Watch log (T53)").
    public private(set) var watchLog = false
    private var statusCheckedAt: Date?

    /// The open player's item; nil = no player.
    private var item: WatchItem?
    private var title: (@MainActor () async -> String?)?
    /// Bumped by every `start`; a player's `stop` only ends its own.
    private var handle = 0
    /// The session whose `start` went out, nil while nothing is reported.
    private(set) var session: String?
    private var lastReport: Date?
    private var suspended = false
    private var beginTask: Task<Void, Never>?
    private var beatTask: Task<Void, Never>?
    private var tail: Task<Void, Never>?
    private var pending = 0

    /// `target` = the gateway now (nil: none, nothing is sent); `refreshStatus` reads `/dvb/status` again, which ends
    /// in `setWatchLog`; `sleep` waits between beats (injected by tests).
    public init(clock: @escaping @Sendable () -> Date = Date.init,
                target: @escaping @MainActor () -> WatchTarget?,
                refreshStatus: @escaping @MainActor () async -> Void,
                newSession: @escaping @Sendable () -> String = WatchRules.newSessionID,
                sleep: @escaping @Sendable (TimeInterval) async -> Void = { try? await Task.sleep(for: .seconds($0)) }) {
        self.clock = clock
        self.target = target
        self.refreshStatus = refreshStatus
        self.newSession = newSession
        self.sleep = sleep
    }

    // MARK: status

    /// A `/dvb/status` answer (any of the app's status reads). Off: nothing more is sent (the gateway ends and drops
    /// its sessions itself); on with a player open: its `start`.
    public func setWatchLog(_ on: Bool) {
        watchLog = on
        statusCheckedAt = clock()
        if !on {
            session = nil
            lastReport = nil
        } else if item != nil, session == nil, !suspended {
            begin()
        }
    }

    /// Another gateway (or none): what it says is not known yet; a session of the old one is left to its timeout.
    public func gatewayChanged() {
        watchLog = false
        statusCheckedAt = nil
        session = nil
        lastReport = nil
        if item != nil, !suspended { begin() }
    }

    private var statusIsStale: Bool {
        guard let checked = statusCheckedAt else { return true }
        return clock().timeIntervalSince(checked) >= WatchRules.statusMaxAge
    }

    // MARK: player

    /// A player started playing `start.item` (also a zap: the old session's `stop` goes out first). Returns the handle
    /// for `stop`.
    @discardableResult
    public func start(_ start: WatchStart) -> Int {
        end()
        handle += 1
        item = start.item
        title = start.title
        suspended = false
        begin()
        startBeats()
        return handle
    }

    /// The player with `handle` closed; a stale handle (an older player) changes nothing.
    public func stop(_ handle: Int) {
        guard handle == self.handle, item != nil else { return }
        end()
        endBeats()
        item = nil
        title = nil
    }

    /// The app went to the background: `stop` (the player stays open behind it).
    public func background() {
        guard !suspended else { return }
        suspended = true
        end()
        endBeats()
    }

    /// Back in front with the player still open: a new session.
    public func foreground() {
        guard suspended else { return }
        suspended = false
        guard item != nil else { return }
        begin()
        startBeats()
    }

    /// The beat loop's step (tests call it with a fake clock): a `beat` 60 s after the last report; `watchLog` read
    /// again when it is older than 5 minutes (a switch turned on reaches an open player this way).
    public func tick() {
        guard item != nil, !suspended else { return }
        guard let session else {
            if statusIsStale { begin() }
            return
        }
        if statusIsStale {
            statusCheckedAt = clock()
            let refresh = refreshStatus
            Task { await refresh() }
        }
        guard let last = lastReport, clock().timeIntervalSince(last) >= WatchRules.beatInterval - 0.5 else { return }
        lastReport = clock()
        send(.beat, session: session, item: nil)
    }

    /// Seconds until the next beat is due (a full interval while nothing is reported).
    private var nextWait: TimeInterval {
        guard session != nil, let last = lastReport else { return WatchRules.beatInterval }
        return max(1, last.addingTimeInterval(WatchRules.beatInterval).timeIntervalSince(clock()))
    }

    // MARK: inside

    /// `start` once the title is known and the gateway says the log is on.
    private func begin() {
        guard beginTask == nil, session == nil else { return }
        let lookUp = title
        beginTask = Task { [weak self] in
            var found: String??
            if let lookUp { found = .some(await lookUp()) }
            guard !Task.isCancelled, let self else { return }
            if let found {
                self.item?.title = WatchRules.clean(found)
                self.title = nil
            }
            if self.statusIsStale {
                self.statusCheckedAt = self.clock()
                await self.refreshStatus()
                guard !Task.isCancelled else { return }
            }
            self.beginTask = nil
            guard self.watchLog, !self.suspended, self.session == nil, let item = self.item else { return }
            let session = self.newSession()
            self.session = session
            self.lastReport = self.clock()
            self.send(.start, session: session, item: item)
        }
    }

    /// `stop` of the session in progress, if one was started.
    private func end() {
        beginTask?.cancel()
        beginTask = nil
        if let session { send(.stop, session: session, item: nil) }
        session = nil
        lastReport = nil
    }

    private func startBeats() {
        endBeats()
        let sleep = sleep
        beatTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let wait = self?.nextWait else { return }
                await sleep(wait)
                guard !Task.isCancelled, let self else { return }
                self.tick()
            }
        }
    }

    private func endBeats() {
        beatTask?.cancel()
        beatTask = nil
    }

    private func send(_ event: WatchRules.Event, session: String, item: WatchItem?) {
        guard let target = target(), pending < WatchRules.maxPending else { return }
        let body = WatchRules.body(event, session: session, item: item)
        let previous = tail
        pending += 1
        tail = Task { [weak self] in
            await previous?.value
            try? await target.client.watch(body: body, profileToken: target.profileToken)
            self?.pending -= 1
        }
    }

    /// Tests: every report queued so far has been sent (or failed) and a pending start is decided.
    func settle() async {
        while let task = beginTask {
            await task.value
            if beginTask == task { break }
        }
        await tail?.value
    }

    /// Tests: reports queued and not answered yet.
    var pendingForTests: Int { pending }

    /// Tests: the beat loop runs (a player is open and in front).
    var isBeating: Bool { beatTask != nil }
}
