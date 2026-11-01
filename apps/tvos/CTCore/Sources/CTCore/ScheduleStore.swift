import Foundation

/// In-memory cache of daily schedules (programme metadata only, no stream URLs).
/// Today's schedule (and before 05:00 the previous day's, still on screen) is refetched after `todayMaxAge`;
/// other days are kept for the app's lifetime.
public actor ScheduleStore {
    public typealias Schedule = [String: [Programme]]

    private let client: CTClient
    private let channels: [Channel]
    private let todayMaxAge: TimeInterval
    private let clock: @Sendable () -> Date
    private var cache: [BroadcastDay: (fetchedAt: Date, schedule: Schedule)] = [:]
    private var inFlight: [BroadcastDay: Task<Schedule, Error>] = [:]

    public init(
        client: CTClient,
        channels: [Channel] = Channels.all,
        todayMaxAge: TimeInterval = 3600,
        clock: @escaping @Sendable () -> Date = Date.init
    ) {
        self.client = client
        self.channels = channels
        self.todayMaxAge = todayMaxAge
        self.clock = clock
    }

    /// Programmes of `day` keyed by channel slug. A failure is not cached, so a later call retries.
    public func schedule(for day: BroadcastDay) async throws -> Schedule {
        let now = clock()
        if let hit = cache[day] {
            // after midnight the guide still shows the previous broadcast day (GuideDays.openDay)
            let isToday = day == BroadcastDay(containing: now) || day == GuideDays.openDay(now: now)
            if !isToday || now.timeIntervalSince(hit.fetchedAt) < todayMaxAge {
                return hit.schedule
            }
        }
        if let task = inFlight[day] {
            return try await task.value
        }
        let client = client
        let slugs = channels.map(\.slug)
        let task = Task { try await client.schedules(slugs: slugs, day: day) }
        inFlight[day] = task
        defer { inFlight[day] = nil }
        let schedule = try await task.value
        cache[day] = (now, schedule)
        return schedule
    }

    /// The programme on air on each channel (keyed by slug). Shortly after midnight the
    /// current programme may still belong to yesterday's schedule, so that is checked too.
    public func onAir(at now: Date) async -> [String: Programme] {
        await onAirNow(at: now).programmes
    }

    /// What `onAir` returns, plus which channels have no programme because a schedule that could hold it failed.
    public struct OnAir: Sendable, Equatable {
        public var programmes: [String: Programme] = [:]
        /// Slugs without a programme whose day's schedule failed: Živě shows "Program se nepodařilo načíst"
        /// on their cards instead of a programme (spec/apps.md, T36).
        public var failed: Set<String> = []

        public init(programmes: [String: Programme] = [:], failed: Set<String> = []) {
            self.programmes = programmes
            self.failed = failed
        }
    }

    /// Today's calendar day counts; yesterday's only before 05:00, while the guide still shows it
    /// (`GuideDays.openDay`). Later a failed yesterday cannot hide anything airing now.
    public func onAirNow(at now: Date) async -> OnAir {
        let today = BroadcastDay(containing: now)
        let shown = GuideDays.openDay(now: now)
        var result = OnAir()
        var failedDay = false
        for day in [today, today.adding(days: -1)] {
            guard let schedule = try? await schedule(for: day) else {
                if day == today || day == shown { failedDay = true }
                continue
            }
            for channel in channels where result.programmes[channel.slug] == nil {
                result.programmes[channel.slug] = schedule[channel.slug]?.first { $0.isAiring(at: now) }
            }
            if result.programmes.count == channels.count { break }
        }
        if failedDay {
            result.failed = Set(channels.map(\.slug).filter { result.programmes[$0] == nil })
        }
        return result
    }
}
