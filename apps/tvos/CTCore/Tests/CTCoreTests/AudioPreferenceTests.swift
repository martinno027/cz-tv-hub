import XCTest
@testable import CTCore

final class AudioPreferenceTests: XCTestCase {
    func testCzechByDefault() {
        XCTAssertEqual(AudioPreference.languages(), ["cs"])
        XCTAssertEqual(AudioPreference.languages(selected: nil), ["cs"])
        XCTAssertEqual(AudioPreference.languages(selected: " "), ["cs"])
    }

    func testViewerChoiceFirstThenCzech() {
        XCTAssertEqual(AudioPreference.languages(selected: "en"), ["en", "cs"])
        XCTAssertEqual(AudioPreference.languages(selected: "EN"), ["en", "cs"])
        XCTAssertEqual(AudioPreference.languages(selected: "cs"), ["cs"])
        XCTAssertEqual(AudioPreference.languages(selected: "CS"), ["cs"])
    }
}
