import Foundation

/// Weather on Domů (spec/apps.md "Domů", T59) from `GET /dvb/weather` (spec/dvb-gateway.md "Weather"). The gateway
/// does the WMO/AQI/pollen/warning rules; the app shows its texts as they come and only formats numbers and times.
public struct Weather: Sendable, Equatable {
    public struct Now: Sendable, Equatable {
        public let temperature: Double
        public let text: String?
        /// `sun`, `partly`, `cloud`, `fog`, `drizzle`, `rain`, `snow`, `storm`; anything else is shown as `cloud`.
        public let icon: String
    }

    public struct Day: Sendable, Equatable {
        public let date: BroadcastDay
        public let text: String?
        public let icon: String
        public let max: Double?
        public let min: Double?
        public let rainChance: Int?
    }

    public struct Pollen: Sendable, Equatable {
        public let name: String
        public let level: String
    }

    public struct Warning: Sendable, Equatable {
        public enum Level: String, Sendable, Equatable {
            case yellow, orange, red
        }

        /// Nil for a level the gateway does not document (shown without a colour).
        public let level: Level?
        public let event: String
        public let to: Date?
    }

    public let now: Now
    /// "07:13" / "16:19" as the gateway sends them (Prague time).
    public let sunrise: String?
    public let sunset: String?
    public let days: [Day]
    public let aqiText: String?
    public let pollen: [Pollen]
    public let warnings: [Warning]
    public let attribution: String?
}

/// The texts of Domů's weather (spec/apps.md "Domů", header and block 8).
public enum WeatherRules {
    /// Weather (and the location) is asked for again when Domů is shown and the last answer is older than this.
    public static let maxAge: TimeInterval = 30 * 60
    /// Rain chance shown on a week card from this percentage.
    public static let rainChanceShownFrom = 20
    /// The gateway's range for `lat`/`lon` (spec/dvb-gateway.md "Weather"); outside it the request goes without them.
    static let latRange = 48.0...51.5
    static let lonRange = 11.5...19.5

    public static let consentTitle = "Počasí podle polohy"
    /// Shown once before the first OS prompt (spec/apps.md "Location for weather").
    public static let consentText = "Počasí na Domů podle polohy této televize: poloha se zaokrouhlí asi na 10 km, "
        + "pošle se jen vaší bráně a ta podle ní zjistí počasí u Open-Meteo. Nikde se neukládá."
    public static let consentAllow = "Povolit"
    public static let consentDeny = "Nepoužívat"
    /// Nastavení → Server.
    public static let settingTitle = "Počasí podle polohy této televize"
    public static let settingNote = "Poloha zaokrouhlená asi na 10 km jde jen vaší bráně; bez ní platí místo nastavené "
        + "na bráně. Nikde se neukládá."
    public static let weekTitle = "Počasí na týden"

    public struct Coordinate: Sendable, Equatable {
        public let lat: Double
        public let lon: Double
    }

    /// Domů asks again: never asked, or the last answer (with or without weather) is older than `maxAge`.
    public static func needsRefresh(fetchedAt: Date?, now: Date) -> Bool {
        guard let fetchedAt else { return true }
        return now.timeIntervalSince(fetchedAt) >= maxAge || now < fetchedAt
    }

    /// The device's location rounded to 0.1° (half away from zero), the only thing sent to the gateway; nil for a
    /// value that is not a finite number or lies outside the gateway's range (then the request goes without it).
    public static func round(lat: Double, lon: Double) -> Coordinate? {
        guard lat.isFinite, lon.isFinite, latRange.contains(lat), lonRange.contains(lon) else { return nil }
        return Coordinate(lat: tenth(lat), lon: tenth(lon))
    }

    /// Rounds the shortest decimal form of the value (as typed, "49.95" → 50.0), not its binary approximation.
    private static func tenth(_ value: Double) -> Double {
        var decimal = Decimal(string: "\(value)", locale: Locale(identifier: "en_US_POSIX")) ?? Decimal(value)
        var rounded = Decimal()
        NSDecimalRound(&rounded, &decimal, 1, .plain)  // .plain = half away from zero
        return NSDecimalNumber(decimal: rounded).doubleValue
    }

    /// `/dvb/weather` or `/dvb/weather?lat=50.1&lon=14.4`.
    public static func path(_ location: Coordinate?) -> String {
        guard let location else { return "/dvb/weather" }
        return "/dvb/weather?lat=\(oneDecimal(location.lat))&lon=\(oneDecimal(location.lon))"
    }

