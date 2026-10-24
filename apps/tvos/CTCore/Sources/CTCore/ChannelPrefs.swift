import Foundation
import Observation

/// "Kanály" (spec/apps.md): channels hidden and reordered on this TV only, in one list that mixes
/// CT internet, Anténa and Poslanecká sněmovna channels, plus a switch per source.
/// Keys: `ct:<API id>` for CT internet channels, `dvb:<gateway channel id>` for Anténa channels,
/// `psp:<stream id>` for the Chamber of Deputies' live streams.
/// Unknown keys are kept (channels of a source that is off keep their place).
public struct ChannelPrefs: Codable, Equatable, Sendable {
    public static let maxKeys = 500

    /// Where a channel comes from; a whole source can be switched off.
    public enum Source: String, Codable, Sendable, CaseIterable {
        case ct, dvb, psp
    }

    public var order: [String]
    public var hidden: [String]
    /// Sources switched off (raw values of `Source`); missing in stored JSON = all on.
    public var sourcesOff: [String]

    public init(order: [String] = [], hidden: [String] = [], sourcesOff: [String] = []) {
        self.order = order
        self.hidden = hidden
        self.sourcesOff = sourcesOff
    }

    private enum CodingKeys: String, CodingKey { case order, hidden, sourcesOff }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        order = try container.decodeIfPresent([String].self, forKey: .order) ?? []
        hidden = try container.decodeIfPresent([String].self, forKey: .hidden) ?? []
        sourcesOff = try container.decodeIfPresent([String].self, forKey: .sourcesOff) ?? []
    }

    public func isOn(_ source: Source) -> Bool { !sourcesOff.contains(source.rawValue) }

    public mutating func setOn(_ source: Source, _ isOn: Bool) {
        sourcesOff.removeAll { $0 == source.rawValue }
        if !isOn { sourcesOff.append(source.rawValue) }
    }

    public static func ctKey(_ channel: Channel) -> String { "ct:\(channel.id)" }
    public static func dvbKey(_ channel: DVBChannel) -> String { "dvb:\(channel.id)" }
    public static func pspKey(_ stream: PSPStream) -> String { stream.key }

    /// `all` = the list's keys in the saved order, then the rest in default order; `visible` = `all` without hidden.
    /// The input is already limited to the sources that are on.
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

    /// Swaps `key` with its neighbour in the arranged order; no-op at the edge or for an unknown key.
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
    /// A change by the viewer (not by `replace`): signed in to a profile it is sent to the gateway.
    @ObservationIgnored public var onChange: (ChannelPrefs) -> Void = { _ in }

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
        guard store(next) else { return }
        onChange(next)
    }

    /// Settings from a profile (or the TV's own coming back); not reported to `onChange`.
    public func replace(_ value: ChannelPrefs) {
        var next = value
        next.order = Array(next.order.prefix(ChannelPrefs.maxKeys))
        next.hidden = Array(next.hidden.suffix(ChannelPrefs.maxKeys))
        store(next)
    }

    @discardableResult
    private func store(_ next: ChannelPrefs) -> Bool {
        guard next != prefs else { return false }
        prefs = next
        backend.set(try? JSONEncoder().encode(next), forKey: Self.storageKey)
        return true
    }

    // MARK: arranged list

    /// The mixed channel list: CT internet channels, then the Anténa ones, then the Chamber's streams by
    /// default, only of the sources that are on, in this TV's order.
    public func channels(antenna: [DVBChannel]) -> (all: [MixedChannel], visible: [MixedChannel]) {
        prefs.arrange(MixedChannel.defaultList(antenna: antenna, prefs: prefs), key: \.key)
    }
}

/// One entry of the mixed channel list (Živě, guide, settings).
public enum MixedChannel: Sendable, Hashable, Identifiable {
    case ct(Channel)
    case antenna(DVBChannel)
    /// A live stream of the Chamber of Deputies (live only).
    case parliament(PSPStream)

    public var key: String {
        switch self {
        case .ct(let channel): return ChannelPrefs.ctKey(channel)
        case .antenna(let channel): return ChannelPrefs.dvbKey(channel)
        case .parliament(let stream): return ChannelPrefs.pspKey(stream)
        }
    }

    public var id: String { key }

    public var name: String {
        switch self {
        case .ct(let channel): return channel.name
        case .antenna(let channel): return channel.name
        case .parliament(let stream): return stream.name
        }
    }

    public var source: ChannelPrefs.Source {
        switch self {
        case .ct: return .ct
        case .antenna: return .dvb
        case .parliament: return .psp
        }
    }

    /// Default order: the CT catalogue, then the gateway's channels, then the Chamber's streams 1…5; sources
    /// that are off are left out.
    public static func defaultList(antenna: [DVBChannel], prefs: ChannelPrefs) -> [MixedChannel] {
        (prefs.isOn(.ct) ? Channels.all.map(MixedChannel.ct) : [])
            + (prefs.isOn(.dvb) ? antenna.map(MixedChannel.antenna) : [])
            + (prefs.isOn(.psp) ? PSPStreams.all.map(MixedChannel.parliament) : [])
    }
}
