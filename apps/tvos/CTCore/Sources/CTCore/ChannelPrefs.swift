import Foundation
import Observation

/// "Kanály" (spec/apps.md): channels hidden and reordered on this TV only.
/// Keys: `ct:<API id>` for CT internet channels, `dvb:<gateway channel id>` for Anténa channels.
/// The order applies within each group; unknown keys are kept.
public struct ChannelPrefs: Codable, Equatable, Sendable {
    public static let maxKeys = 500

    public var order: [String]
    public var hidden: [String]

    public init(order: [String] = [], hidden: [String] = []) {
        self.order = order
        self.hidden = hidden
    }

    public static func ctKey(_ channel: Channel) -> String { "ct:\(channel.id)" }
    public static func dvbKey(_ channel: DVBChannel) -> String { "dvb:\(channel.id)" }

    /// `all` = the group's keys in the saved order, then the rest in default order; `visible` = `all` without hidden.
    public func arrange(_ channels: [String]) -> (all: [String], visible: [String]) {
        let group = Set(channels)
        var all: [String] = []
        var seen = Set<String>()
        for key in order where group.contains(key) && seen.insert(key).inserted {
            all.append(key)
        }
        all += channels.filter { seen.insert($0).inserted }
        let hiddenSet = Set(hidden)
        return (all, all.filter { !hiddenSet.contains($0) })
    }

    /// Same arrangement for any channel type, by key.
    public func arrange<T>(_ channels: [T], key: (T) -> String) -> (all: [T], visible: [T]) {
        var byKey: [String: T] = [:]
        for channel in channels where byKey[key(channel)] == nil { byKey[key(channel)] = channel }
        let keys = arrange(channels.map(key))
        return (keys.all.compactMap { byKey[$0] }, keys.visible.compactMap { byKey[$0] })
    }

    public func isHidden(_ key: String) -> Bool { hidden.contains(key) }

    /// Swaps `key` with its neighbour in the group's arranged order; no-op at the edge or for an unknown key.
    public mutating func move(_ key: String, by delta: Int, in channels: [String]) {
        var all = arrange(channels).all
        guard let index = all.firstIndex(of: key) else { return }
        let target = index + delta
        guard all.indices.contains(target) else { return }
        all.swapAt(index, target)
        let group = Set(all)
        order = Array((all + order.filter { !group.contains($0) }).prefix(Self.maxKeys))
    }

    public mutating func setHidden(_ key: String, _ isHidden: Bool) {
        hidden.removeAll { $0 == key }
        if isHidden { hidden.append(key) }
        hidden = Array(hidden.suffix(Self.maxKeys))
    }

    public mutating func toggleHidden(_ key: String) { setHidden(key, !isHidden(key)) }

    public mutating func reset() { self = ChannelPrefs() }
}

/// Persists `ChannelPrefs` as JSON under `storageKey` (UserDefaults in the app).
@MainActor
@Observable
public final class ChannelPrefsStore {
    public static let storageKey = "channelPrefs"

    @ObservationIgnored private let backend: ResumeBackend

    public private(set) var prefs: ChannelPrefs

    public init(backend: ResumeBackend = UserDefaults.standard) {
        self.backend = backend
        prefs = backend.data(forKey: Self.storageKey)
            .flatMap { try? JSONDecoder().decode(ChannelPrefs.self, from: $0) } ?? ChannelPrefs()
    }

    public func update(_ change: (inout ChannelPrefs) -> Void) {
        var next = prefs
        change(&next)
        next.order = Array(next.order.prefix(ChannelPrefs.maxKeys))
        next.hidden = Array(next.hidden.suffix(ChannelPrefs.maxKeys))
        guard next != prefs else { return }
        prefs = next
        backend.set(try? JSONEncoder().encode(next), forKey: Self.storageKey)
    }

    // MARK: arranged groups

    public func internet() -> (all: [Channel], visible: [Channel]) {
        prefs.arrange(Channels.all, key: ChannelPrefs.ctKey)
    }

    public func antenna(_ channels: [DVBChannel]) -> (all: [DVBChannel], visible: [DVBChannel]) {
        prefs.arrange(channels, key: ChannelPrefs.dvbKey)
    }
}