    /// "8 °C, polojasno".
    public static func nowLine(_ now: Weather.Now) -> String {
        let temperature = "\(degrees(now.temperature)) °C"
        guard let text = now.text, let first = text.first else { return temperature }
        return "\(temperature), \(first.lowercased())\(text.dropFirst())"
    }

    /// "Slunce 7:13–16:19"; nil unless both are there.
    public static func sunLine(_ weather: Weather) -> String? {
        guard let rise = weather.sunrise.map(clock), let set = weather.sunset.map(clock) else { return nil }
        return "Slunce \(rise)–\(set)"
    }

    /// "Ovzduší: zhoršené"; nil without `aqiText`.
    public static func airLine(_ weather: Weather) -> String? {
        weather.aqiText.map { "Ovzduší: \($0)" }
    }

    /// "Pyl: bříza střední, tráva nízká"; nil without pollen.
    public static func pollenLine(_ weather: Weather) -> String? {
        guard !weather.pollen.isEmpty else { return nil }
        return "Pyl: " + weather.pollen.map { "\($0.name) \($0.level)" }.joined(separator: ", ")
    }

    /// "Výstraha ČHMÚ: Silný vítr do 18:00" (`to` in Prague time), on another day "… do po 16. 11. 18:00", without
    /// `to` (open-ended warnings such as fire risk) "… (do odvolání)".
    public static func warningLine(_ warning: Weather.Warning, now: Date, calendar: Calendar = .prague) -> String {
        let line = "Výstraha ČHMÚ: \(warning.event)"
        guard let to = warning.to else { return "\(line) (do odvolání)" }
        let parts = calendar.dateComponents([.hour, .minute], from: to)
        let time = "\(parts.hour ?? 0):\(String(format: "%02d", parts.minute ?? 0))"
        let day = BroadcastDay(containing: to, calendar: calendar)
        let today = BroadcastDay(containing: now, calendar: calendar)
        guard day != today else { return "\(line) do \(time)" }
        return "\(line) do \(weekdayDate(day, calendar: calendar)) \(time)"
    }

    /// The warnings still in force: one whose `to` has passed is left out (the answer may be 30 minutes old).
    public static func visibleWarnings(_ warnings: [Weather.Warning], now: Date) -> [Weather.Warning] {
        warnings.filter { $0.to.map { $0 > now } ?? true }
    }

    /// Block 8: the days from today on (a cached answer after midnight still starts with yesterday).
    public static func weekDays(_ days: [Weather.Day], today: BroadcastDay) -> [Weather.Day] {
        days.filter { $0.date >= today }
    }

    /// "Dnes" or "út 4. 11." (no "Zítra" on the week cards).
    public static func dayLabel(_ day: BroadcastDay, today: BroadcastDay, calendar: Calendar = .prague) -> String {
        day == today ? "Dnes" : weekdayDate(day, calendar: calendar)
    }

    /// "út 4. 11."
    static func weekdayDate(_ day: BroadcastDay, calendar: Calendar = .prague) -> String {
        let weekday = calendar.component(.weekday, from: day.startDate(calendar: calendar))
        return "\(BroadcastDay.weekdays[weekday - 1]) \(day.day). \(day.month)."
    }

    /// "9° / 2°"; one side alone when the other is missing, nil without both.
    public static func rangeLine(_ day: Weather.Day) -> String? {
        switch (day.max, day.min) {
        case let (max?, min?): return "\(degrees(max))° / \(degrees(min))°"
        case let (max?, nil): return "\(degrees(max))°"
        case let (nil, min?): return "\(degrees(min))°"
        case (nil, nil): return nil
        }
    }

    /// "srážky 60 %" from 20 %, else nil.
    public static func rainLine(_ day: Weather.Day) -> String? {
        guard let chance = day.rainChance, chance >= rainChanceShownFrom else { return nil }
        return "srážky \(chance) %"
    }

    /// Whole degrees, half away from zero, never "-0", ASCII hyphen as the minus (golden "temperature").
    static func degrees(_ value: Double) -> String {
        let rounded = Int(value.rounded(.toNearestOrAwayFromZero))
        return "\(rounded == 0 ? 0 : rounded)"
    }

    /// "07:13" → "7:13"; anything else as it came.
    static func clock(_ text: String) -> String {
        text.count == 5 && text.hasPrefix("0") ? String(text.dropFirst()) : text
    }

    private static func oneDecimal(_ value: Double) -> String {
        String(format: "%.1f", locale: Locale(identifier: "en_US_POSIX"), value)
    }
}

