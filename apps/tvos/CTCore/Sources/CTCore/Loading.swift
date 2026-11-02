import Foundation

/// The loader of one part of the archive that waits for the gateway (T52, spec/apps.md "Loading is always visible"):
/// the grid, the show page, its episode list, a next page. The part keeps its old content, dimmed, while the loader
/// shows; the loader shows only once the part has waited `delay` (a fast answer shows none, no flash). A newer choice
/// replaces the waited one without restarting the wait (its loader stays): only the newest request's answer counts.
public struct LoadingState: Equatable, Sendable {
    /// How long a part waits before its loader shows.
    public static let delay: Duration = .milliseconds(150)
    /// The old content under the loader.
    public static let dimmedOpacity = 0.35
    /// The ring on the TV; the next page's ring is half of it.
    public static let ringSize = 64.0
    public static let smallRingSize = ringSize / 2

    /// The newest request handed out; an answer for an older one is dropped.
    private var latest = 0
    /// Since when the part waits; nil = not waiting.
    public private(set) var since: ContinuousClock.Instant?

    public init() {}

    public var isWaiting: Bool { since != nil }

    /// A request starts: its ticket. Waiting already (a newer choice), the wait goes on from where it was.
    public mutating func begin(at now: ContinuousClock.Instant = .now) -> Int {
        latest += 1
        if since == nil { since = now }
        return latest
    }

    /// The answer (or the error) for `ticket` is there: true = it is the newest, show it (the loader goes);
    /// false = a newer choice replaced it, drop it (the loader stays for the newer one).
    @discardableResult
    public mutating func finish(_ ticket: Int) -> Bool {
        guard ticket == latest, since != nil else { return false }
        since = nil
        return true
    }

    /// Nothing waited for any more (the part was cleared); answers still on their way are dropped.
    public mutating func cancel() {
        latest += 1
        since = nil
    }

    /// Whether the loader (and the dimming) shows at `now`.
    public func isShown(at now: ContinuousClock.Instant = .now) -> Bool {
        guard let since else { return false }
        return now - since >= Self.delay
    }

    /// How long until the loader shows (zero = it shows); nil = not waiting.
    public func untilShown(at now: ContinuousClock.Instant = .now) -> Duration? {
        guard let since else { return nil }
        return max(.zero, Self.delay - (now - since))
    }
}

/// A choice (a season, year or month chip) whose list loads while the list shown stays (T52). A newer choice
/// replaces the waited one; when the newest fails, the chips go back to the choice **whose list is shown** — the
/// one before the first choice still waited for, never a replaced choice that never loaded.
public struct ChoiceLoad<Shown> {
    public private(set) var loading = LoadingState()
    /// What a failing choice puts back; set when the first of the waited choices began.
    public private(set) var fallback: Shown?

    public init() {}

    /// A choice starts loading over `shown` (what is listed now); its ticket.
    public mutating func begin(over shown: @autoclosure () -> Shown, at now: ContinuousClock.Instant = .now) -> Int {
        if !loading.isWaiting { fallback = shown() }
        return loading.begin(at: now)
    }

    /// The answer for `ticket` came: true = show it (the newest choice), false = drop it.
    public mutating func succeed(_ ticket: Int) -> Bool {
        guard loading.finish(ticket) else { return false }
        fallback = nil
        return true
    }

    /// The load for `ticket` failed: what to put back, or nil when a newer choice replaced it (nothing changes).
    public mutating func fail(_ ticket: Int) -> Shown? {
        guard loading.finish(ticket) else { return nil }
        defer { fallback = nil }
        return fallback
    }

    /// Nothing waited for any more.
    public mutating func cancel() {
        loading.cancel()
        fallback = nil
    }
}
