import CTCore
import Foundation

enum Formatting {
    /// Times are shown in Prague time, matching CT's broadcast days.
    private static let timeFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "cs_CZ")
        formatter.timeZone = Calendar.prague.timeZone
        formatter.dateFormat = "H:mm"
        return formatter
    }()

    static func time(_ date: Date) -> String { timeFormatter.string(from: date) }

    static func range(_ programme: Programme) -> String {
        "\(time(programme.start))–\(time(programme.end))"
    }

    static func progress(_ programme: Programme, now: Date) -> Double {
        let total = programme.end.timeIntervalSince(programme.start)
        guard total > 0 else { return 0 }
        return min(1, max(0, now.timeIntervalSince(programme.start) / total))
    }
}

/// How a guide cell looks and what selecting it means for the viewer.
enum ProgrammeState {
    /// `buffer`: ended less than 3 h ago, plays from "Zpět v čase"; `archive`: iVysílání archive.
    case airing, buffer, archive, unavailable, future
    /// Guide filler where the channel has no programme.
    case offAir

    init(_ programme: Programme, now: Date) {
        switch DecisionRules.action(for: programme, now: now) {
        case .future: self = .future
        case .startOver where programme.isAiring(at: now): self = .airing
        case .startOver: self = .buffer
        case .vod: self = .archive
        case .vodUnavailable: self = .unavailable
        }
    }

    var statusText: String {
        switch self {
        case .airing: return "Právě běží – od začátku nebo živě"
        case .buffer: return "Zpět v čase"
        case .archive: return "Přehrát ze záznamu"
        case .unavailable: return "Ze záznamu zřejmě nedostupné"
        case .future: return "Ještě nezačalo"
        case .offAir: return "Mimo vysílání"
        }
    }

    var symbol: String {
        switch self {
        case .airing: return "dot.radiowaves.left.and.right"
        case .buffer: return "gobackward"
        case .archive: return "play.fill"
        case .unavailable: return "nosign"
        case .future: return "clock"
        case .offAir: return "moon"
        }
    }
}
