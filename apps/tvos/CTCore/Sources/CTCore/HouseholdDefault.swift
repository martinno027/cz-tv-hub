import Foundation
import Observation

// "Household default (T11)" (spec/apps.md "Kanály", spec/dvb-gateway.md "Household default channel list"): one channel
// list for the household, edited on /admin, that a TV not signed in and never changed by its viewer shows. The first
// change on the TV makes its list its own; "Obnovit výchozí" follows the default again.

/// The household default as `GET /dvb/defaults` answers it; `channelPrefs` nil = no default (the built-in order).
public struct HouseholdDefaults: Sendable, Equatable, Codable {
    public let channelPrefs: ChannelPrefs?
    public let version: Int

    public init(channelPrefs: ChannelPrefs?, version: Int) {
        self.channelPrefs = channelPrefs
        self.version = version
    }
}

/// Golden cases `household_default_cases.json`: `own` = the TV's list is its own, `local` = the TV's own list,
/// `householdDefault` = the cached default (nil = none) and its `version`.
public struct HouseholdDefaultState<Settings: Equatable & Sendable>: Equatable, Sendable {
    public var own: Bool
    public var local: Settings
    public var householdDefault: Settings?
    public var version: Int

    public init(own: Bool, local: Settings, householdDefault: Settings?, version: Int) {
        self.own = own
        self.local = local
        self.householdDefault = householdDefault
        self.version = version
    }

    /// What the TV shows while not signed in.
    public func effective(builtin: Settings) -> Settings {
        own ? local : (householdDefault ?? builtin)
    }
}

public enum HouseholdDefaultEvent<Settings: Equatable & Sendable>: Sendable {
    /// `GET /dvb/defaults` answered; applied only when `version` is newer.
    case defaults(Settings?, version: Int)
    /// The viewer changed the list (not signed in); `settings` = the new list.
    case change(Settings)
    /// Obnovit výchozí (not signed in).
    case reset
}

public enum HouseholdDefault {
    /// Stored prefs from before T11 that differ from the built-in ones are the TV's own.
    public static func migratedOwn(_ stored: ChannelPrefs) -> Bool {
        !stored.order.isEmpty || !stored.hidden.isEmpty || !stored.sourcesOff.isEmpty
    }

    /// `empty` = the TV's own list after a reset.
    public static func reduce<S>(_ state: HouseholdDefaultState<S>, _ event: HouseholdDefaultEvent<S>, empty: S)
        -> HouseholdDefaultState<S> {
        var next = state
        switch event {
        case .defaults(let settings, let version):
            guard version > state.version else { return state }
            next.householdDefault = settings
            next.version = version
        case .change(let settings):
            next.own = true
            next.local = settings
        case .reset:
            next.own = false
            next.local = empty
        }
        return next
    }
}

// MARK: - API

public protocol HouseholdDefaultsAPI: Sendable {
    func householdDefaults() async throws -> HouseholdDefaults
}

public enum HouseholdDefaultsParser {
    /// `200 {"channelPrefs": {...} | null, "version": <int>, "updated": …}`. A `channelPrefs` that is neither an
    /// object nor null, or no `version`, is an error (nothing is guessed); the lists inside are read like a profile's.
    public static func parse(status: Int, body: Data) throws -> HouseholdDefaults {
        try ProfileParser.check(HTTPResponse(status: status, body: body))
        guard let root = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] else {
            throw malformed("defaults: body is not a JSON object")
        }
        guard let number = root["version"] as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              !CFNumberIsFloatType(number), number.intValue >= 0 else {
            throw malformed("defaults: version missing")
        }
        switch root["channelPrefs"] {
        case is NSNull:
            return HouseholdDefaults(channelPrefs: nil, version: number.intValue)
        case let prefs as [String: Any]:
            guard let value = JSONValue(any: prefs) else { throw malformed("defaults: channelPrefs is not JSON") }
            return HouseholdDefaults(channelPrefs: ProfileDocument(fields: ["channelPrefs": value]).channelPrefs,
                                     version: number.intValue)
        default:
            throw malformed("defaults: channelPrefs missing or not an object")
        }
    }

    private static func malformed(_ detail: String) -> CTError {
        CTError("Brána Anténa neodpovídá", detail: detail)
    }
}

extension DVBClient: HouseholdDefaultsAPI {
    public func householdDefaults() async throws -> HouseholdDefaults {
        let response = try await get("/dvb/defaults")
        return try HouseholdDefaultsParser.parse(status: response.status, body: response.body)
    }
}

// MARK: - the TV

/// Runs the household default for the app: `own` and the cached default persisted, the effective list put into the
/// `ChannelPrefsStore` while not signed in. Signed in, the profile's list is in the store and the default is only
/// cached. The TV's own list is the store's while signed out (the profile keeps it aside while signed in).
@MainActor
@Observable
public final class HouseholdDefaultController {
    public static let storageKey = "householdDefault"

    /// The TV's list is its own: the household default does not apply.
    public private(set) var own: Bool
    public private(set) var cached: HouseholdDefaults?
    /// The gateway the cached default came from (its base URL).
    @ObservationIgnored private var origin: String?

    /// Signed in to a profile (the profile's list applies, not this one).
    @ObservationIgnored public var isSignedIn: () -> Bool = { false }
    /// The store got a list that is not the viewer's change (so the profile's "current settings" follow it).
    @ObservationIgnored public var onApply: (ChannelPrefs) -> Void = { _ in }

    @ObservationIgnored private let prefs: ChannelPrefsStore
    @ObservationIgnored private let backend: ResumeBackend
    @ObservationIgnored private var api: HouseholdDefaultsAPI?

