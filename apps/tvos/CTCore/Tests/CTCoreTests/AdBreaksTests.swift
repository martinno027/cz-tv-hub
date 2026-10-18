import Foundation
import XCTest
@testable import CTCore

/// "Přeskočit reklamu": golden cases shared with the other apps (spec/fixtures/ads).
final class AdBreaksTests: XCTestCase {
    private func goldenBreaks(_ skipCases: [String: Any]) -> [AdBreak] {
        (skipCases["breaks"] as! [[String: Any]]).map {
            AdBreak(start: ($0["start"] as! NSNumber).doubleValue, end: ($0["end"] as! NSNumber).doubleValue,
                    confidence: ($0["confidence"] as! NSNumber).doubleValue)
        }
    }

    func testParsingDoneMatchesGoldenBreaks() throws {
        let skipCases = try Fixture.json("ads/skip_cases")
        let response = try Fixture.response("ads/breaks_done")
        let breaks = try AdBreaks.parse(status: response.status, body: response.body)
        XCTAssertEqual(breaks, goldenBreaks(skipCases))
        XCTAssertEqual(breaks.last?.confidence, 1.0, "confidence defaults to 1")
    }

    func testPendingHasNoBreaks() throws {
        let response = try Fixture.response("ads/breaks_pending")
        XCTAssertEqual(try AdBreaks.parse(status: response.status, body: response.body), [])
    }

    func testFeatureOffIsAnError() throws {
        let response = try Fixture.response("ads/breaks_off")
        XCTAssertThrowsError(try AdBreaks.parse(status: response.status, body: response.body)) { error in
            XCTAssertEqual((error as? CTError)?.userMessage, "Přeskakování reklam není zapnuté")
        }
    }

    func testNonJSONBodyIsAnError() {
        XCTAssertThrowsError(try AdBreaks.parse(status: 200, body: Data("<html>".utf8))) { error in
            XCTAssertEqual((error as? CTError)?.userMessage, "Brána Anténa neodpovídá")
        }
    }

    func testBooleansAreNotNumbers() throws {
        let body = Data(#"{"status": "done", "breaks": [{"start": false, "end": true}, {"start": 1, "end": 30, "confidence": "x"}]}"#.utf8)
        XCTAssertEqual(try AdBreaks.parse(status: 200, body: body), [AdBreak(start: 1, end: 30, confidence: 1)])
    }

    func testGoldenSkipCases() throws {
        let skipCases = try Fixture.json("ads/skip_cases")
        XCTAssertEqual((skipCases["minRemainingSeconds"] as! NSNumber).doubleValue, AdBreaks.minRemainingSeconds)
        let breaks = goldenBreaks(skipCases)
        let cases = skipCases["cases"] as! [[String: Any]]
        XCTAssertFalse(cases.isEmpty)
        for item in cases {
            let position = (item["position"] as! NSNumber).doubleValue
            let expected = (item["skipTo"] as? NSNumber)?.doubleValue
            XCTAssertEqual(AdBreaks.skipTarget(breaks, position: position), expected, "position \(position)")
        }
        XCTAssertNil(AdBreaks.skipTarget(breaks, position: .nan))
        XCTAssertNil(AdBreaks.skipTarget([], position: 100))
    }

    func testClientRequestsBreaksWithBearerKey() async throws {
        let transport = FakeTransport()
        try transport.on("/dvb/recording/rec%201/breaks", fixture: "ads/breaks_done")
        let client = DVBClient(config: GatewayConfig(address: "127.0.0.1", key: "secret")!, transport: transport)
        let breaks = try await client.adBreaks(recordingID: "rec 1")
        XCTAssertEqual(breaks.count, 3)
        let request = try XCTUnwrap(transport.requests.first)
        XCTAssertEqual(transport.requests.count, 1)
        XCTAssertEqual(request.httpMethod ?? "GET", "GET")
        XCTAssertEqual(request.url?.absoluteString, "http://127.0.0.1:8098/dvb/recording/rec%201/breaks")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer secret")
    }

    func testClientSurfacesFeatureOff() async throws {
        let transport = FakeTransport()
        try transport.on("/breaks", fixture: "ads/breaks_off")
        let client = DVBClient(config: GatewayConfig(address: "pi", key: "k")!, transport: transport)
        do {
            _ = try await client.adBreaks(recordingID: "r1")
            XCTFail("expected error")
        } catch let error as CTError {
            XCTAssertEqual(error.code, "HTTP 404")
        }
    }
}
