import Foundation

// "Profil" (spec/apps.md, spec/dvb-gateway.md "Profiles"): this TV's settings kept in a profile on the gateway and
// loaded on any TV by signing in. Only settings travel; never the gateway address/key, "Pokračovat" or the password.
// The TV stores the profile name and the session token (Keychain in the app), never the password.

public enum ProfileRules {
    public static let maxNameLength = 32
    public static let passwordLengths = 4...64

    /// The name as stored: trimmed, NFC, 1–32 code points, no control characters; nil when not valid.
    public static func normalizedName(_ name: String) -> String? {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines).precomposedStringWithCanonicalMapping
        let scalars = trimmed.unicodeScalars
        guard (1...maxNameLength).contains(scalars.count),
              !scalars.contains(where: { $0.properties.generalCategory == .control }) else { return nil }
        return trimmed
    }

    /// Uniqueness key: lower case without diacritics ("Jana" = "jána").
    public static func key(_ name: String) -> String? {
        normalizedName(name).map { $0.folding(options: .diacriticInsensitive, locale: nil).lowercased() }
    }

    public static func isValidPassword(_ password: String) -> Bool {
        passwordLengths.contains(password.unicodeScalars.count)
    }

    public static let invalidName = "Jméno musí mít 1 až 32 znaků."
    public static let invalidPassword = "Heslo musí mít 4 až 64 znaků."
    public static let passwordsDiffer = "Hesla se neshodují"
    public static let needsGateway = "Profily se ukládají na bránu Raspberry. Nejdřív ji nastavte v Nastavení → Server."
    public static let signedOutMessage = "Profil byl odhlášen (smazaný nebo nové heslo). Platí zase nastavení této televize."
    public static let savedText = "Nastavení je uložené v profilu"
    public static let waitingText = "Čeká na uložení — brána není dostupná"
}

// MARK: - JSON kept as it came

/// A JSON value, so fields the app does not know survive a round trip unchanged.
public enum JSONValue: Sendable, Equatable {
    case null
    case bool(Bool)
    case int(Int)
    case double(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    /// From a `JSONSerialization` object; nil for anything that is not JSON.
    public init?(any value: Any) {
        switch value {
        case is NSNull:
            self = .null
        case let number as NSNumber:
            if CFGetTypeID(number) == CFBooleanGetTypeID() {
                self = .bool(number.boolValue)
            } else if CFNumberIsFloatType(number) {
                self = .double(number.doubleValue)
            } else {
                self = .int(number.intValue)
            }
        case let text as String:
            self = .string(text)
        case let items as [Any]:
            var result: [JSONValue] = []
            for item in items {
                guard let value = JSONValue(any: item) else { return nil }
                result.append(value)
            }
            self = .array(result)
        case let fields as [String: Any]:
            var result: [String: JSONValue] = [:]
            for (key, item) in fields {
                guard let value = JSONValue(any: item) else { return nil }
                result[key] = value
            }
            self = .object(result)
        default:
            return nil
        }
    }

    /// For `JSONSerialization`.
    public var any: Any {
        switch self {
        case .null: return NSNull()
        case .bool(let value): return value
        case .int(let value): return value
        case .double(let value): return value
        case .string(let value): return value
        case .array(let items): return items.map(\.any)
        case .object(let fields): return fields.mapValues(\.any)
        }
    }
}

// MARK: - settings document

/// `{"v": 1, "channelPrefs": <"Kanály" JSON>, "thumbnails": <"Náhledy anténních pořadů">}`; unknown fields (also inside
/// `channelPrefs`) are kept and sent back unchanged. Missing or malformed known fields read as the defaults.
public struct ProfileDocument: Sendable, Equatable {
    public static let currentVersion = 1

    public private(set) var fields: [String: JSONValue]

    public init(fields: [String: JSONValue] = [:]) {
        self.fields = fields
    }

    /// A new document holding these settings.
    public init(channelPrefs: ChannelPrefs, thumbnails: Bool) {
        self = ProfileDocument().exporting(channelPrefs: channelPrefs, thumbnails: thumbnails)
    }

    /// From a decoded JSON object; nil when it is not an object.
    public init?(json: Any) {
        guard case .object(let fields)? = JSONValue(any: json) else { return nil }
        self.fields = fields
    }

    public init?(data: Data) {
        guard let json = try? JSONSerialization.jsonObject(with: data) else { return nil }
        self.init(json: json)
    }

    public var jsonObject: [String: Any] { fields.mapValues(\.any) }

    public var data: Data {
        (try? JSONSerialization.data(withJSONObject: jsonObject, options: [.sortedKeys])) ?? Data("{}".utf8)
    }

