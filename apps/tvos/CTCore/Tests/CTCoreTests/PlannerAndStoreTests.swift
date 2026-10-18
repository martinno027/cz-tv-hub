import Foundation
import XCTest
@testable import CTCore

final class PlaybackPlannerTests: XCTestCase {
    let now = utc("2026-10-17T18:00:00Z")
    let ct1 = Channels.byID("CH_1")!
    var transport: FakeTransport!
    var planner: PlaybackPlanner!

    override func setUp() async throws {
        transport = FakeTransport()
        try transport.on("/channel/", fixture: "live_CH_1")
        let now = now
        planner = PlaybackPlanner(client: CTClient(transport: transport), clock: { now })
    }

    private func programme(_ start: String, _ end: String, playable: Bool, idec: String = "226411010101016") -> Programme {
        Programme(channel: "ct1", idec: idec, title: "Pořad", start: utc(start), end: utc(end), isPlayableNow: playable)
    }

    func testLive() async throws {
        let source = try await planner.resolve(.live(ct1))
        XCTAssertEqual(source.mode, .live)
        XCTAssertEqual(source.title, "ČT1")
        XCTAssertTrue(source.url.absoluteString.contains("contentType=live"))
    }

    func testAiringStartsOverWithOffsetAndDate() async throws {
        let p = programme("2026-10-17T17:30:00Z", "2026-10-17T19:00:00Z", playable: true)
        let source = try await planner.resolve(.programme(p, channel: ct1))
        XCTAssertTrue(source.url.absoluteString.contains("contentType=timeshift"))
        XCTAssertEqual(source.mode, .startOver(seekDate: utc("2026-10-17T17:29:30Z"), offset: 8970))
        XCTAssertFalse(transport.requests.contains { $0.url!.path.contains("/media/") })
    }

    func testArchived() async throws {
        try transport.on("/media/external/", fixture: "vod_ok")
        let p = programme("2026-10-17T08:00:00Z", "2026-10-17T09:00:00Z", playable: true)
        let source = try await planner.resolve(.programme(p, channel: ct1))
        XCTAssertEqual(source.mode, .vod)
        XCTAssertTrue(source.url.absoluteString.contains("contentType=vod"))
    }

    func testRecentArchivedProgrammePlaysFromBufferWithoutAskingVod() async throws {
        try transport.on("/media/external/", fixture: "vod_ok")
        let p = programme("2026-10-17T16:00:00Z", "2026-10-17T17:00:00Z", playable: true)
        let source = try await planner.resolve(.programme(p, channel: ct1))
        XCTAssertEqual(source.mode, .startOver(seekDate: utc("2026-10-17T15:59:30Z"), offset: 3570))
        XCTAssertFalse(transport.requests.contains { $0.url!.path.contains("/media/") })
    }

