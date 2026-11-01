import XCTest
@testable import CTCore

/// spec/apps.md "A failed ČT day is asked again by itself (T42)": every 30 s after a failure for the first 5 minutes,
/// then every 2 minutes, until it loads. A fake clock (`MutableClock`) and a fake transport whose ČT comes back at a
/// chosen time; the loops below are the screens' loops (GuideView retry task, LiveView refresh) without the sleeping.
final class ScheduleRetryTests: XCTestCase {
    private let start = utc("2026-10-17T18:30:00Z")  // 20:30 Prague, during the outage
    private let today = BroadcastDay(year: 2026, month: 10, day: 17)

    private func scheduleBody() -> HTTPResponse {
        let plans = Channels.all.map { channel in
            ["channel": channel.slug,
             "program": [["idec": "\(channel.slug)-evening", "title": "Večer \(channel.slug)",
                          "start": "2026-10-17T18:00:00Z", "end": "2026-10-17T20:30:00Z"]]] as [String: Any]
        }
        let body = try! JSONSerialization.data(withJSONObject: ["data": ["TVProgramDailyChannelsPlanV2": plans]])
        return HTTPResponse(status: 200, body: body)
    }

    private final class Outage {
        let transport = FakeTransport()
        let clock: MutableClock
        let store: ScheduleStore
        /// ČT answers again from this many seconds after the start (nil = never).
        let endsAfter: TimeInterval?
        let start: Date
        let body: HTTPResponse

        init(start: Date, endsAfter: TimeInterval?, body: HTTPResponse) throws {
            let clock = MutableClock(start)
            self.clock = clock
            self.start = start
            self.endsAfter = endsAfter
            self.body = body
            store = ScheduleStore(client: CTClient(transport: transport), clock: { clock.now })
            try transport.on("graphql", fixture: "graphql_error")
        }

        /// The fake clock moves on by `seconds`; ČT comes back once the outage is over.
        func pass(_ seconds: TimeInterval) {
            clock.now = clock.now.addingTimeInterval(seconds)
            if let endsAfter, clock.now.timeIntervalSince(start) >= endsAfter { transport.on("graphql", body) }
        }

        var elapsed: TimeInterval { clock.now.timeIntervalSince(start) }
        var requests: Int { transport.requests.filter { $0.url!.path.contains("graphql") }.count }
    }

    private func makeOutage(endsAfter: TimeInterval?) throws -> Outage {
        try Outage(start: start, endsAfter: endsAfter, body: scheduleBody())
    }

    /// The guide's loop: load, then while it fails wait the delay and ask again. Returns when each ask happened.
    private func guideLoop(_ outage: Outage, schedule: inout ScheduleStore.Schedule, limit: Int = 100) async -> [TimeInterval] {
        var retry = ScheduleRetry()
        var asks: [TimeInterval] = []
        repeat {
            asks.append(outage.elapsed)
            let loaded = try? await outage.store.schedule(for: today)
            if let loaded { schedule = loaded }
            retry.record(failed: loaded == nil, at: outage.clock.now)
            guard let wait = retry.delay(now: outage.clock.now) else { break }
            outage.pass(wait)
        } while asks.count < limit
        return asks
    }

    func testGuideDayRecoversWithoutReopening() async throws {
        let outage = try makeOutage(endsAfter: 500)
        var schedule: ScheduleStore.Schedule = [:]
        let asks = await guideLoop(outage, schedule: &schedule)
        // every 30 s for the first 5 minutes (11 asks incl. the first load), then every 2 minutes; ČT back at 500 s
        XCTAssertEqual(asks, [0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 420, 540])
        XCTAssertEqual(outage.requests, asks.count, "each ask reaches ČT (no failure cached), nothing after it loaded")
        XCTAssertEqual(schedule["ct1"]?.first?.idec, "ct1-evening", "the ČT rows get their programmes")
        XCTAssertEqual(GuideFiller.text(source: .ct, ctScheduleFailed: false), GuideFiller.offAir)
        XCTAssertNotEqual(GuideFiller.text(source: .ct, ctScheduleFailed: true), GuideFiller.offAir)
    }

    func testAskingGoesOnUntilItLoadsEvenAfterAnHour() async throws {
        let outage = try makeOutage(endsAfter: 74 * 60 + 10)  // the owner's case: over an hour
        var schedule: ScheduleStore.Schedule = [:]
        let asks = await guideLoop(outage, schedule: &schedule)
        XCTAssertEqual(asks.last, 75 * 60, "the first 2-minute ask after ČT came back (5 min + 35 × 2 min)")
        XCTAssertEqual(asks.count, 11 + 35, "11 in the first 5 minutes, then one every 2 minutes up to 75 min")
        XCTAssertEqual(Set(zip(asks.dropFirst(11), asks.dropFirst(10)).map { $0 - $1 }), [120])
        XCTAssertNotNil(schedule["ct24"])
    }

    func testLiveCardsRecoverWithoutReopening() async throws {
        let outage = try makeOutage(endsAfter: 45)
        var retry = ScheduleRetry()
        var cards: [ScheduleStore.OnAir] = []
        var waits: [TimeInterval] = []
        // Živě's loop (LiveView): read what is on air, record, wait, again.
        for _ in 0..<4 {
            let onAir = await outage.store.onAirNow(at: outage.clock.now)
            cards.append(onAir)
            retry.record(failed: !onAir.failed.isEmpty, at: outage.clock.now)
            let wait = retry.liveWait(now: outage.clock.now)
            waits.append(wait)
            outage.pass(wait)
        }
        XCTAssertEqual(cards[0].failed, Set(Channels.all.map(\.slug)), "the outage: every ČT card says so")
        XCTAssertEqual(cards[1].failed.count, Channels.all.count)
        XCTAssertEqual(cards[2].failed, [], "ČT is back: no card says the failure any more")
        XCTAssertEqual(cards[2].programmes["ct24"]?.idec, "ct24-evening")
        XCTAssertEqual(cards[3], cards[2])
        XCTAssertEqual(waits, [30, 30, 60, 60], "30 s while failing, then the normal minute")
        XCTAssertEqual(outage.requests, 2 * 2 + 1, "two failed reads (today + yesterday each), then today only, cached")
    }

    func testHiddenScreenAsksAtOnceWhenBackAndASuccessStartsOver() {
        var retry = ScheduleRetry()
        XCTAssertNil(retry.delay(now: start), "nothing asked yet: nothing to retry")
        XCTAssertEqual(retry.liveWait(now: start), 60)
        retry.record(failed: true, at: start)
        XCTAssertTrue(retry.failing)
        XCTAssertEqual(retry.delay(now: start.addingTimeInterval(10)), 20)
        XCTAssertEqual(retry.delay(now: start.addingTimeInterval(600)), 0, "back from the player after 10 min: at once")

        retry.record(failed: true, at: start.addingTimeInterval(600))
        XCTAssertEqual(retry.interval, 120, "still failing after 5 minutes")
        retry.record(failed: false, at: start.addingTimeInterval(720))
        XCTAssertFalse(retry.failing)
        XCTAssertNil(retry.delay(now: start.addingTimeInterval(720)))
        retry.record(failed: true, at: start.addingTimeInterval(4000))
        XCTAssertEqual(retry.interval, 30, "a new failure after a success starts at 30 s again")
    }
}
