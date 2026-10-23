import Foundation
import XCTest
@testable import CTCore

final class ChannelNumbersTests: XCTestCase {
    func testGoldenStepCases() throws {
        let cases = try Fixture.json("channel_numbers_cases")["step"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 9)
        for item in cases {
            let result = ChannelNumbers.step(item["visible"] as! [String], from: item["current"] as? String,
                                             by: item["by"] as! Int)
            XCTAssertEqual(result, item["key"] as? String, item["name"] as! String)
        }
    }

    func testGoldenTypeCases() throws {
        let cases = try Fixture.json("channel_numbers_cases")["type"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 13)
        for item in cases {
            let name = item["name"] as! String
            let result = ChannelNumbers.type(buffer: item["buffer"] as! String, digit: item["digit"] as! Int,
                                             count: item["count"] as! Int)
            XCTAssertEqual(result.buffer, item["bufferAfter"] as! String, name)
            XCTAssertEqual(result.commit, item["commit"] as! Bool, name)
        }
    }

    func testGoldenResolveCases() throws {
        let cases = try Fixture.json("channel_numbers_cases")["resolve"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 4)
        for item in cases {
            let result = ChannelNumbers.resolve(item["visible"] as! [String], number: item["number"] as! Int)
            XCTAssertEqual(result, item["key"] as? String, item["name"] as! String)
        }
    }

    func testNumber() {
        XCTAssertEqual(ChannelNumbers.number(of: "b", in: ["a", "b"]), 2)
        XCTAssertNil(ChannelNumbers.number(of: "c", in: ["a", "b"]))
    }
}
