import Foundation

/// spec/apps.md "Image prefetch": which guide images to load around the focus, in which order, how many at a
/// time, and how long a missing Anténa thumbnail is not asked for again. Images stay in memory only.
public enum ImagePrefetch {
    public static let rowsAbove = 3
    public static let rowsBelow = 8
    /// Programmes overlapping ±3 h around the focused time.
    public static let span: TimeInterval = 3 * 3600
    /// A new window once the focus has moved more than this far from the last prefetch point.
    public static let refreshRows = 3
    public static let refreshTime: TimeInterval = 3600

    public struct Point: Sendable, Equatable {
        public let row: Int
        public let time: Date

        public init(row: Int, time: Date) {
            self.row = row
            self.time = time
        }
    }

    public static func needsRefresh(from last: Point?, to point: Point) -> Bool {
        guard let last else { return true }
        return abs(point.row - last.row) > refreshRows || abs(point.time.timeIntervalSince(last.time)) > refreshTime
    }

    /// The programmes of rows `point.row - 3 ... point.row + 8` overlapping `point.time ± 3 h`, nearest first:
    /// by rows, then by time from the focused time (0 inside the programme); on a tie the row below goes first.
    public static func window<Item: TimedItem>(rows: [[Item]], around point: Point) -> [(row: Int, item: Item)] {
        guard !rows.isEmpty else { return [] }
        let from = point.time.addingTimeInterval(-span)
        let to = point.time.addingTimeInterval(span)
        let rowRange = max(0, point.row - rowsAbove)...min(rows.count - 1, max(0, point.row + rowsBelow))
        var found: [(row: Int, item: Item, rowDistance: Int, timeDistance: TimeInterval)] = []
        for row in rowRange {
            for item in rows[row] where item.end > from && item.start < to {
                let timeDistance = max(0, item.start.timeIntervalSince(point.time), point.time.timeIntervalSince(item.end))
                found.append((row, item, abs(row - point.row), timeDistance))
            }
        }
        found.sort { lhs, rhs in
            if lhs.rowDistance != rhs.rowDistance { return lhs.rowDistance < rhs.rowDistance }
            if lhs.timeDistance != rhs.timeDistance { return lhs.timeDistance < rhs.timeDistance }
            if lhs.row != rhs.row { return lhs.row > rhs.row }
            return lhs.item.start < rhs.item.start
        }
        return found.map { ($0.row, $0.item) }
    }
}

/// Where an image comes from; each source has its own limit of concurrent requests.
public enum ImageLane: Sendable, Hashable, CaseIterable {
    /// ČT images from ČT's CDN.
    case ct
    /// Anténa thumbnails: the gateway runs at most 2 ffmpeg.
    case gateway

    public var limit: Int {
        switch self {
        case .ct: return 4
        case .gateway: return 2
        }
    }
}

/// The prefetch queue: pending keys in order, the ones running per lane. A new window drops the pending keys
/// that left it (running ones finish); the focused key goes to the front.
public struct ImageQueue<Key: Hashable>: Sendable where Key: Sendable {
    public private(set) var pending: [(key: Key, lane: ImageLane)] = []
    public private(set) var running: [Key: ImageLane] = [:]
    /// The keys of the last window, for retries.
    public private(set) var window: Set<Key> = []

    public init() {}

    /// Replaces the pending keys by `window` (already running ones are skipped), keeping a pending `keep`
    /// (the focused key) at the front.
    public mutating func replace(with window: [(key: Key, lane: ImageLane)], keep: Key? = nil) {
        self.window = Set(window.map(\.key))
        let kept = keep.flatMap { key in pending.first { $0.key == key } }
        var seen = Set(running.keys)
        var next: [(key: Key, lane: ImageLane)] = []
        if let kept {
            next.append(kept)
            seen.insert(kept.key)
        }
        for request in window where !seen.contains(request.key) {
            seen.insert(request.key)
            next.append(request)
        }
        pending = next
    }

