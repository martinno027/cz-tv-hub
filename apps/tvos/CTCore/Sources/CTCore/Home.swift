import Foundation

/// Domů (spec/apps.md "Domů", T58): what its rows show. The header is `HomeCalendar`.
public enum HomeRules {
    public static let continueTitle = "Pokračovat"
    public static let liveTitle = "Teď běží"
    public static let parliamentTitle = "Sněmovna"
    public static let moreChannels = "Další kanály"
    /// "Teď běží" shows the first ČT and Anténa channels of "Kanály".
    public static let liveCount = 10
    public static let gatewayUnreachable = "Brána antény není dostupná."
    public static let gatewayRejected = "Brána antény odmítla klíč. Zkontrolujte ho v Nastavení → Server."
    public static let recordingPaused = "Nahrávání je pozastavené: na disku nahrávek dochází místo."

    /// How the last `/dvb/status` failed.
    public enum StatusFailure: Sendable, Equatable {
        /// No answer, or an error other than 401.
        case unreachable
        /// 401: the key is wrong.
        case rejected
    }

    /// How a `/dvb/status` request failed; nil when nobody waited for it any more (not a failure, T57).
    public static func statusFailure(_ error: Error) -> StatusFailure? {
        if RequestCancellation.matches(error) { return nil }
        return (error as? CTError)?.code == "HTTP 401" ? .rejected : .unreachable
    }

    public struct Notice: Sendable, Equatable {
        public let text: String
        /// In the warning colour; the Tailscale state is not a warning.
        public let isWarning: Bool
    }

    /// The gateway notice, only with a gateway configured: Tailscale's "Připojuji se…" while it connects, else how
    /// the last `/dvb/status` failed, else the paused recording.
    public static func gatewayNotice(configured: Bool, tailnetLine: String?, failure: StatusFailure?,
                                     recordingPaused: Bool) -> Notice? {
        guard configured else { return nil }
        if let tailnetLine { return Notice(text: tailnetLine, isWarning: false) }
        switch failure {
        case .rejected: return Notice(text: gatewayRejected, isWarning: true)
        case .unreachable: return Notice(text: gatewayUnreachable, isWarning: true)
        case nil: return recordingPaused ? Notice(text: Self.recordingPaused, isWarning: true) : nil
        }
    }

    /// The channels of "Teď běží": the first ten ČT and Anténa channels of this TV's visible list (the Chamber's
    /// streams have their own row).
    public static func liveRow(_ visible: [MixedChannel]) -> [MixedChannel] {
        Array(visible.filter { $0.parliamentStream == nil }.prefix(liveCount))
    }
}

extension MixedChannel {
    public var parliamentStream: PSPStream? {
        if case .parliament(let stream) = self { return stream }
        return nil
    }
}
