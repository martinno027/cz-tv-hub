import Foundation
import XCTest
@testable import CTCore

final class ParserTests: XCTestCase {
    private func parse<T>(_ fixture: String, _ body: (HTTPResponse) throws -> T) throws -> T {
        try body(Fixture.response(fixture))
    }

    private func assertCTError(_ expression: @autoclosure () throws -> some Any, message: String, code: String? = nil,
                               file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertThrowsError(try expression(), file: file, line: line) { error in
            guard let error = error as? CTError else { return XCTFail("not a CTError: \(error)", file: file, line: line) }
            XCTAssertEqual(error.userMessage, message, file: file, line: line)
            if let code { XCTAssertEqual(error.code, code, file: file, line: line) }
        }
    }

    // MARK: live

    func testLiveStreams() throws {
        let streams = try parse("live_CH_1") { try CTParser.liveStreams(status: $0.status, body: $0.body, channelID: "CH_1") }
        XCTAssertTrue(streams.main.absoluteString.contains("contentType=live"))
        XCTAssertTrue(try XCTUnwrap(streams.timeshift).absoluteString.contains("contentType=timeshift"))
    }

    func testLiveBlocked() throws {
        let response = try Fixture.response("live_blocked")
        assertCTError(try CTParser.liveStreams(status: response.status, body: response.body, channelID: "CH_1"),
                      message: "Vysílání je blokováno")
    }

