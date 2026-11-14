import Foundation
import XCTest
@testable import CTCore

/// Domů's weather (spec/apps.md "Domů", T59): the `/dvb/weather` answer (spec/fixtures/dvb/dvb_weather_*.json), the
/// device location's rounding (spec/fixtures/weather_cases.json "round") and the texts on screen.
final class WeatherTests: XCTestCase {
    private func client(_ transport: FakeTransport) -> DVBClient {
        DVBClient(config: GatewayConfig(address: "127.0.0.1:1", key: "k")!, transport: transport)
    }

    private func okWeather() throws -> Weather {
        let response = try Fixture.response("dvb/dvb_weather_ok")
        return try XCTUnwrap(try WeatherParser.parse(status: response.status, body: response.body))
    }

    func testParsesTheAnswer() throws {
        let weather = try okWeather()
        XCTAssertEqual(weather.now, Weather.Now(temperature: 8.4, text: "Polojasno", icon: "partly"))
        XCTAssertEqual(weather.sunrise, "07:13")
        XCTAssertEqual(weather.sunset, "16:19")
        XCTAssertEqual(weather.days.count, 7)
        XCTAssertEqual(weather.days[1], Weather.Day(date: BroadcastDay(year: 2026, month: 11, day: 15), text: "Slabý déšť",
                                                    icon: "rain", max: 8.0, min: 3.5, rainChance: 60))
        XCTAssertEqual(weather.days.map(\.icon), ["partly", "rain", "cloud", "sun", "fog", "snow", "storm"])
        XCTAssertEqual(weather.aqiText, "zhoršené")
        XCTAssertEqual(weather.pollen, [Weather.Pollen(name: "olše", level: "nízká")])
        XCTAssertEqual(weather.warnings, [Weather.Warning(level: .yellow, event: "Silný vítr", to: utc("2026-11-14T17:00:00Z")),
                                          Weather.Warning(level: .orange, event: "Riziko požárů", to: nil)])
        XCTAssertEqual(weather.attribution, "Počasí: Open-Meteo.com (CC BY 4.0), CAMS · Výstrahy: ČHMÚ (CC BY 4.0)")
    }