    /// `tvOwn` = the TV's own stored list, used once to migrate a TV from before T11 (no stored state yet).
    public init(prefs: ChannelPrefsStore, tvOwn: ChannelPrefs, backend: ResumeBackend = UserDefaults.standard) {
        self.prefs = prefs
        self.backend = backend
        if let stored = backend.data(forKey: Self.storageKey).flatMap({ try? JSONDecoder().decode(Stored.self, from: $0) }) {
            own = stored.own
            cached = stored.householdDefault
            origin = stored.origin
        } else {
            own = HouseholdDefault.migratedOwn(tvOwn)
            cached = nil
        }
        persist()
    }

    /// The gateway (or none). Odebrat bránu or another address drops the cached default: a different gateway counts
    /// its versions from its own start.
    public func setAPI(_ api: HouseholdDefaultsAPI?, gateway: URL?) {
        self.api = api
        let next = gateway?.absoluteString
        guard next != origin else { return }
        origin = next
        cached = nil
        persist()
        applyIfFollowing()
    }

    /// The list the TV shows while not signed in and not `own`.
    public var followed: ChannelPrefs { cached?.channelPrefs ?? ChannelPrefs() }

    /// Obnovit výchozí does something: signed in it puts the default into the profile, unless the profile's list is
    /// that already; signed out it follows the default again, unless it already does.
    public var canReset: Bool { isSignedIn() ? prefs.prefs != followed : own }

    /// App start and Nastavení open. Any failure (no gateway, an older gateway's 404, offline) keeps the cached default.
    public func refresh() async {
        let asked = origin
        guard let api, let defaults = try? await api.householdDefaults(), origin == asked else { return }  // same gateway
        received(defaults)
    }

    public func received(_ defaults: HouseholdDefaults) {
        let next = reduce(.defaults(defaults.channelPrefs, version: defaults.version))
        guard next.version > cached?.version ?? 0 else { return }  // not newer
        cached = HouseholdDefaults(channelPrefs: next.householdDefault, version: next.version)
        persist()
        applyIfFollowing()
    }

    /// The viewer changed the list (wired to `ChannelPrefsStore.onChange`); signed out it becomes the TV's own.
    public func viewerChanged(_ next: ChannelPrefs) {
        guard !isSignedIn(), !own else { return }
        own = reduce(.change(next)).own
        persist()
    }

    /// Obnovit výchozí. Signed in: the profile's list becomes a copy of the default (or the built-in order), sent as
    /// any change; the TV's own state is untouched.
    public func reset() {
        if isSignedIn() {
            let followed = followed
            prefs.update { $0 = followed }
            return
        }
        own = reduce(.reset).own
        persist()
        prefs.replace(followed)
        onApply(prefs.prefs)
    }

    /// Signed out (or at start): the TV's own list (`tvOwn`, what the profile kept aside) or the default it follows.
    public func applySignedOut(tvOwn: ChannelPrefs) {
        guard !isSignedIn() else { return }
        prefs.replace(state(local: tvOwn).effective(builtin: ChannelPrefs()))
        onApply(prefs.prefs)
    }

    private func applyIfFollowing() {
        guard !isSignedIn(), !own, prefs.prefs != followed else { return }
        prefs.replace(followed)
        onApply(prefs.prefs)
    }

    private func reduce(_ event: HouseholdDefaultEvent<ChannelPrefs>) -> HouseholdDefaultState<ChannelPrefs> {
        HouseholdDefault.reduce(state(local: prefs.prefs), event, empty: ChannelPrefs())
    }

    private func state(local: ChannelPrefs) -> HouseholdDefaultState<ChannelPrefs> {
        HouseholdDefaultState(own: own, local: own ? local : ChannelPrefs(), householdDefault: cached?.channelPrefs,
                              version: cached?.version ?? 0)
    }

    // MARK: persistence

    private struct Stored: Codable {
        let own: Bool
        let householdDefault: HouseholdDefaults?
        var origin: String?
    }

    private func persist() {
        backend.set(try? JSONEncoder().encode(Stored(own: own, householdDefault: cached, origin: origin)), forKey: Self.storageKey)
    }
}

extension HouseholdDefaultController {
    /// Wires Kanály, the profile sync and the household default as the app runs them: a viewer change goes to both
    /// (signed out it makes the list the TV's own, signed in it is sent to the profile); settings coming in from the
    /// profile apply as they are while signed in, and signing out returns to the TV's own list or the default it
    /// follows. The profile's "current settings" follow what the TV shows, so a new profile starts with it.
    /// The thumbnails switch is not part of the default: it travels only with the profile.
    public func connect(profile: ProfileSyncController, thumbnails: @escaping () -> Bool,
                        applyThumbnails: @escaping (Bool) -> Void) {
        let prefs = prefs
        isSignedIn = { [weak profile] in profile?.isSignedIn ?? false }
        onApply = { [weak profile] next in profile?.settingsChanged(channelPrefs: next, thumbnails: thumbnails()) }
        profile.apply = { [weak self, weak profile] document in
            // the switch first: `onApply` reads it
            applyThumbnails(document.thumbnails)
            if profile?.isSignedIn ?? false {
                prefs.replace(document.channelPrefs)
            } else {
                self?.applySignedOut(tvOwn: document.channelPrefs)
            }
        }
        prefs.onChange = { [weak self, weak profile] next in
            self?.viewerChanged(next)
            profile?.settingsChanged(channelPrefs: next, thumbnails: thumbnails())
        }
    }
}
