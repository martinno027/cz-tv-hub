import Foundation

/// The header of Domů (spec/apps.md "Domů", T58): today's date, the name day or the state holiday, and at most two
/// calendar lines. All offline; golden cases `spec/fixtures/calendar_cases.json`.
public enum HomeCalendar {
    public struct Header: Sendable, Equatable {
        /// "středa 4. listopadu"
        public let fullDate: String
        /// "Svátek má Karel", "Dnes je státní svátek: …"; nil on a day with neither.
        public let nameLine: String?
        /// At most two, in this order: a state holiday tomorrow, a clock change, school holidays, new/full moon.
        public let lines: [String]
    }

    public enum MoonPhase: Sendable, Equatable {
        case new, full
    }

    public struct SchoolHoliday: Sendable, Equatable {
        public let name: String
        public let from: BroadcastDay
        public let to: BroadcastDay
    }

    static let maxLines = 2
    /// A clock change or the start of school holidays is announced this many days ahead.
    static let daysAhead = 6

    static let weekdays = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"]
    static let monthsGenitive = ["ledna", "února", "března", "dubna", "května", "června", "července", "srpna", "září",
                                 "října", "listopadu", "prosince"]

    /// Zákon č. 245/2000 Sb., fixed dates ("MM-DD").
    static let fixedHolidays: [String: String] = [
        "01-01": "Den obnovy samostatného českého státu",
        "05-01": "Svátek práce",
        "05-08": "Den vítězství",
        "07-05": "Den slovanských věrozvěstů Cyrila a Metoděje",
        "07-06": "Den upálení mistra Jana Husa",
        "09-28": "Den české státnosti",
        "10-28": "Den vzniku samostatného československého státu",
        "11-17": "Den boje za svobodu a demokracii a Mezinárodní den studentstva",
        "12-24": "Štědrý den",
        "12-25": "1. svátek vánoční",
        "12-26": "2. svátek vánoční",
    ]
    /// Days from Easter Sunday.
    static let easterHolidays: [Int: String] = [-2: "Velký pátek", 1: "Velikonoční pondělí"]

    /// The MŠMT nationwide school holidays (spring holidays depend on the district: not shown). Updated once a year
    /// with `spec/fixtures/calendar_cases.json`; outside the table nothing is shown.
    static let schoolHolidays: [SchoolHoliday] = [
        SchoolHoliday(name: "Podzimní prázdniny", from: BroadcastDay(year: 2026, month: 10, day: 29),
                      to: BroadcastDay(year: 2026, month: 10, day: 30)),
        SchoolHoliday(name: "Vánoční prázdniny", from: BroadcastDay(year: 2026, month: 12, day: 23),
                      to: BroadcastDay(year: 2027, month: 1, day: 3)),
        SchoolHoliday(name: "Pololetní prázdniny", from: BroadcastDay(year: 2027, month: 1, day: 29),
                      to: BroadcastDay(year: 2027, month: 1, day: 29)),
        SchoolHoliday(name: "Velikonoční prázdniny", from: BroadcastDay(year: 2027, month: 3, day: 25),
                      to: BroadcastDay(year: 2027, month: 3, day: 25)),
        SchoolHoliday(name: "Hlavní prázdniny", from: BroadcastDay(year: 2027, month: 7, day: 1),
                      to: BroadcastDay(year: 2027, month: 8, day: 31)),
    ]

    public static func header(for day: BroadcastDay) -> Header {
        let holiday = stateHoliday(day)
        let nameLine = holiday.map { "Dnes je státní svátek: \($0)" } ?? nameDayLine(day)
        var lines: [String] = []
        if let tomorrow = stateHoliday(day.plus(1)) { lines.append("Zítra je státní svátek: \(tomorrow)") }
        if let change = clockChangeLine(day) { lines.append(change) }
        if let school = schoolHolidayLine(day) { lines.append(school) }
        switch moonPhase(on: day) {
        case .full: lines.append("Dnes je úplněk")
        case .new: lines.append("Dnes je nov")
        case nil: break
        }
        return Header(fullDate: fullDate(day), nameLine: nameLine, lines: Array(lines.prefix(maxLines)))
    }

