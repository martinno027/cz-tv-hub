import Foundation
import XCTest
@testable import CTCore

/// Golden cases shared by all three apps: spec/fixtures/ct_antenna_match.json.
final class AntennaCTMatchTests: XCTestCase {
    func testChannelSlugs() throws {
        let cases = try Fixture.json("ct_antenna_match")["channels"] as! [[String: Any]]
        XCTAssertFalse(cases.isEmpty)
        for c in cases {
            XCTAssertEqual(AntennaCTMatch.slugs(channelName: c["name"] as! String), c["slugs"] as! [String], c["name"] as! String)
        }
    }

    func testProgrammes() throws {
        let block = try Fixture.json("ct_antenna_match")["programmes"] as! [String: Any]
        var schedule: [String: [Programme]] = [:]
        for p in block["schedule"] as! [[String: Any]] {
            let slug = p["slug"] as! String
            schedule[slug, default: []].append(Programme(
                channel: slug, idec: p["idec"] as! String, title: p["title"] as! String,
                start: utc(p["start"] as! String), end: utc(p["end"] as! String), isPlayableNow: true,
                imageURL: URL(string: p["imageUrl"] as! String)))
        }
        let cases = block["cases"] as! [[String: Any]]
        XCTAssertFalse(cases.isEmpty)
        for c in cases {
            let match = AntennaCTMatch.programme(start: utc(c["start"] as! String), end: utc(c["end"] as! String),
                                                 title: c["title"] as! String, channelName: c["channel"] as! String,
                                                 schedule: schedule)
            XCTAssertEqual(match?.idec, c["idec"] as? String, c["name"] as! String)
        }
    }
}
