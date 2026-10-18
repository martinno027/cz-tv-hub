import Foundation

/// In-memory cache of daily schedules (programme metadata only, no stream URLs).
/// Today's schedule is refetched after `todayMaxAge`; other days are kept for the app's lifetime.
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
            let isToday = day == BroadcastDay(containing: now)
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
        let today = BroadcastDay(containing: now)
        var result: [String: Programme] = [:]
        for day in [today, today.adding(days: -1)] {
            guard let schedule = try? await schedule(for: day) else { continue }
            for channel in channels where result[channel.slug] == nil {
                result[channel.slug] = schedule[channel.slug]?.first { $0.isAiring(at: now) }
            }
            if result.count == channels.count { break }
        }
        return result
    }
}