    public var channelPrefs: ChannelPrefs {
        guard case .object(let prefs)? = fields["channelPrefs"] else { return ChannelPrefs() }
        func keys(_ name: String) -> [String] {
            guard case .array(let items)? = prefs[name] else { return [] }
            return items.compactMap { if case .string(let key) = $0 { return key } else { return nil } }
        }
        return ChannelPrefs(order: Array(keys("order").prefix(ChannelPrefs.maxKeys)),
                            hidden: Array(keys("hidden").suffix(ChannelPrefs.maxKeys)),
                            sourcesOff: keys("sourcesOff"))
    }

    /// Default on, like the TV switch.
    public var thumbnails: Bool {
        if case .bool(let value)? = fields["thumbnails"] { return value }
        return true
    }

    /// This document with the TV's current settings written over its known fields; the rest stays.
    public func exporting(channelPrefs prefs: ChannelPrefs, thumbnails: Bool) -> ProfileDocument {
        var result = fields
        if case .int? = result["v"] {} else { result["v"] = .int(Self.currentVersion) }
        var known: [String: JSONValue] = [:]
        if case .object(let existing)? = result["channelPrefs"] { known = existing }
        known["order"] = .array(prefs.order.map(JSONValue.string))
        known["hidden"] = .array(prefs.hidden.map(JSONValue.string))
        known["sourcesOff"] = .array(prefs.sourcesOff.map(JSONValue.string))
        result["channelPrefs"] = .object(known)
        result["thumbnails"] = .bool(thumbnails)
        return ProfileDocument(fields: result)
    }
}

// MARK: - sync reducer

/// Signed-in state (spec/apps.md "Profil" → Sync, golden cases `profile_cases.json` "sync"). `active` = the settings in
/// force on the TV, `local` = the TV's own settings kept aside while signed in, `version` = the profile version last seen.
public struct ProfileSyncState<Settings: Equatable & Sendable>: Equatable, Sendable {
    public var profile: String?
    public var active: Settings
    public var local: Settings?
    public var pending: Bool
    public var version: Int?

    public init(profile: String? = nil, active: Settings, local: Settings? = nil, pending: Bool = false, version: Int? = nil) {
        self.profile = profile
        self.active = active
        self.local = local
        self.pending = pending
        self.version = version
    }

    public var isSignedIn: Bool { profile != nil }
}

public enum ProfileSyncEvent<Settings: Equatable & Sendable>: Sendable {
    /// Signed in: the profile's settings become active, the TV's own are kept aside.
    case signIn(name: String, settings: Settings, version: Int)
    /// Created from the current settings: they are the profile's and the TV's own.
    case create(name: String, version: Int)
    /// The viewer changed a setting.
    case change(Settings)
    case pushed(version: Int)
    case pushFailed
    /// `GET /dvb/profile` answered.
    case remote(settings: Settings, version: Int)
    case signOut
    /// `401` from a profile call: the session is gone.
    case unauthorized
}

public enum ProfileSyncMessage: Sendable, Equatable {
    case signedOut

    public var text: String { ProfileRules.signedOutMessage }
}

public enum ProfileSync {
    public static func reduce<S>(_ state: ProfileSyncState<S>, _ event: ProfileSyncEvent<S>)
        -> (state: ProfileSyncState<S>, message: ProfileSyncMessage?) {
        var next = state
        switch event {
        case .signIn(let name, let settings, let version):
            next = ProfileSyncState(profile: name, active: settings, local: state.local ?? state.active, version: version)
        case .create(let name, let version):
            next = ProfileSyncState(profile: name, active: state.active, local: state.local ?? state.active, version: version)
        case .change(let settings):
            next.active = settings
            next.pending = state.isSignedIn
        case .pushed(let version):
            guard state.isSignedIn else { return (state, nil) }
            next.pending = false
            next.version = version
        case .pushFailed:
            break
        case .remote(let settings, let version):
            guard state.isSignedIn, !state.pending, version != state.version else { return (state, nil) }
            next.active = settings
            next.version = version
        case .signOut:
            next = ProfileSyncState(active: state.local ?? state.active)
        case .unauthorized:
            guard state.isSignedIn else { return (state, nil) }
            return (ProfileSyncState(active: state.local ?? state.active), .signedOut)
        }
        return (next, nil)
    }
}

// MARK: - API

/// A signed-in session as `POST /dvb/profiles` and `/dvb/profiles/login` answer it.
public struct ProfileSession: Sendable, Equatable {
    public let token: String
    public let name: String
    public let settings: ProfileDocument
    public let version: Int
}

public struct ProfileSnapshot: Sendable, Equatable {
    public let name: String
    public let settings: ProfileDocument
    public let version: Int
}

/// What the TV keeps in the Keychain: never the password.
public struct ProfileCredentials: Sendable, Equatable, Codable {
    public let name: String
    public let token: String

