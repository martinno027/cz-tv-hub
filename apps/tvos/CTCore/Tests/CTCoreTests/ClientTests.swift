import Foundation
import XCTest
@testable import CTCore

final class ClientTests: XCTestCase {
    func testLiveRequest() async throws {
        let transport = FakeTransport()
        try transport.on("/channel/CH_1?", fixture: "live_CH_1")
        let streams = try await CTClient(transport: transport).liveStreams(channelID: "CH_1")
        XCTAssertNotNil(streams.timeshift)

        let request = try XCTUnwrap(transport.requests.first)
        XCTAssertEqual(request.httpMethod, "GET")
        XCTAssertEqual(request.url?.absoluteString,
                       "https://api.ceskatelevize.cz/video/v1/playlist-live/v1/stream-data/channel/CH_1"
                       + "?canPlayDrm=false&quality=web&streamType=hls&origin=ivysilaniweb&usePlayability=true&clientVersion=0.0.0")
        XCTAssertEqual(request.value(forHTTPHeaderField: "User-Agent"), "Mozilla/5.0 (compatible; cz-tv-hub/0.1)")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Accept"), "application/json")
    }

    func testNeverAsksForDrmOrIpCheckBypass() async throws {
        let transport = FakeTransport()
        try transport.on("/channel/", fixture: "live_CH_1")
        try transport.on("/media/external/", fixture: "vod_ok")
        let client = CTClient(transport: transport)
        _ = try await client.liveStreams(channelID: "CH_1")
        _ = try await client.vod(idec: "226411010101016")
        for request in transport.requests {
            let url = request.url!.absoluteString
            XCTAssertTrue(url.contains("canPlayDrm=false"))
            XCTAssertFalse(url.contains("skipIpAddressCheck"))
        }
    }

    func testVodRequestAndErrors() async throws {
        let transport = FakeTransport()
        try transport.on("/media/external/226411010101016?", fixture: "vod_ok")
        try transport.on("/media/external/403?", fixture: "vod_no_licence")
        let client = CTClient(transport: transport)
        guard case .stream = try await client.vod(idec: "226411010101016") else { return XCTFail() }
        do {
            _ = try await client.vod(idec: "403")
            XCTFail("expected error")
        } catch let error as CTError {
            XCTAssertEqual(error.userMessage, "ČT nemá práva pro\u{00A0}internet")
        }
    }

    func testScheduleRequestBody() async throws {
        let transport = FakeTransport()
        try transport.on("graphql", fixture: "schedule_ct1_ct24")
        let schedules = try await CTClient(transport: transport)
            .schedules(slugs: ["ct1", "ct24"], day: BroadcastDay(year: 2026, month: 10, day: 16))
        XCTAssertEqual(schedules["ct1"]?.count, 6)

        let request = try XCTUnwrap(transport.requests.first)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.url?.absoluteString,
                       "https://api.ceskatelevize.cz/graphql/?client=iVysilaniWeb&version=1.131.1&use-new-playability=true")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
        let body = try JSONSerialization.jsonObject(with: XCTUnwrap(request.httpBody)) as! [String: Any]
        let variables = body["variables"] as! [String: Any]
        XCTAssertEqual(variables["channels"] as? [String], ["ct1", "ct24"])
        XCTAssertEqual(variables["date"] as? String, "10.16.2026")
        XCTAssertTrue((body["query"] as! String).contains("TVProgramDailyChannelsPlanV2(channels: $channels, date: $date)"))
    }

    func testNetworkFailureBecomesCzechError() async {
        let transport = FakeTransport()
        transport.fail("", URLError(.notConnectedToInternet))
        let client = CTClient(transport: transport)
        do {
            _ = try await client.liveStreams(channelID: "CH_1")
            XCTFail("expected error")
        } catch let error as CTError {
            XCTAssertEqual(error.userMessage, "Stream se nepodařilo načíst")
        } catch {
            XCTFail("unexpected \(error)")
        }
        do {
            _ = try await client.schedules(slugs: ["ct1"], day: BroadcastDay(year: 2026, month: 10, day: 17))
            XCTFail("expected error")
        } catch let error as CTError {
            XCTAssertEqual(error.userMessage, "Program se nepodařilo načíst")
        } catch {
            XCTFail("unexpected \(error)")
        }
    }
}