    /// Moves (or adds) `key` to the front, unless it is already running.
    public mutating func prioritize(_ key: Key, lane: ImageLane) {
        guard running[key] == nil else { return }
        pending.removeAll { $0.key == key }
        pending.insert((key, lane), at: 0)
    }

    /// Takes the pending keys that may start now (front first, within each lane's limit) and marks them running.
    public mutating func start() -> [(key: Key, lane: ImageLane)] {
        var counts: [ImageLane: Int] = [:]
        for lane in running.values { counts[lane, default: 0] += 1 }
        var started: [(key: Key, lane: ImageLane)] = []
        var rest: [(key: Key, lane: ImageLane)] = []
        for request in pending {
            if counts[request.lane, default: 0] < request.lane.limit {
                counts[request.lane, default: 0] += 1
                running[request.key] = request.lane
                started.append(request)
            } else {
                rest.append(request)
            }
        }
        pending = rest
        return started
    }

    public mutating func finish(_ key: Key) {
        running[key] = nil
    }

    /// A busy answer (503) once its `Retry-After` has passed: the focused key goes back to the front, one still
    /// in the window to the back of the queue, any other is dropped. True when it was queued again.
    @discardableResult
    public mutating func retry(_ key: Key, lane: ImageLane, focused: Key?) -> Bool {
        guard running[key] == nil, !pending.contains(where: { $0.key == key }) else { return true }
        if key == focused {
            pending.insert((key, lane), at: 0)
        } else if window.contains(key) {
            pending.append((key, lane))
        } else {
            return false
        }
        return true
    }

    public mutating func removeAll() {
        pending = []
    }
}

/// Anténa thumbnails the gateway answered 404 for: not asked again for the guide's lifetime when the
/// recording is finished, for 60 s while it is still recording.
public struct ThumbnailMisses: Sendable {
    public static let growingRetry: TimeInterval = 60
    private var misses: [String: (final: Bool, at: Date)] = [:]

    public init() {}

    public mutating func record(_ recordingID: String, final: Bool, at now: Date) {
        misses[recordingID] = (final, now)
    }

    public func isMissing(_ recordingID: String, at now: Date) -> Bool {
        guard let miss = misses[recordingID] else { return false }
        return miss.final || now.timeIntervalSince(miss.at) < Self.growingRetry
    }

    public mutating func removeAll() {
        misses = [:]
    }
}

/// Least recently used in-memory cache, bounded by entry count and by total cost (e.g. bytes).
public struct LRUCache<Key: Hashable, Value> {
    public let capacity: Int
    public let costLimit: Int
    private var values: [Key: (value: Value, cost: Int)] = [:]
    private var order: [Key] = []
    public private(set) var totalCost = 0

    public init(capacity: Int, costLimit: Int = .max) {
        self.capacity = capacity
        self.costLimit = costLimit
    }

    public var count: Int { values.count }

    /// The value, marked as just used.
    public mutating func get(_ key: Key) -> Value? {
        guard let entry = values[key] else { return nil }
        touch(key)
        return entry.value
    }

    /// The value without marking it used.
    public func peek(_ key: Key) -> Value? { values[key]?.value }

    /// Stores `value`; an entry costing more than the whole limit is not kept.
    public mutating func set(_ key: Key, _ value: Value, cost: Int = 0) {
        remove(key)
        guard cost <= costLimit else { return }
        values[key] = (value, cost)
        totalCost += cost
        touch(key)
        while order.count > capacity || totalCost > costLimit { remove(order[0]) }
    }

    public mutating func remove(_ key: Key) {
        guard let entry = values.removeValue(forKey: key) else { return }
        totalCost -= entry.cost
        order.removeAll { $0 == key }
    }

    public mutating func removeAll(where shouldRemove: (Key) -> Bool) {
        for key in order where shouldRemove(key) { remove(key) }
    }

    private mutating func touch(_ key: Key) {
        if let index = order.lastIndex(of: key) { order.remove(at: index) }
        order.append(key)
    }
}