    public init(name: String, token: String) {
        self.name = name
        self.token = token
    }
}

public protocol ProfileAPI: Sendable {
    func createProfile(name: String, password: String, settings: ProfileDocument) async throws -> ProfileSession
    func login(name: String, password: String) async throws -> ProfileSession
    func profile(token: String) async throws -> ProfileSnapshot
    /// The new version.
    func saveProfile(token: String, settings: ProfileDocument) async throws -> Int
    func logout(token: String) async throws
}

public enum ProfileParser {
    /// `CTError.code` of a profile call whose session is gone (sign out locally).
    public static let signedOutCode = "PROFILE_SIGNED_OUT"
    public static let tokenHeader = "X-Cztvhub-Profile"

    public static func session(status: Int, body: Data) throws -> ProfileSession {
        let root = try object(status: status, body: body)
        guard let token = root["token"] as? String, !token.isEmpty, let version = int(root["version"]) else {
            throw malformed("profile session: token or version missing")
        }
        let name = root["name"] as? String ?? ""
        return ProfileSession(token: token, name: name, settings: document(root["settings"]), version: version)
    }

    public static func snapshot(status: Int, body: Data) throws -> ProfileSnapshot {
        let root = try object(status: status, body: body)
        guard let version = int(root["version"]) else { throw malformed("profile: version missing") }
        return ProfileSnapshot(name: root["name"] as? String ?? "", settings: document(root["settings"]), version: version)
    }

    public static func saved(status: Int, body: Data) throws -> Int {
        let root = try object(status: status, body: body)
        guard let version = int(root["version"]) else { throw malformed("profile save: version missing") }
        return version
    }

    /// The gateway's `code` of a profile session that is gone (spec/dvb-gateway.md "Profiles").
    public static let signedOutGatewayCode = "profileSignedOut"

    /// A non-2xx answer: the gateway's Czech `error` text. Only a `401` with `"code": "profileSignedOut"` means the
    /// session is gone; any other 401 (the app key's) is a gateway error and keeps the profile.
    public static func error(status: Int, body: Data) -> CTError {
        let base = DVBParser.error(status: status, body: body)
        let code = (try? JSONSerialization.jsonObject(with: body) as? [String: Any])?["code"] as? String
        if status == 401, code == signedOutGatewayCode {
            return CTError(base.userMessage, code: signedOutCode, detail: base.detail)
        }
        return base
    }

    static func check(_ response: HTTPResponse) throws {
        guard (200..<300).contains(response.status) else {
            throw error(status: response.status, body: response.body)
        }
    }

    private static func object(status: Int, body: Data) throws -> [String: Any] {
        try check(HTTPResponse(status: status, body: body))
        guard let root = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] else {
            throw malformed("profile: body is not a JSON object")
        }
        return root
    }

    private static func document(_ value: Any?) -> ProfileDocument {
        value.flatMap { ProfileDocument(json: $0) } ?? ProfileDocument()
    }

    private static func int(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        return number.intValue
    }

    private static func malformed(_ detail: String) -> CTError {
        CTError("Brána Anténa neodpovídá", detail: detail)
    }
}

extension DVBClient: ProfileAPI {
    public func createProfile(name: String, password: String, settings: ProfileDocument) async throws -> ProfileSession {
        let body: [String: Any] = ["name": name, "password": password, "settings": settings.jsonObject]
        let response = try await send("POST", "/dvb/profiles", json: body)
        return try ProfileParser.session(status: response.status, body: response.body)
    }

    public func login(name: String, password: String) async throws -> ProfileSession {
        let response = try await send("POST", "/dvb/profiles/login", json: ["name": name, "password": password])
        return try ProfileParser.session(status: response.status, body: response.body)
    }

    public func profile(token: String) async throws -> ProfileSnapshot {
        let response = try await send("GET", "/dvb/profile", token: token)
        try ProfileParser.check(response)
        return try ProfileParser.snapshot(status: response.status, body: response.body)
    }

    public func saveProfile(token: String, settings: ProfileDocument) async throws -> Int {
        let response = try await send("PUT", "/dvb/profile", json: ["settings": settings.jsonObject], token: token)
        try ProfileParser.check(response)
        return try ProfileParser.saved(status: response.status, body: response.body)
    }

    public func logout(token: String) async throws {
        let response = try await send("POST", "/dvb/profile/logout", token: token)
        try ProfileParser.check(response)
    }