    /// "středa 4. listopadu": lower-case weekday, genitive month, no year.
    public static func fullDate(_ day: BroadcastDay) -> String {
        "\(weekdays[day.weekdayFromMonday]) \(day.day). \(monthsGenitive[day.month - 1])"
    }

    /// "Svátek má Karel", "Svátek mají Kašpar, Melichar a Baltazar"; nil on a day without a name.
    static func nameDayLine(_ day: BroadcastDay) -> String? {
        guard let names = NameDays.table[day.monthDay], let last = names.last else { return nil }
        if names.count == 1 { return "Svátek má \(last)" }
        return "Svátek mají \(names.dropLast().joined(separator: ", ")) a \(last)"
    }

    public static func stateHoliday(_ day: BroadcastDay) -> String? {
        if let fixed = fixedHolidays[day.monthDay] { return fixed }
        return easterHolidays[day.julianDay - easter(year: day.year).julianDay]
    }

    /// Easter Sunday by the Gregorian computus (Meeus/Jones/Butcher).
    public static func easter(year: Int) -> BroadcastDay {
        let a = year % 19, b = year / 100, c = year % 100
        let d = b / 4, e = b % 4
        let f = (b + 8) / 25, g = (b - f + 1) / 3
        let h = (19 * a + b - d - g + 15) % 30
        let i = c / 4, k = c % 4
        let l = (32 + 2 * e + 2 * i - h - k) % 7
        let m = (a + 11 * h + 22 * l) / 451
        let month = (h + l - 7 * m + 114) / 31
        let day = (h + l - 7 * m + 114) % 31 + 1
        return BroadcastDay(year: year, month: month, day: day)
    }

    /// The EU clock changes of `year`: the last Sunday of March (forward) and of October (back).
    public static func clockChanges(year: Int) -> (forward: BroadcastDay, back: BroadcastDay) {
        (lastSunday(year: year, month: 3), lastSunday(year: year, month: 10))
    }

    /// A clock change today or within the next 6 days.
    static func clockChangeLine(_ day: BroadcastDay) -> String? {
        let changes = clockChanges(year: day.year)
        for offset in 0...daysAhead {
            let date = day.plus(offset)
            let direction: String
            if date == changes.forward { direction = "o hodinu dopředu" } else if date == changes.back {
                direction = "o hodinu zpět"
            } else { continue }
            switch offset {
            case 0: return "Dnes se mění čas: \(direction)"
            case 1: return "Zítra se mění čas: \(direction)"
            default: return "V neděli \(date.day). \(monthsGenitive[date.month - 1]) se mění čas: \(direction)"
            }
        }
        return nil
    }

    /// "Dnes jsou vánoční prázdniny" during them, "Podzimní prázdniny 29.–30. října" when they start within 6 days.
    static func schoolHolidayLine(_ day: BroadcastDay) -> String? {
        if let current = schoolHolidays.first(where: { $0.from <= day && day <= $0.to }) {
            return "Dnes jsou \(current.name.prefix(1).lowercased() + current.name.dropFirst())"
        }
        let soon = schoolHolidays.first {
            let ahead = $0.from.julianDay - day.julianDay
            return ahead >= 1 && ahead <= daysAhead
        }
        return soon.map { "\($0.name) \(range($0.from, $0.to))" }
    }

    /// "29. ledna", "29.–30. října", "23. prosince – 3. ledna".
    static func range(_ from: BroadcastDay, _ to: BroadcastDay) -> String {
        if from == to { return "\(from.day). \(monthsGenitive[from.month - 1])" }
        if from.month == to.month { return "\(from.day).–\(to.day). \(monthsGenitive[to.month - 1])" }
        return "\(from.day). \(monthsGenitive[from.month - 1]) – \(to.day). \(monthsGenitive[to.month - 1])"
    }

