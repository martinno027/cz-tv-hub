import Foundation
import XCTest
@testable import CTCore

/// Golden cases `spec/fixtures/tailnet_cases.json` ("Tailscale v aplikaci").
final class TailnetTests: XCTestCase {
    private var fixture: [String: Any] { get throws { try Fixture.json("tailnet_cases") } }

    func testGoldenTargetCases() throws {
        let cases = try fixture["target"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 10)
        for item in cases {
            let address = item["address"] as! String
            XCTAssertEqual(TailnetRules.target(address: address), item["target"] as? String, address)
        }
    }

    func testGoldenStatusCases() throws {
        let cases = try fixture["status"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 10)
        for item in cases {
            let name = item["name"] as! String
            var info: TailnetInfo?
            if let raw = item["info"] as? [String: Any] {
                let json = String(data: try JSONSerialization.data(withJSONObject: raw), encoding: .utf8)!
                info = try XCTUnwrap(TailnetInfo.parse(json), name)
            }
            let status = TailnetRules.status(enabled: item["enabled"] as! Bool, info: info, error: item["error"] as? String)
            XCTAssertEqual(status.line, item["line"] as! String, name)
            XCTAssertEqual(status.qr, item["qr"] as! Bool, name)
            XCTAssertEqual(status.logout, item["logout"] as! Bool, name)
            XCTAssertEqual(status.usesBridge, item["usesBridge"] as! Bool, name)
        }
    }

    func testMissingAddressLine() {
        let status = TailnetRules.status(enabled: true, info: nil, error: TailnetRules.missingAddress)
        XCTAssertEqual(status.line, "Tailscale se nepodařilo spustit: chybí adresa brány.")
        XCTAssertFalse(status.usesBridge)
        XCTAssertFalse(status.logout)
    }

    func testLogoutFollowsTheIdentity() {
        let named = TailnetInfo(state: "NeedsLogin", selfName: "televize-appletv")
        XCTAssertTrue(TailnetRules.status(enabled: true, info: named, error: nil).logout)
        XCTAssertFalse(TailnetRules.status(enabled: true, info: TailnetInfo(state: "Running"), error: nil).logout)
    }

    func testInfoOfABridgeWithoutNode() throws {
        let info = try XCTUnwrap(TailnetInfo.parse(#"{"state":"Stopped"}"#))
        XCTAssertEqual(info, TailnetInfo(state: "Stopped"))
        XCTAssertNil(TailnetInfo.parse("not json"))
    }

    func testQRModules() throws {
        let modules = try XCTUnwrap(TailnetRules.qrModules("101\n010\n001"))
        XCTAssertEqual(modules, [[true, false, true], [false, true, false], [false, false, true]])
        XCTAssertNil(TailnetRules.qrModules(""))
        XCTAssertNil(TailnetRules.qrModules("10\n1"))
    }

    func testGatewayConfigStillParsesTheSameWay() {
        XCTAssertEqual(GatewayConfig(address: " pi.tail1234.ts.net ", key: " k ")?.baseURL.absoluteString,
                       "http://pi.tail1234.ts.net:8098")
        XCTAssertNil(GatewayConfig(address: "raspberrypi", key: " "))
        XCTAssertNil(GatewayConfig(address: "ftp://raspberrypi", key: "k"))
    }
}
