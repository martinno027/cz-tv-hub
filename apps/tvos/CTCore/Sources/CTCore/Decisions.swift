import Foundation

/// What selecting a programme does (decision rules in spec/apps.md).
public enum ProgrammeAction: Sendable, Equatable {
    /// Not started yet; not playable.
    case future
    /// Play from the timeshift buffer, `offset` seconds after the buffer's oldest point.
    case startOver(offset: TimeInterval)
    /// Resolve the archive item by IDEC (only once the programme began at least W ago).
    case vod
    /// Not expected to play, but still ask the VOD API so CT's reason can be shown.
    case vodUnavailable
}

public enum DecisionRules {
    /// Length of CT's "Zpět v čase" buffer.
    public static let timeshiftWindow: TimeInterval = 3 * 3600
    /// Start a bit early rather than miss the beginning.
    public static let startMargin: TimeInterval = 30

    public static func action(for programme: Programme, now: Date) -> ProgrammeAction {
        if now < programme.start {
            return .future
        }
        // Airing, or began less than W ago: always the "Zpět v čase" buffer, regardless of
        // isPlayableNow. Decided by time only, never as a reaction to a VOD error.
        if now < programme.end || now.timeIntervalSince(programme.start) < timeshiftWindow {
            return .startOver(offset: startOverOffset(start: programme.start, now: now))
        }
        return programme.isPlayableNow ? .vod : .vodUnavailable
    }

    /// Seconds from the beginning of the timeshift buffer (`now − W`) to `start − M`.
    public static func startOverOffset(start: Date, now: Date) -> TimeInterval {
        let bufferStart = now.addingTimeInterval(-timeshiftWindow)
        return max(0, start.addingTimeInterval(-startMargin).timeIntervalSince(bufferStart))
    }

    /// Wall-clock moment start-over playback should begin at (for EXT-X-PROGRAM-DATE-TIME seeking).
    public static func startOverDate(start: Date) -> Date {
        start.addingTimeInterval(-startMargin)
    }

    /// Wall-clock moment a "Pokračovat" in the timeshift buffer seeks to: `start + progress` (no margin).
    public static func resumeDate(start: Date, progress: TimeInterval) -> Date {
        start.addingTimeInterval(progress)
    }

    /// Fallback for players without wall-clock seeking: seconds from the buffer's oldest point
    /// (`now − W`) to `start + progress`, at least 0.
    public static func resumeOffset(start: Date, progress: TimeInterval, now: Date) -> TimeInterval {
        let bufferStart = now.addingTimeInterval(-timeshiftWindow)
        return max(0, resumeDate(start: start, progress: progress).timeIntervalSince(bufferStart))
    }
}