    func testLiveWithoutMainIsGenericError() {
        let body = Data(#"{"streamUrls": {"main": "", "timeshift": "https://x/ts", "isBlocked": null}}"#.utf8)
        assertCTError(try CTParser.liveStreams(status: 200, body: body, channelID: "CH_1"), message: "Stream se nepodařilo načíst")
    }

    func testLiveTimeshiftOptional() throws {
        let body = Data(#"{"streamUrls": {"main": "https://x/live", "unknown": [1, 2]}}"#.utf8)
        let streams = try CTParser.liveStreams(status: 200, body: body, channelID: "CH_1")
        XCTAssertEqual(streams.main.absoluteString, "https://x/live")
        XCTAssertNil(streams.timeshift)
    }

    func testLiveGarbageBodyDoesNotCrash() {
        assertCTError(try CTParser.liveStreams(status: 200, body: Data("<html>".utf8), channelID: "CH_1"),
                      message: "Stream se nepodařilo načíst")
        assertCTError(try CTParser.liveStreams(status: 200, body: Data("[1]".utf8), channelID: "CH_1"),
                      message: "Stream se nepodařilo načíst")
    }

    // MARK: VOD

    func testVodOk() throws {
        let result = try parse("vod_ok") { try CTParser.vod(status: $0.status, body: $0.body, idec: "226411010101016") }
        guard case .stream(let url) = result else { return XCTFail("expected stream, got \(result)") }
        XCTAssertTrue(url.absoluteString.contains("contentType=vod"))
    }

    func testVodSkipsEmptyStreamUrls() throws {
        let body = Data(#"{"streams": [{"url": null}, {"url": ""}, {"url": "https://x/2"}]}"#.utf8)
        XCTAssertEqual(try CTParser.vod(status: 200, body: body, idec: "1"), .stream(URL(string: "https://x/2")!))
    }

    func testVodStillLive() throws {
        let result = try parse("vod_still_live") { try CTParser.vod(status: $0.status, body: $0.body, idec: "226411058150032") }
        XCTAssertEqual(result, .stillLive(channelID: "CH_24"))
    }

    // CT's texts contain non-breaking spaces; they are shown verbatim.
    func testVodNoLicenceKeepsCTMessage() throws {
        let response = try Fixture.response("vod_no_licence")
        XCTAssertEqual(response.status, 403)
        assertCTError(try CTParser.vod(status: response.status, body: response.body, idec: "1"),
                      message: "ČT nemá práva pro\u{00A0}internet", code: "MISSING_LICENCE_NOW")
    }

    func testVodUnsupportedDrmAddsHintAndKeepsCode() throws {
        let response = try Fixture.response("vod_drm")
        XCTAssertEqual(response.status, 403)
        assertCTError(try CTParser.vod(status: response.status, body: response.body, idec: "1"),
                      message: "Video vyžaduje prohlížeč s\u{00A0}ochranou DRM\nPořad můžete pustit v oficiální aplikaci iVysílání.",
                      code: "UNSUPPORTED_DRM")
        let noText = Data(#"{"message": {"status": {"name": "UNSUPPORTED_DRM"}}}"#.utf8)
        assertCTError(try CTParser.vod(status: 403, body: noText, idec: "1"),
                      message: "Video není k dispozici\nPořad můžete pustit v oficiální aplikaci iVysílání.")
    }

    func testVodNotFound() throws {
        let response = try Fixture.response("vod_not_found")
        assertCTError(try CTParser.vod(status: response.status, body: response.body, idec: "1"),
                      message: "Video není k\u{00A0}dispozici", code: "OTHER")
    }

    func testVodNonJSONErrorBody() {
        assertCTError(try CTParser.vod(status: 502, body: Data("Bad Gateway".utf8), idec: "1"), message: "Video není k dispozici")
    }

    func testVodEmptyOkBody() {
        assertCTError(try CTParser.vod(status: 200, body: Data("{}".utf8), idec: "1"), message: "Video není k dispozici")
    }

    // MARK: schedule

    func testScheduleFixture() throws {
        let schedules = try parse("schedule_ct1_ct24") { try CTParser.schedules(status: $0.status, body: $0.body) }
        XCTAssertEqual(Set(schedules.keys), ["ct1", "ct24"])
        XCTAssertEqual(schedules["ct1"]?.count, 6)
        XCTAssertEqual(schedules["ct24"]?.count, 6)

        let episode = try XCTUnwrap(schedules["ct1"]?[1])
        XCTAssertEqual(episode.channel, "ct1")
        XCTAssertEqual(episode.idec, "224381492050002")
        XCTAssertEqual(episode.title, "Testovací pořad 3")
        XCTAssertEqual(episode.episodeTitle, "Testovací pořad 4")
        XCTAssertEqual(episode.genre, "Seriály")
        XCTAssertEqual(episode.start, utc("2026-10-16T07:00:00Z"))
        XCTAssertEqual(episode.end, utc("2026-10-16T07:51:00Z"))
        XCTAssertFalse(episode.isPlayableNow)
        XCTAssertFalse(episode.liveOnly)
        XCTAssertNotNil(episode.imageURL)
        XCTAssertEqual(episode.description, "Smyšlený popis pořadu pro testy.")

        // Simulcast: same IDEC on two channels must still give distinct ids.
        XCTAssertEqual(schedules["ct1"]?[0].idec, schedules["ct24"]?[0].idec)
        XCTAssertNotEqual(schedules["ct1"]?[0].id, schedules["ct24"]?[0].id)
    }

    func testScheduleSkipsMalformedEntries() throws {
        let body = Data("""
        {"data": {"TVProgramDailyChannelsPlanV2": [
          {"channel": "ct2", "program": [
            {"idec": "1", "title": "OK", "start": "2026-10-17T05:00:00.000Z", "end": "2026-10-17T06:00:00Z"},
            {"idec": "2", "title": "no end", "start": "2026-10-17T06:00:00.000Z"},
            {"title": "no idec", "start": "2026-10-17T06:00:00.000Z", "end": "2026-10-17T07:00:00.000Z"},
            {"idec": "3", "start": "yesterday", "end": "2026-10-17T07:00:00.000Z"},
            {"idec": 4, "start": "2026-10-17T07:00:00.000Z", "end": "2026-10-17T08:00:00.000Z", "isPlayableNow": null},
            "garbage",
            {"idec": "1", "title": "OK", "start": "2026-10-17T05:00:00.000Z", "end": "2026-10-17T06:00:00Z"}
          ]},
          {"program": []},
          {"channel": "ct1", "program": null}
        ]}}
        """.utf8)
        let schedules = try CTParser.schedules(status: 200, body: body)
        XCTAssertEqual(schedules["ct2"]?.map(\.idec), ["1", "4"])
        XCTAssertEqual(schedules["ct2"]?.last?.title, "")
        XCTAssertEqual(schedules["ct2"]?.last?.isPlayableNow, false)
        XCTAssertEqual(schedules["ct1"], [])
    }

    func testScheduleGraphQLError() throws {
        let response = try Fixture.response("graphql_error")
        assertCTError(try CTParser.schedules(status: response.status, body: response.body), message: "Program se nepodařilo načíst")
    }

    func testScheduleHTTPError() {
        assertCTError(try CTParser.schedules(status: 500, body: Data()), message: "Program se nepodařilo načíst")
    }
}
