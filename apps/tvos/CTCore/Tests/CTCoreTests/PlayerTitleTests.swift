import Foundation
import XCTest
@testable import CTCore

final class PlayerTitleTests: XCTestCase {
    func testLive() {
        let end = Date(timeIntervalSince1970: 1000)
        XCTAssertEqual(PlayerTitle.live(channel: "CT 1 PHA HD T2", airing: ("Případy 1. oddělení", end)),
                       PlayerTitle(title: "Případy 1. oddělení", subtitle: "CT 1 PHA HD T2", until: end))
        XCTAssertEqual(PlayerTitle.live(channel: "ČT1", airing: nil), PlayerTitle(title: "ČT1"))
        XCTAssertEqual(PlayerTitle.live(channel: "ČT1", airing: ("", end)), PlayerTitle(title: "ČT1"))
    }

    func testProgramme() {
        XCTAssertEqual(PlayerTitle.programme("Na tělo", channel: "ČT1", episode: "8/9"),
                       PlayerTitle(title: "Na tělo", subtitle: "ČT1 · 8/9"))
        XCTAssertEqual(PlayerTitle.programme("Zprávy", channel: nil), PlayerTitle(title: "Zprávy"))
    }

    func testNextCheck() {
        let now = Date(timeIntervalSince1970: 0)
        XCTAssertEqual(PlayerTitle(title: "a", until: Date(timeIntervalSince1970: 20)).nextCheck(after: now),
                       Date(timeIntervalSince1970: 21))
        XCTAssertEqual(PlayerTitle(title: "a", until: Date(timeIntervalSince1970: 500)).nextCheck(after: now),
                       Date(timeIntervalSince1970: 60))
        XCTAssertEqual(PlayerTitle(title: "a").nextCheck(after: now), Date(timeIntervalSince1970: 60))
    }
}