    // MARK: moon

    /// New or full moon on this Prague calendar day (Meeus, Astronomical Algorithms, ch. 49: the mean phase plus
    /// the three largest periodic terms; error minutes to an hour).
    public static func moonPhase(on day: BroadcastDay, calendar: Calendar = .prague) -> MoonPhase? {
        // the lunation number around the day; k = 0 is the new moon of 6 January 2000
        let approx = (Double(day.julianDay) - 2_451_550.09766) / 29.530588861
        for k in Int(approx.rounded(.down)) - 1...Int(approx.rounded(.down)) + 1 {
            for phase in [MoonPhase.new, .full] {
                let instant = moonInstant(k: Double(k) + (phase == .full ? 0.5 : 0))
                if BroadcastDay(containing: instant, calendar: calendar) == day { return phase }
            }
        }
        return nil
    }

    /// The instant of the phase with lunation number `k` (integer = new moon, + 0.5 = full moon).
    static func moonInstant(k: Double) -> Date {
        let t = k / 1236.85
        let jde = 2_451_550.09766 + 29.530588861 * k + 0.00015437 * t * t - 0.000000150 * t * t * t
            + 0.00000000073 * t * t * t * t
        let e = 1 - 0.002516 * t - 0.0000074 * t * t
        // the Sun's and the Moon's mean anomaly
        let sun = radians(2.5534 + 29.10535670 * k - 0.0000014 * t * t - 0.00000011 * t * t * t)
        let moon = radians(201.5643 + 385.81693528 * k + 0.0107582 * t * t + 0.00001238 * t * t * t
                           - 0.000000058 * t * t * t * t)
        let full = k.truncatingRemainder(dividingBy: 1) != 0
        let correction = full
            ? -0.40614 * sin(moon) + 0.17302 * e * sin(sun) + 0.01614 * sin(2 * moon)
            : -0.40720 * sin(moon) + 0.17241 * e * sin(sun) + 0.01608 * sin(2 * moon)
        return Date(timeIntervalSince1970: (jde + correction - 2_440_587.5) * 86_400)
    }

    private static func radians(_ degrees: Double) -> Double {
        degrees.truncatingRemainder(dividingBy: 360) * .pi / 180
    }

    private static func lastSunday(year: Int, month: Int) -> BroadcastDay {
        var day = BroadcastDay(year: year, month: month, day: 31)  // March and October both have 31 days
        while day.weekdayFromMonday != 6 { day = day.plus(-1) }
        return day
    }
}

extension BroadcastDay {
    /// Julian day number (noon); differences are whole days, independent of time zones.
    var julianDay: Int {
        let a = (14 - month) / 12
        let y = year + 4800 - a
        let m = month + 12 * a - 3
        return day + (153 * m + 2) / 5 + 365 * y + y / 4 - y / 100 + y / 400 - 32045
    }

    /// 0 = Monday … 6 = Sunday.
    var weekdayFromMonday: Int { julianDay % 7 }

    /// "MM-DD"
    var monthDay: String { String(format: "%02d-%02d", month, day) }

    /// The day `days` later, by calendar arithmetic only.
    func plus(_ days: Int) -> BroadcastDay {
        var j = julianDay + days + 32044
        let g = j / 146_097
        j %= 146_097
        let c = (j / 36524 + 1) * 3 / 4
        j -= c * 36524
        let b = j / 1461
        j %= 1461
        let a = (j / 365 + 1) * 3 / 4
        j -= a * 365
        let y = g * 400 + c * 100 + b * 4 + a
        let m = (j * 5 + 308) / 153 - 2
        let d = j - (m + 4) * 153 / 5 + 122
        return BroadcastDay(year: y - 4800 + (m + 2) / 12, month: (m + 2) % 12 + 1, day: d + 1)
    }
}
