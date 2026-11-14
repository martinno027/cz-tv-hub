import XCTest

/// Domů's weather (spec/apps.md "Domů", T59) pressed through XCUIRemote: the header shows the weather, and the week
/// (no focus stop of its own) scrolls into view once the focus moves in the last row. Opt-in: needs a running (fake)
/// gateway whose `/dvb/weather` answers like `spec/fixtures/dvb/dvb_weather_ok.json`. "Nepoužívat" is set through the
/// launch arguments, so neither the app's question nor the OS prompt appears.
///
///     TEST_RUNNER_WEATHER_GATEWAY=127.0.0.1:58093 TEST_RUNNER_WEATHER_KEY=dev-key-dev-key-dev-key-dev-key-0000 \
///       xcodebuild test -scheme Televize -destination 'platform=tvOS Simulator,name=…' \
///       -only-testing:TelevizeUITests/HomeWeatherUITests
@MainActor
final class HomeWeatherUITests: XCTestCase {
    private func keep(_ screenshot: XCUIScreenshot) {
        let attachment = XCTAttachment(screenshot: screenshot)
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func testWeekScrollsIntoView() throws {
        let env = ProcessInfo.processInfo.environment
        guard let gateway = env["WEATHER_GATEWAY"], let key = env["WEATHER_KEY"] else {
            throw XCTSkip("set TEST_RUNNER_WEATHER_GATEWAY and TEST_RUNNER_WEATHER_KEY")
        }
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-gatewayAddress", gateway, "-gatewayKey", key, "-weatherUseLocation", "<false/>"]
        app.launch()
        XCTAssertTrue(app.descendants(matching: .any)["home.weather.now"].waitForExistence(timeout: 15))
        keep(app.screenshot())
        let week = app.descendants(matching: .any)["home.week"].firstMatch
        XCTAssertTrue(week.waitForExistence(timeout: 5))
        XCUIRemote.shared.press(.right)
        sleep(2)
        keep(app.screenshot())
        XCTAssertTrue(app.windows.firstMatch.frame.contains(week.frame), "week \(week.frame) not on screen")
    }
}

/// The location question (spec/apps.md "Weather texts"): Menu on it is no answer, so the next start asks again and
/// meanwhile the weather comes without a location. Same opt-in gateway as above; `-weatherConsentReset YES` (DEBUG)
/// clears a stored answer first.
@MainActor
final class HomeWeatherConsentUITests: XCTestCase {
    func testMenuOnTheQuestionIsNoAnswer() throws {
        let env = ProcessInfo.processInfo.environment
        guard let gateway = env["WEATHER_GATEWAY"], let key = env["WEATHER_KEY"] else {
            throw XCTSkip("set TEST_RUNNER_WEATHER_GATEWAY and TEST_RUNNER_WEATHER_KEY")
        }
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-gatewayAddress", gateway, "-gatewayKey", key, "-weatherConsentReset", "YES"]
        app.launch()
        let question = app.descendants(matching: .any)["weather.consent"].firstMatch
        XCTAssertTrue(question.waitForExistence(timeout: 15))
        XCTAssertTrue(app.buttons["weather.consent.allow"].exists)
        XCTAssertTrue(app.buttons["weather.consent.deny"].exists)
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(question.waitForNonExistence(timeout: 5))
        // weather without the location (no OS prompt: the switch stays off)
        XCTAssertTrue(app.descendants(matching: .any)["home.weather.now"].waitForExistence(timeout: 15))
        XCTAssertFalse(question.exists)
        // the next start asks again (nothing stored)
        app.terminate()
        app.launchArguments = ["-gatewayAddress", gateway, "-gatewayKey", key]
        app.launch()
        XCTAssertTrue(app.descendants(matching: .any)["weather.consent"].firstMatch.waitForExistence(timeout: 15),
                      "asked again at the next start")
    }
}