/// "Počasí podle polohy této televize" (spec/apps.md "Location for weather", "Weather texts"): the one-time question
/// before the OS is ever asked. Back on it is no answer: asked again at the next app start, the switch stays off.
public struct WeatherConsent: Sendable, Equatable {
    public enum Answer: Sendable {
        /// "Povolit"
        case allow
        /// "Nepoužívat": never ask the OS, the household place.
        case deny
        /// Back / Menu: nothing decided.
        case none
    }

    /// The stored answer; nil = never answered.
    public private(set) var stored: Bool?
    /// Back was pressed on the question in this run of the app (not stored).
    public private(set) var dismissedThisRun = false

    public init(stored: Bool?) {
        self.stored = stored
    }

    /// Domů shows the question: never answered and not put away in this run.
    public var asksOnDomu: Bool { stored == nil && !dismissedThisRun }
    /// The switch in Nastavení → Server, and the only state in which the OS is asked for the location.
    public var usesLocation: Bool { stored == true }

    public mutating func answer(_ answer: Answer) {
        switch answer {
        case .allow: stored = true
        case .deny: stored = false
        case .none: dismissedThisRun = true
        }
    }

    /// The switch turned off: at once, no question.
    public mutating func switchOff() {
        stored = false
    }
}

public enum WeatherParser {
    /// The weather to show; nil for `404` ("Počasí není zapnuté"), an answer with `error`, or one without `now`
    /// (Domů then shows the date only). Unknown fields are ignored, missing optional ones tolerated.
    public static func parse(status: Int, body: Data) throws -> Weather? {
        if status == 404 { return nil }
        guard (200..<300).contains(status) else { throw DVBParser.error(status: status, body: body) }
        guard let root = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] else {
            throw CTError("Brána Anténa neodpovídá", detail: "weather: body is not a JSON object")
        }
        guard text(root["error"]) == nil, let now = root["now"] as? [String: Any],
              let temperature = number(now["temperature"]) else { return nil }
        let sun = root["sun"] as? [String: Any]
        let air = root["air"] as? [String: Any]
        let days: [Weather.Day] = (root["days"] as? [Any] ?? []).compactMap { item in
            guard let item = item as? [String: Any], let date = day(item["date"]) else { return nil }
            return Weather.Day(date: date, text: text(item["text"]), icon: text(item["icon"]) ?? "cloud",
                               max: number(item["max"]), min: number(item["min"]),
                               rainChance: number(item["rainChance"]).map { Int($0.rounded()) })
        }
        let pollen: [Weather.Pollen] = (air?["pollen"] as? [Any] ?? []).compactMap { item in
            guard let item = item as? [String: Any], let name = text(item["name"]), let level = text(item["level"])
            else { return nil }
            return Weather.Pollen(name: name, level: level)
        }
        let warnings: [Weather.Warning] = (root["warnings"] as? [Any] ?? []).compactMap { item in
            guard let item = item as? [String: Any], let event = text(item["event"]) else { return nil }
            return Weather.Warning(level: text(item["level"]).flatMap(Weather.Warning.Level.init(rawValue:)),
                                   event: event, to: CTParser.date(item["to"]))
        }
        return Weather(
            now: Weather.Now(temperature: temperature, text: text(now["text"]), icon: text(now["icon"]) ?? "cloud"),
            sunrise: text(sun?["rise"]), sunset: text(sun?["set"]),
            days: days, aqiText: text(air?["aqiText"]), pollen: pollen, warnings: warnings,
            attribution: text(root["attribution"])
        )
    }

    private static func day(_ value: Any?) -> BroadcastDay? {
        guard let parts = text(value)?.split(separator: "-").compactMap({ Int($0) }), parts.count == 3,
              (1...12).contains(parts[1]), (1...31).contains(parts[2]) else { return nil }
        return BroadcastDay(year: parts[0], month: parts[1], day: parts[2])
    }

    private static func text(_ value: Any?) -> String? {
        guard let text = (value as? String)?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else { return nil }
        return text
    }

    private static func number(_ value: Any?) -> Double? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              number.doubleValue.isFinite else { return nil }
        return number.doubleValue
    }
}

extension DVBClient {
    /// Domů's weather; `location` already rounded (`WeatherRules.round`), nil = the gateway's household place. Nil
    /// when the gateway has none to show (404, `error`).
    public func weather(location: WeatherRules.Coordinate?) async throws -> Weather? {
        let response = try await get(WeatherRules.path(location))
        return try WeatherParser.parse(status: response.status, body: response.body)
    }
}