    private func send(_ method: String, _ path: String, json: [String: Any]? = nil, token: String? = nil) async throws -> HTTPResponse {
        var headers: [String: String] = [:]
        if let token { headers[ProfileParser.tokenHeader] = token }
        var body: Data?
        if let json {
            body = try JSONSerialization.data(withJSONObject: json)
            headers["Content-Type"] = "application/json"
        }
        return try await send(method: method, path: path, body: body, headers: headers)
    }
}

// MARK: - the signed-in TV

/// Where the session lives (Keychain in the app).
@MainActor
public protocol ProfileCredentialStore: AnyObject {
    func load() -> ProfileCredentials?
    func save(_ credentials: ProfileCredentials)
    func delete()
}

/// Runs the sync for the app: the reducer's state persisted (no secrets), the session token in `credentials`, changes
/// pushed after `debounce` without another change, pending pushes retried by `refresh` / `retryPending`.
@MainActor
@Observable
public final class ProfileSyncController {
    public static let storageKey = "profileSync"
    public static let retryInterval: TimeInterval = 60

    public private(set) var state: ProfileSyncState<ProfileDocument>
    /// The last push failed and nothing was saved since: "Čeká na uložení — brána není dostupná".
    public private(set) var pushFailed = false
    public private(set) var isBusy = false

    /// Applies settings to the TV (Kanály + thumbnails switch) when they come from the profile or the TV's own return.
    @ObservationIgnored public var apply: (ProfileDocument) -> Void = { _ in }
    /// Shown once (an alert in the app).
    @ObservationIgnored public var onMessage: (ProfileSyncMessage) -> Void = { _ in }

    @ObservationIgnored private var api: ProfileAPI?
    @ObservationIgnored private let credentials: ProfileCredentialStore
    @ObservationIgnored private let backend: ResumeBackend
    @ObservationIgnored private let debounce: Duration
    @ObservationIgnored private var debounceTask: Task<Void, Never>?
    @ObservationIgnored private var isPushing = false

    /// `current` = the settings in force on the TV now (the stores are the truth for the known fields). The caller
    /// applies `state.active` once after wiring `apply` (it differs from `current` when a lost session was dropped).
    public init(current: ProfileDocument, credentials: ProfileCredentialStore, backend: ResumeBackend = UserDefaults.standard,
                debounce: Duration = .seconds(1)) {
        self.credentials = credentials
        self.backend = backend
        self.debounce = debounce
        var stored = backend.data(forKey: Self.storageKey).flatMap(Self.decode)
            ?? ProfileSyncState(active: current)
        stored.active = stored.active.exporting(channelPrefs: current.channelPrefs, thumbnails: current.thumbnails)
        let signedIn = credentials.load()
        if stored.isSignedIn, let signedIn {
            stored.profile = signedIn.name
            state = stored
        } else {
            // A session without state (or the reverse) is not usable: the TV's own settings.
            credentials.delete()
            state = stored.isSignedIn ? ProfileSync.reduce(stored, .signOut).state : stored
        }
        persist()
    }

    public var isSignedIn: Bool { state.isSignedIn }
    public var profileName: String? { state.profile }
    public var hasGateway: Bool { api != nil }

    /// A new gateway (or none). Removing it while signed in signs out locally first.
    public func setAPI(_ api: ProfileAPI?) {
        if api == nil, isSignedIn {
            let old = self.api
            let token = credentials.load()?.token
            if let old, let token { Task { try? await old.logout(token: token) } }
            dispatch(.signOut)
        }
        self.api = api
    }

    // MARK: viewer actions

    public func signIn(name: String, password: String) async throws {
        let api = try requireAPI()
        guard let normalized = ProfileRules.normalizedName(name) else { throw CTError(ProfileRules.invalidName) }
        guard ProfileRules.isValidPassword(password) else { throw CTError(ProfileRules.invalidPassword) }
        isBusy = true
        defer { isBusy = false }
        let session = try await api.login(name: normalized, password: password)
        cancelDebounce()
        credentials.save(ProfileCredentials(name: Self.name(session, typed: normalized), token: session.token))
        dispatch(.signIn(name: Self.name(session, typed: normalized), settings: session.settings, version: session.version))
    }

    public func create(name: String, password: String, repeated: String) async throws {
        let api = try requireAPI()
        guard let normalized = ProfileRules.normalizedName(name) else { throw CTError(ProfileRules.invalidName) }
        guard ProfileRules.isValidPassword(password) else { throw CTError(ProfileRules.invalidPassword) }
        guard password == repeated else { throw CTError(ProfileRules.passwordsDiffer) }
        isBusy = true
        defer { isBusy = false }
        let session = try await api.createProfile(name: normalized, password: password, settings: state.active)
        cancelDebounce()
        credentials.save(ProfileCredentials(name: Self.name(session, typed: normalized), token: session.token))
        dispatch(.create(name: Self.name(session, typed: normalized), version: session.version))
    }