    func testUnsupportedDrmIsNotRetriedThroughAnotherStream() async throws {
        let body = Data(#"{"message": {"status": {"name": "UNSUPPORTED_DRM"}, "labels": {"textShort": "Nelze přehrát"}}}"#.utf8)
        transport.on("/media/external/", HTTPResponse(status: 403, body: body))
        let p = programme("2026-10-17T10:00:00Z", "2026-10-17T11:00:00Z", playable: true)
        await assertThrows(try await planner.resolve(.programme(p, channel: ct1)),
                           "Nelze přehrát\nPořad můžete pustit v oficiální aplikaci iVysílání.")
        XCTAssertEqual(transport.requests.count, 1, "no live/timeshift fallback")
        XCTAssertFalse(transport.requests[0].url!.absoluteString.contains("canPlayDrm=true"))
    }

    func testStillLiveFallsBackToStartOverOnReportedChannel() async throws {
        try transport.on("/media/external/", fixture: "vod_still_live")
        let p = programme("2026-10-17T14:00:00Z", "2026-10-17T14:50:00Z", playable: true)
        let source = try await planner.resolve(.programme(p, channel: ct1))
        XCTAssertEqual(source.mode, .startOver(seekDate: utc("2026-10-17T13:59:30Z"), offset: 0))
        XCTAssertTrue(transport.requests.last!.url!.path.hasSuffix("/channel/CH_24"))
    }

    func testUnavailableShowsCTReason() async throws {
        try transport.on("/media/external/", fixture: "vod_no_licence")
        let p = programme("2026-10-17T09:00:00Z", "2026-10-17T10:00:00Z", playable: false)
        await assertThrows(try await planner.resolve(.programme(p, channel: ct1)), "ČT nemá práva pro\u{00A0}internet")
    }

    func testFutureIsNotPlayable() async {
        let p = programme("2026-10-17T20:00:00Z", "2026-10-17T21:00:00Z", playable: false)
        await assertThrows(try await planner.resolve(.programme(p, channel: ct1)), "Pořad ještě nezačal")
        XCTAssertTrue(transport.requests.isEmpty)
    }

    func testStartOverWithoutTimeshift() async {
        transport.on("/channel/", HTTPResponse(status: 200, body: Data(#"{"streamUrls": {"main": "https://x/live"}}"#.utf8)))
        let p = programme("2026-10-17T17:30:00Z", "2026-10-17T19:00:00Z", playable: true)
        await assertThrows(try await planner.resolve(.programme(p, channel: ct1)), "Zpět v čase není pro tento kanál k dispozici")
    }

    func testBlockedLive() async throws {
        try transport.on("/channel/", fixture: "live_blocked")
        await assertThrows(try await planner.resolve(.live(ct1)), "Vysílání je blokováno")
    }

    private func assertThrows(_ expression: @autoclosure () async throws -> PlaybackSource, _ message: String,
                              file: StaticString = #filePath, line: UInt = #line) async {
        do {
            let source = try await expression()
            XCTFail("expected error, got \(source)", file: file, line: line)
        } catch let error as CTError {
            XCTAssertEqual(error.userMessage, message, file: file, line: line)
        } catch {
            XCTFail("unexpected \(error)", file: file, line: line)
        }
    }
}

final class ScheduleStoreTests: XCTestCase {
    private func scheduleBody(_ programmes: [(slug: String, idec: String, start: String, end: String)]) -> HTTPResponse {
        let grouped = Dictionary(grouping: programmes, by: \.slug)
        let plans = grouped.map { slug, items in
            ["channel": slug, "program": items.map { ["idec": $0.idec, "title": $0.idec, "start": $0.start, "end": $0.end] }] as [String: Any]
        }
        let body = try! JSONSerialization.data(withJSONObject: ["data": ["TVProgramDailyChannelsPlanV2": plans]])
        return HTTPResponse(status: 200, body: body)
    }

    private func graphQLCount(_ transport: FakeTransport) -> Int {
        transport.requests.filter { $0.url!.path.contains("graphql") }.count
    }

    func testCachesDaysAndRefreshesTodayAfterAnHour() async throws {
        let transport = FakeTransport()
        try transport.on("graphql", fixture: "schedule_ct1_ct24")
        let clock = MutableClock(utc("2026-10-17T12:00:00Z"))
        let store = ScheduleStore(client: CTClient(transport: transport), clock: { clock.now })
        let today = BroadcastDay(year: 2026, month: 10, day: 17)
        let yesterday = today.adding(days: -1)

        _ = try await store.schedule(for: today)
        _ = try await store.schedule(for: today)
        _ = try await store.schedule(for: yesterday)
        XCTAssertEqual(graphQLCount(transport), 2)

        clock.now = utc("2026-10-17T13:00:01Z")
        _ = try await store.schedule(for: yesterday)
        XCTAssertEqual(graphQLCount(transport), 2, "past days stay cached")
        _ = try await store.schedule(for: today)
        XCTAssertEqual(graphQLCount(transport), 3, "today is refetched after an hour")
    }

    func testFailingDayDoesNotBreakOthersAndIsRetried() async throws {
        let transport = FakeTransport()
        try transport.on("graphql", fixture: "schedule_ct1_ct24")
        try transport.on("10.19.2026", fixture: "graphql_error")
        let store = ScheduleStore(client: CTClient(transport: transport), clock: { utc("2026-10-17T12:00:00Z") })

        do {
            _ = try await store.schedule(for: BroadcastDay(year: 2026, month: 10, day: 19))
            XCTFail("expected error")
        } catch let error as CTError {
            XCTAssertEqual(error.userMessage, "Program se nepodařilo načíst")
        }
        let ok = try await store.schedule(for: BroadcastDay(year: 2026, month: 10, day: 18))
        XCTAssertEqual(ok["ct1"]?.count, 6)
        _ = try? await store.schedule(for: BroadcastDay(year: 2026, month: 10, day: 19))
        XCTAssertEqual(graphQLCount(transport), 3, "failures are not cached")
    }

    func testOnAirFallsBackToYesterdaysSchedule() async throws {
        let transport = FakeTransport()
        // 00:30 Prague on the 17th; the night programme on ct2 is listed on the 16th.
        transport.on("10.17.2026", scheduleBody([("ct1", "today", "2026-10-16T22:00:00Z", "2026-10-16T23:00:00Z")]))
        transport.on("10.16.2026", scheduleBody([("ct2", "night", "2026-10-16T21:30:00Z", "2026-10-16T23:30:00Z"),
                                                  ("ct1", "old", "2026-10-16T20:00:00Z", "2026-10-16T22:00:00Z")]))
        let store = ScheduleStore(client: CTClient(transport: transport))
        let onAir = await store.onAir(at: utc("2026-10-16T22:30:00Z"))
        XCTAssertEqual(onAir["ct1"]?.idec, "today")
        XCTAssertEqual(onAir["ct2"]?.idec, "night")
        XCTAssertNil(onAir["ct24"])
    }
}
