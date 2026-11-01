import Foundation

/// A failed ČT day is asked again by itself (spec/apps.md, T42): while Živě or the guide is visible, every
/// `fastInterval` after a failure for the first `fastPhase`, then every `slowInterval`, until it loads. Outside the
/// ~10 requests of one screen open. The screen keeps one value per failing day and only waits while it is visible
/// (not behind the player, not in the background); `ScheduleStore` caches no failure, so each ask reaches ČT.
public struct ScheduleRetry: Sendable, Equatable {
    public static let fastInterval: TimeInterval = 30
    /// How long after the first failure the fast interval applies.
    public static let fastPhase: TimeInterval = 5 * 60
    public static let slowInterval: TimeInterval = 2 * 60
    /// Živě's normal refresh of what is on air while nothing failed (the schedules are cached; this re-reads them).
    public static let liveRefresh: TimeInterval = 60

    /// The first failure of the current run of failures; nil = loaded (or never asked).
    public private(set) var failedSince: Date?
    /// The last load (failed or not).
    public private(set) var lastAttempt: Date?

    public init() {}

    public var failing: Bool { failedSince != nil }

    /// Records a load that just finished at `now`. A success ends the run: a later failure starts at 30 s again.
    public mutating func record(failed: Bool, at now: Date) {
        lastAttempt = now
        if !failed {
            failedSince = nil
        } else if failedSince == nil {
            failedSince = now
        }
    }

    /// The interval after the last failed load: 30 s while it came within 5 minutes of the first failure, then 2 min.
    public var interval: TimeInterval? {
        guard let failedSince, let lastAttempt else { return nil }
        return lastAttempt.timeIntervalSince(failedSince) < Self.fastPhase ? Self.fastInterval : Self.slowInterval
    }

    /// How long to wait at `now` before asking again; nil = loaded, nothing to ask. 0 when the time has passed
    /// (the screen was hidden meanwhile: back on screen it asks at once).
    public func delay(now: Date) -> TimeInterval? {
        guard let interval, let lastAttempt else { return nil }
        return max(0, lastAttempt.addingTimeInterval(interval).timeIntervalSince(now))
    }

    /// Živě's wait after a read at `now`: the retry delay while ČT failed, else its normal minute.
    public func liveWait(now: Date) -> TimeInterval {
        delay(now: now) ?? Self.liveRefresh
    }
}
