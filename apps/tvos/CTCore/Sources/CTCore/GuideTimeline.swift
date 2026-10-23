import Foundation

/// Anything with a time span that can sit in a guide row.
public protocol TimedItem {
    var start: Date { get }
    var end: Date { get }
}

/// Horizontal geometry of the EPG grid: one time axis shared by all channel rows.
public struct GuideTimeline: Sendable, Equatable {
    public enum Segment<Item> {
        case gap(width: Double)
        case item(Item, width: Double)
    }

    public static let tickInterval: TimeInterval = 30 * 60

    public let start: Date
    public let end: Date
    public let pointsPerSecond: Double

    public init(start: Date, end: Date, pointsPerSecond: Double) {
        self.start = start
        self.end = max(end, start)
        self.pointsPerSecond = pointsPerSecond
    }

    /// Axis covering all programmes, rounded out to whole ticks. Empty input → the given day.
    public init(spans: [any TimedItem], fallbackDay: BroadcastDay, pointsPerSecond: Double, calendar: Calendar = .prague) {
        let first = spans.map(\.start).min() ?? fallbackDay.startDate(calendar: calendar)
        let last = spans.map(\.end).max() ?? fallbackDay.adding(days: 1, calendar: calendar).startDate(calendar: calendar)
        self.init(start: Self.floorToTick(first), end: Self.ceilToTick(last), pointsPerSecond: pointsPerSecond)
    }

    public var width: Double { end.timeIntervalSince(start) * pointsPerSecond }

    /// "Live column first" (spec/apps.md): index of the segment under `date` in a row's `segments`
    /// (which start at `start`), or of the nearest focusable one when that segment is a gap narrower
    /// than `minGapWidth` (not focusable). Nil when the row has nothing focusable.
    public func focusableSegment<Item>(at date: Date, in segments: [Segment<Item>], minGapWidth: Double) -> Int? {
        func isFocusable(_ segment: Segment<Item>) -> Bool {
            if case .gap(let width) = segment { return width >= minGapWidth }
            return true
        }
        let target = x(for: date)
        var left = 0.0
        var under = segments.count - 1
        for (index, segment) in segments.enumerated() {
            let width: Double
            switch segment {
            case .gap(let w): width = w
            case .item(_, let w): width = w
            }
            if target < left + width { under = index; break }
            left += width
        }
        guard under >= 0 else { return nil }
        // the segment itself, else the nearest focusable one (the later one first on a tie)
        for distance in 0..<segments.count {
            for index in [under + distance, under - distance] where segments.indices.contains(index) {
                if isFocusable(segments[index]) { return index }
            }
        }
        return nil
    }

    public func x(for date: Date) -> Double {
        min(max(date, start), end).timeIntervalSince(start) * pointsPerSecond
    }

    public func contains(_ date: Date) -> Bool { start <= date && date < end }

    /// Tick marks every 30 minutes, including `start`.
    public var ticks: [Date] {
        stride(from: start.timeIntervalSince1970, to: end.timeIntervalSince1970, by: Self.tickInterval)
            .map(Date.init(timeIntervalSince1970:))
    }

    /// Index of the tick at or just before `date`.
    public func tickIndex(for date: Date) -> Int {
        max(0, Int(date.timeIntervalSince(start) / Self.tickInterval))
    }

    /// Cells and gaps for one channel row, left to right, covering the whole axis. Overlapping
    /// entries are clipped so every row stays aligned to the shared axis. With `splitGapsAtTicks`,
    /// long gaps are cut at every tick so focus moving up/down through them stays near the same time.
    public func segments<Item: TimedItem>(for items: [Item], splitGapsAtTicks: Bool = false) -> [Segment<Item>] {
        var segments: [Segment<Item>] = []
        var cursor = start
        for item in items.sorted(by: { $0.start < $1.start }) {
            let from = max(item.start, cursor)
            let to = min(item.end, end)
            guard to > from else { continue }
            appendGap(from: cursor, to: from, split: splitGapsAtTicks, to: &segments)
            segments.append(.item(item, width: to.timeIntervalSince(from) * pointsPerSecond))
            cursor = to
        }
        appendGap(from: cursor, to: end, split: splitGapsAtTicks, to: &segments)
        return segments
    }

    private func appendGap<Item>(from: Date, to: Date, split: Bool, to segments: inout [Segment<Item>]) {
        var cursor = from
        if split {
            var tick = Self.floorToTick(cursor).addingTimeInterval(Self.tickInterval)
            while tick < to {
                segments.append(.gap(width: tick.timeIntervalSince(cursor) * pointsPerSecond))
                cursor = tick
                tick = tick.addingTimeInterval(Self.tickInterval)
            }
        }
        if to > cursor {
            segments.append(.gap(width: to.timeIntervalSince(cursor) * pointsPerSecond))
        }
    }

    static func floorToTick(_ date: Date) -> Date {
        Date(timeIntervalSince1970: (date.timeIntervalSince1970 / tickInterval).rounded(.down) * tickInterval)
    }

    static func ceilToTick(_ date: Date) -> Date {
        Date(timeIntervalSince1970: (date.timeIntervalSince1970 / tickInterval).rounded(.up) * tickInterval)
    }
}

extension GuideTimeline.Segment: Equatable where Item: Equatable {}
extension GuideTimeline.Segment: Sendable where Item: Sendable {}
