import XCTest

/// "Channel numbers and zapping" (spec/apps.md): in the live player the remote's down/up switches to the
/// next/previous channel of this TV and shows "<number>  <channel>". Opt-in: plays CT's real live streams;
/// starts on ČT1, which must be channel 1 on the simulator (Nastavení → Kanály).
///
///     TEST_RUNNER_ZAP=1 xcodebuild test -scheme Televize -destination 'platform=tvOS Simulator,name=Apple TV' \
///       -only-testing:TelevizeUITests/ZapUITests
@MainActor
final class ZapUITests: XCTestCase {
    func testDownAndUpSwitchChannels() throws {
        guard ProcessInfo.processInfo.environment["ZAP"] != nil else { throw XCTSkip("set TEST_RUNNER_ZAP=1") }
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-play", "live:CH_1"]
        app.launch()
        let banner = app.descendants(matching: .any)["zapBanner"]
        sleep(12)  // player open and playing
        XCUIRemote.shared.press(.down)
        XCTAssertTrue(banner.waitForExistence(timeout: 3), "no banner after down")
        attach(app, "after-down")
        XCTAssertTrue(banner.label.hasPrefix("2"), "down: \(banner.label)")
        sleep(8)  // ČT2 resolved and playing, banner gone
        XCUIRemote.shared.press(.up)
        XCTAssertTrue(banner.waitForExistence(timeout: 3), "no banner after up")
        XCTAssertTrue(banner.label.hasPrefix("1"), "up: \(banner.label)")
        XCUIRemote.shared.press(.up)  // wraps to the last channel
        sleep(1)
        XCTAssertFalse(banner.label.hasPrefix("1"), "wrap: \(banner.label)")
        attach(app, "after-wrap")
    }

    private func attach(_ app: XCUIApplication, _ name: String) {
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }
}