    /// Odhlásit: the gateway is told (failure ignored), then the TV's own settings are back.
    public func signOut() async {
        guard isSignedIn else { return }
        cancelDebounce()
        if let api, let token = credentials.load()?.token {
            isBusy = true
            try? await api.logout(token: token)
            isBusy = false
        }
        dispatch(.signOut)
    }

    /// The viewer changed a setting on this TV; signed in, it is sent after the debounce.
    public func settingsChanged(channelPrefs: ChannelPrefs, thumbnails: Bool) {
        let next = state.active.exporting(channelPrefs: channelPrefs, thumbnails: thumbnails)
        guard next != state.active else { return }
        state = ProfileSync.reduce(state, .change(next)).state
        persist()
        guard isSignedIn else { return }
        cancelDebounce()
        let delay = debounce
        debounceTask = Task { [weak self] in
            try? await Task.sleep(for: delay)
            guard !Task.isCancelled else { return }
            await self?.push()
        }
    }

    /// App start and Nastavení open: a pending change is sent first, else a newer profile version is applied.
    public func refresh() async {
        guard isSignedIn, let api, let token = credentials.load()?.token else { return }
        if state.pending {
            await push()
            return
        }
        do {
            let snapshot = try await api.profile(token: token)
            guard credentials.load()?.token == token else { return }  // signed out or in again meanwhile
            dispatch(.remote(settings: snapshot.settings, version: snapshot.version))
        } catch {
            handle(error)
        }
    }

    /// Every 60 s: a failed push is sent again.
    public func retryPending() async {
        guard state.pending, debounceTask == nil else { return }
        await push()
    }

    // MARK: internals

    private func push() async {
        debounceTask = nil
        guard isSignedIn, state.pending, !isPushing, let api, let token = credentials.load()?.token else { return }
        isPushing = true
        defer { isPushing = false }
        let sent = state.active
        do {
            let version = try await api.saveProfile(token: token, settings: sent)
            guard credentials.load()?.token == token else { return }
            pushFailed = false
            dispatch(.pushed(version: version))
            // Changed again while the request was on its way: that change still has to go.
            if state.active != sent {
                dispatch(.change(state.active))
                await push()
            }
        } catch {
            guard credentials.load()?.token == token else { return }
            if !handle(error) {
                pushFailed = true
                dispatch(.pushFailed)
            }
        }
    }

    /// True when the error signed the TV out.
    @discardableResult
    private func handle(_ error: Error) -> Bool {
        guard (error as? CTError)?.code == ProfileParser.signedOutCode else { return false }
        cancelDebounce()
        dispatch(.unauthorized)
        return true
    }

    private func dispatch(_ event: ProfileSyncEvent<ProfileDocument>) {
        let before = state
        let (next, message) = ProfileSync.reduce(state, event)
        state = next
        if !next.isSignedIn {
            credentials.delete()
            pushFailed = false
        }
        persist()
        if next.active != before.active { apply(next.active) }
        if let message { onMessage(message) }
    }

    private func requireAPI() throws -> ProfileAPI {
        guard let api else { throw CTError(ProfileRules.needsGateway) }
        return api
    }

    private func cancelDebounce() {
        debounceTask?.cancel()
        debounceTask = nil
    }

    private static func name(_ session: ProfileSession, typed: String) -> String {
        session.name.isEmpty ? typed : session.name
    }

    // MARK: persistence (no secrets: name and token are in `credentials`)

    private func persist() {
        var root: [String: Any] = ["active": state.active.jsonObject, "pending": state.pending]
        if let local = state.local { root["local"] = local.jsonObject }
        if let version = state.version { root["version"] = version }
        root["signedIn"] = state.isSignedIn
        backend.set(try? JSONSerialization.data(withJSONObject: root), forKey: Self.storageKey)
    }

    private static func decode(_ data: Data) -> ProfileSyncState<ProfileDocument>? {
        guard let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let active = root["active"].flatMap({ ProfileDocument(json: $0) }) else { return nil }
        let signedIn = root["signedIn"] as? Bool ?? false
        return ProfileSyncState(profile: signedIn ? "" : nil, active: active,
                                local: root["local"].flatMap { ProfileDocument(json: $0) },
                                pending: signedIn && (root["pending"] as? Bool ?? false),
                                version: signedIn ? root["version"] as? Int : nil)
    }
}