    /// 404 "Počasí není zapnuté" and 200 with `error`: nothing to show (the header shows the date only).
    func testOffAndErrorShowNothing() throws {
        for name in ["dvb/dvb_weather_off", "dvb/dvb_weather_error"] {
            let response = try Fixture.response(name)
            XCTAssertNil(try WeatherParser.parse(status: response.status, body: response.body), name)
        }
        XCTAssertThrowsError(try WeatherParser.parse(status: 401, body: Data(#"{"error": "x"}"#.utf8)))
        XCTAssertThrowsError(try WeatherParser.parse(status: 200, body: Data("<html>".utf8)))
    }

    /// Unknown fields ignored, optional ones missing, broken entries dropped.
    func testToleratesMissingAndUnknownFields() throws {
        let body = Data(#"""
        {"now": {"temperature": -0.3, "future": 1}, "air": null, "extra": {"a": 1},
         "days": [{"date": "2026-11-14"}, {"date": "nope", "max": 3}, "x", {"date": "2026-11-15", "max": true}],
         "warnings": [{"level": "purple", "event": "Mráz"}, {"level": "red"}]}
        """#.utf8)
        let weather = try XCTUnwrap(try WeatherParser.parse(status: 200, body: body))
        XCTAssertEqual(weather.now, Weather.Now(temperature: -0.3, text: nil, icon: "cloud"))
        XCTAssertEqual(WeatherRules.nowLine(weather.now), "0 °C", "no \"-0\", no text")
        XCTAssertEqual(weather.days.map(\.date.description), ["2026-11-14", "2026-11-15"])
        XCTAssertNil(weather.days[1].max, "a boolean is not a temperature")
        XCTAssertNil(WeatherRules.rangeLine(weather.days[0]))
        XCTAssertEqual(weather.warnings, [Weather.Warning(level: nil, event: "Mráz", to: nil)])
        XCTAssertEqual(WeatherRules.warningLine(weather.warnings[0], now: utc("2026-11-14T10:00:00Z")),
                       "Výstraha ČHMÚ: Mráz (do odvolání)")
        XCTAssertNil(WeatherRules.sunLine(weather))
        XCTAssertNil(WeatherRules.airLine(weather))
        XCTAssertNil(WeatherRules.pollenLine(weather))
        XCTAssertNil(weather.attribution)
        XCTAssertNil(try WeatherParser.parse(status: 200, body: Data(#"{"days": []}"#.utf8)), "no now: nothing")
    }

    func testRoundingGoldenCases() throws {
        let cases = try XCTUnwrap(try Fixture.json("weather_cases")["round"] as? [[String: Any]])
        XCTAssertEqual(cases.count, 10)
        func value(_ raw: Any?) -> Double? {
            if let number = raw as? NSNumber { return number.doubleValue }
            switch raw as? String {
            case "NaN": return .nan
            case "inf": return .infinity
            case let text?: return Double(text)  // "abc" is not a number: nothing to send
            case nil: return nil
            }
        }
        for item in cases {
            let expected = (item["out"] as? [NSNumber]).map { WeatherRules.Coordinate(lat: $0[0].doubleValue,
                                                                                    lon: $0[1].doubleValue) }
            let result = value(item["lat"]).flatMap { lat in value(item["lon"]).flatMap { WeatherRules.round(lat: lat, lon: $0) } }
            XCTAssertEqual(result, expected, "\(item)")
        }
    }

    /// Only the rounded values go to the gateway; without a location the request has no query.
    func testRequestCarriesOnlyTheRoundedLocation() async throws {
        let transport = FakeTransport()
        try transport.on("/dvb/weather", fixture: "dvb/dvb_weather_ok")
        let location = WeatherRules.round(lat: 50.0755, lon: 14.4378)
        let weather = try await client(transport).weather(location: location)
        XCTAssertNotNil(weather)
        _ = try await client(transport).weather(location: nil)
        XCTAssertEqual(transport.requests.map { $0.url!.absoluteString },
                       ["http://127.0.0.1:1/dvb/weather?lat=50.1&lon=14.4", "http://127.0.0.1:1/dvb/weather"])
        XCTAssertEqual(transport.requests[0].value(forHTTPHeaderField: "Authorization"), "Bearer k")
        XCTAssertNil(transport.requests[0].value(forHTTPHeaderField: ClientHeader.field), "not a sign-in log route")
        // a location that rounds to nothing (abroad, not a number) is not sent
        XCTAssertNil(WeatherRules.round(lat: 52.52, lon: 13.40))
        XCTAssertEqual(WeatherRules.path(WeatherRules.round(lat: 52.52, lon: 13.40)), "/dvb/weather")
        XCTAssertEqual(WeatherRules.path(WeatherRules.Coordinate(lat: -0.0, lon: 15.0)), "/dvb/weather?lat=-0.0&lon=15.0")
    }

    func testOffThroughTheClient() async throws {
        let transport = FakeTransport()
        try transport.on("/dvb/weather", fixture: "dvb/dvb_weather_off")
        let weather = try await client(transport).weather(location: nil)
        XCTAssertNil(weather)
    }

    func testHeaderLines() throws {
        let weather = try okWeather()
        XCTAssertEqual(WeatherRules.nowLine(weather.now), "8 °C, polojasno")
        XCTAssertEqual(WeatherRules.sunLine(weather), "Slunce 7:13–16:19")
        XCTAssertEqual(WeatherRules.airLine(weather), "Ovzduší: zhoršené")
        XCTAssertEqual(WeatherRules.pollenLine(weather), "Pyl: olše nízká")
        let two = Weather(now: weather.now, sunrise: nil, sunset: nil, days: [], aqiText: nil,
                          pollen: [.init(name: "bříza", level: "střední"), .init(name: "tráva", level: "nízká")],
                          warnings: [], attribution: nil)
        XCTAssertEqual(WeatherRules.pollenLine(two), "Pyl: bříza střední, tráva nízká")
        XCTAssertEqual(WeatherRules.nowLine(Weather.Now(temperature: -2.5, text: "Sněžení", icon: "snow")),
                       "-3 °C, sněžení", "half away from zero")
    }

    /// `to` in Prague time (also across the clock change), the day when it is not today, "(do odvolání)" without `to`.
    func testWarningLine() throws {
        let weather = try okWeather()
        let now = utc("2026-11-14T10:00:00Z")
        XCTAssertEqual(weather.warnings.map { WeatherRules.warningLine($0, now: now) },
                       ["Výstraha ČHMÚ: Silný vítr do 18:00", "Výstraha ČHMÚ: Riziko požárů (do odvolání)"])
        let monday = Weather.Warning(level: .red, event: "Silný vítr", to: utc("2026-11-16T17:00:00Z"))
        XCTAssertEqual(WeatherRules.warningLine(monday, now: now), "Výstraha ČHMÚ: Silný vítr do po 16. 11. 18:00")
        // 23:30 UTC on the 14th is already the 15th in Prague
        let afterMidnight = Weather.Warning(level: .yellow, event: "Mráz", to: utc("2026-11-14T23:30:00Z"))
        XCTAssertEqual(WeatherRules.warningLine(afterMidnight, now: now), "Výstraha ČHMÚ: Mráz do ne 15. 11. 0:30")
        let summer = Weather.Warning(level: .orange, event: "Bouřky", to: utc("2026-07-01T07:05:00Z"))
        XCTAssertEqual(WeatherRules.warningLine(summer, now: utc("2026-07-01T04:00:00Z")), "Výstraha ČHMÚ: Bouřky do 9:05")
    }

    func testTemperatureGoldenCases() throws {
        let cases = try XCTUnwrap(try Fixture.json("weather_cases")["temperature"] as? [[String: Any]])
        XCTAssertGreaterThan(cases.count, 5)
        for item in cases {
            let value = (item["value"] as! NSNumber).doubleValue
            XCTAssertEqual(WeatherRules.nowLine(Weather.Now(temperature: value, text: nil, icon: "sun")),
                           item["now"] as? String, "\(value)")
            let day = Weather.Day(date: BroadcastDay(year: 2026, month: 11, day: 14), text: nil, icon: "sun",
                                  max: value, min: nil, rainChance: nil)
            XCTAssertEqual(WeatherRules.rangeLine(day), item["card"] as? String, "\(value)")
        }
    }

    func testWeekdayGoldenCases() throws {
        let weekday = try XCTUnwrap(try Fixture.json("weather_cases")["weekday"] as? [String: Any])
        // Calendar weekday 1 = Sunday; the fixture starts on Monday
        XCTAssertEqual(Array(BroadcastDay.weekdays.dropFirst()) + [BroadcastDay.weekdays[0]], weekday["names"] as? [String])
        func day(_ text: Any?) -> BroadcastDay {
            let parts = (text as! String).split(separator: "-").map { Int($0)! }
            return BroadcastDay(year: parts[0], month: parts[1], day: parts[2])
        }
        let cases = try XCTUnwrap(weekday["cases"] as? [[String: Any]])
        XCTAssertGreaterThan(cases.count, 2)
        for item in cases {
            XCTAssertEqual(WeatherRules.dayLabel(day(item["date"]), today: day(item["today"])), item["label"] as? String)
        }
    }

    func testWeekCards() throws {
        let weather = try okWeather()
        let today = BroadcastDay(year: 2026, month: 11, day: 14)
        XCTAssertEqual(weather.days.map { WeatherRules.dayLabel($0.date, today: today) },
                       ["Dnes", "ne 15. 11.", "po 16. 11.", "út 17. 11.", "st 18. 11.", "čt 19. 11.", "pá 20. 11."])
        XCTAssertEqual(WeatherRules.dayLabel(BroadcastDay(year: 2026, month: 11, day: 4), today: today), "st 4. 11.")
        XCTAssertEqual(weather.days.map { WeatherRules.rangeLine($0) },
                       ["9° / 2°", "8° / 4°", "7° / 4°", "7° / -1°", "5° / -2°", "2° / -3°", "12° / 6°"])
        XCTAssertEqual(weather.days.map { WeatherRules.rainLine($0) },
                       [nil, "srážky 60 %", "srážky 30 %", nil, nil, "srážky 70 %", "srážky 80 %"])
        let edge = Weather.Day(date: today, text: nil, icon: "cloud", max: nil, min: nil, rainChance: 19)
        XCTAssertNil(WeatherRules.rainLine(edge))
        XCTAssertEqual(WeatherRules.rainLine(Weather.Day(date: today, text: nil, icon: "cloud", max: nil, min: nil,
                                                         rainChance: 20)), "srážky 20 %")
    }

    func testRefreshAfterThirtyMinutes() {
        let start = utc("2026-11-14T10:00:00Z")
        XCTAssertTrue(WeatherRules.needsRefresh(fetchedAt: nil, now: start))
        XCTAssertFalse(WeatherRules.needsRefresh(fetchedAt: start, now: start.addingTimeInterval(29 * 60 + 59)))
        XCTAssertTrue(WeatherRules.needsRefresh(fetchedAt: start, now: start.addingTimeInterval(30 * 60)))
        XCTAssertTrue(WeatherRules.needsRefresh(fetchedAt: start, now: start.addingTimeInterval(-60)), "clock moved back")
    }

    func testConsentTextIsTheSpecs() {
        XCTAssertEqual(WeatherRules.consentText,
                       "Počasí na Domů podle polohy této televize: poloha se zaokrouhlí asi na 10 km, pošle se jen vaší "
                       + "bráně a ta podle ní zjistí počasí u Open-Meteo. Nikde se neukládá.")
    }

    func testPassedWarningsAreHidden() throws {
        let weather = try okWeather()
        XCTAssertEqual(WeatherRules.visibleWarnings(weather.warnings, now: utc("2026-11-14T16:59:00Z")).map(\.event),
                       ["Silný vítr", "Riziko požárů"])
        XCTAssertEqual(WeatherRules.visibleWarnings(weather.warnings, now: utc("2026-11-14T17:00:00Z")).map(\.event),
                       ["Riziko požárů"], "ended at its `to`; the open-ended one stays")
    }

    func testDaysBeforeTodayAreLeftOut() throws {
        let weather = try okWeather()
        XCTAssertEqual(WeatherRules.weekDays(weather.days, today: BroadcastDay(year: 2026, month: 11, day: 14)).count, 7)
        let afterMidnight = WeatherRules.weekDays(weather.days, today: BroadcastDay(year: 2026, month: 11, day: 15))
        XCTAssertEqual(afterMidnight.map(\.date.description).first, "2026-11-15")
        XCTAssertEqual(afterMidnight.count, 6)
    }

    /// Back on the question = no answer: the switch stays off, the OS is not asked, the next start asks again.
    func testConsentBackIsNoAnswer() {
        var consent = WeatherConsent(stored: nil)
        XCTAssertTrue(consent.asksOnDomu)
        XCTAssertFalse(consent.usesLocation)
        consent.answer(.none)
        XCTAssertFalse(consent.asksOnDomu, "not again in this run")
        XCTAssertFalse(consent.usesLocation, "switch off, OS never asked")
        XCTAssertNil(consent.stored, "nothing stored")
        XCTAssertTrue(WeatherConsent(stored: consent.stored).asksOnDomu, "the next start asks again")

        consent.answer(.allow)
        XCTAssertEqual(consent.stored, true)
        XCTAssertTrue(consent.usesLocation)
        XCTAssertFalse(WeatherConsent(stored: true).asksOnDomu)

        consent.switchOff()
        XCTAssertEqual(consent.stored, false)
        XCTAssertFalse(consent.usesLocation)
        // switched on again in Nastavení: the question first; Back there leaves it off
        consent.answer(.none)
        XCTAssertEqual(consent.stored, false)
        consent.answer(.deny)
        XCTAssertFalse(WeatherConsent(stored: false).asksOnDomu, "Nepoužívat is an answer")
    }
}
