import Foundation
import XCTest
@testable import CTCore

final class ProfileRulesTests: XCTestCase {
    func testGoldenNames() throws {
        let cases = try Fixture.json("profile_cases")["names"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 10)
        for item in cases {
            let name = item["name"] as! String
            if item["valid"] as! Bool {
                XCTAssertEqual(ProfileRules.normalizedName(name), item["normalized"] as? String, name)
                XCTAssertEqual(ProfileRules.key(name), item["key"] as? String, name)
            } else {
                XCTAssertNil(ProfileRules.normalizedName(name), name)
                XCTAssertNil(ProfileRules.key(name), name)
            }
        }
    }

    func testGoldenPasswords() throws {
        let cases = try Fixture.json("profile_cases")["passwords"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 5)
        for item in cases {
            let password = item["password"] as! String
            XCTAssertEqual(ProfileRules.isValidPassword(password), item["valid"] as! Bool, password)
        }
    }

    func testNameIsNFCAndCountsCodePoints() {
        // "á" decomposed (a + combining acute) is one code point after NFC
        let decomposed = "j" + "a\u{0301}" + "na"
        XCTAssertEqual(ProfileRules.normalizedName(decomposed), "jána")
        XCTAssertEqual(ProfileRules.normalizedName(String(repeating: "á", count: 32)), String(repeating: "á", count: 32))
        XCTAssertNil(ProfileRules.normalizedName(String(repeating: "á", count: 33)))
    }
}

final class ProfileDocumentTests: XCTestCase {
    func testGoldenDocuments() throws {
        let cases = try Fixture.json("profile_cases")["document"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 3)
        for item in cases {
            let name = item["name"] as! String
            let document = try XCTUnwrap(ProfileDocument(json: item["incoming"]!), name)
            let applied = item["applied"] as! [String: Any]
            let prefs = applied["channelPrefs"] as! [String: Any]
            XCTAssertEqual(document.channelPrefs, ChannelPrefs(order: prefs["order"] as! [String], hidden: prefs["hidden"] as! [String],
                                                                sourcesOff: prefs["sourcesOff"] as! [String]), name)
            XCTAssertEqual(document.thumbnails, applied["thumbnails"] as! Bool, name)
            let exported = document.exporting(channelPrefs: document.channelPrefs, thumbnails: item["changeThumbnails"] as! Bool)
            XCTAssertEqual(JSONValue(any: exported.jsonObject), JSONValue(any: item["exported"]!), name)
            // through bytes too (what is sent and stored)
            XCTAssertEqual(ProfileDocument(data: exported.data), exported, name)
        }
    }

    func testMalformedFieldsReadAsDefaults() {
        let document = ProfileDocument(json: ["channelPrefs": ["order": ["ct:CH_1", 7, NSNull()], "hidden": "x"],
                                              "thumbnails": 0] as [String: Any])!
        XCTAssertEqual(document.channelPrefs, ChannelPrefs(order: ["ct:CH_1"]))
        XCTAssertTrue(document.thumbnails, "a number is not the switch")
        XCTAssertNil(ProfileDocument(json: [1, 2]))
    }

    func testBoolAndNumberStayApart() {
        XCTAssertEqual(JSONValue(any: true), .bool(true))
        XCTAssertEqual(JSONValue(any: NSNumber(value: 1)), .int(1))
        XCTAssertEqual(JSONValue(any: 1.5), .double(1.5))
        XCTAssertNotEqual(JSONValue(any: true), JSONValue(any: 1))
    }
}

final class ProfileSyncReducerTests: XCTestCase {
    private func state(_ raw: [String: Any]) -> ProfileSyncState<String> {
        ProfileSyncState(profile: raw["profile"] as? String, active: raw["active"] as! String, local: raw["local"] as? String,
                         pending: raw["pending"] as! Bool, version: raw["version"] as? Int)
    }

    private func event(_ raw: [String: Any]) -> ProfileSyncEvent<String> {
        switch raw["type"] as! String {
        case "signIn": return .signIn(name: raw["name"] as! String, settings: raw["settings"] as! String, version: raw["version"] as! Int)
        case "create": return .create(name: raw["name"] as! String, version: raw["version"] as! Int)
        case "change": return .change(raw["settings"] as! String)
        case "pushed": return .pushed(version: raw["version"] as! Int)
        case "pushFailed": return .pushFailed
        case "remote": return .remote(settings: raw["settings"] as! String, version: raw["version"] as! Int)
        case "signOut": return .signOut
        case "unauthorized": return .unauthorized
        case let other: fatalError("unknown event \(other)")
        }
    }

    func testGoldenSyncCases() throws {
        let cases = try Fixture.json("profile_cases")["sync"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 7)
        var steps = 0
        for item in cases {
            let name = item["name"] as! String
            var current = state(item["start"] as! [String: Any])
            for (index, step) in (item["steps"] as! [[String: Any]]).enumerated() {
                let result = ProfileSync.reduce(current, event(step["event"] as! [String: Any]))
                XCTAssertEqual(result.state, state(step["state"] as! [String: Any]), "\(name) step \(index)")
                XCTAssertEqual(result.message, (step["message"] as? String).map { _ in .signedOut }, "\(name) step \(index)")
                current = result.state
                steps += 1
            }
        }
        XCTAssertEqual(steps, 17)
    }
}

final class ProfileClientTests: XCTestCase {
    let config = GatewayConfig(baseURL: URL(string: "http://pi:8098")!, key: "k")

    private func json(_ object: Any) -> Data { try! JSONSerialization.data(withJSONObject: object) }

    private func body(_ request: URLRequest) -> [String: Any] {
        (try? JSONSerialization.jsonObject(with: request.httpBody ?? Data())) as? [String: Any] ?? [:]
    }

    func testLoginSendsNameAndPasswordAndParsesTheSession() async throws {
        let transport = FakeTransport()
        transport.on("/dvb/profiles/login", HTTPResponse(status: 200, body: json(
            ["token": "t1", "name": "Jana", "version": 3, "settings": ["v": 1, "thumbnails": false, "x": [1]]])))
        let session = try await DVBClient(config: config, transport: transport).login(name: "Jana", password: "1234")
        XCTAssertEqual(session.token, "t1")
        XCTAssertEqual(session.name, "Jana")
        XCTAssertEqual(session.version, 3)
        XCTAssertFalse(session.settings.thumbnails)
        XCTAssertEqual(session.settings.fields["x"], .array([.int(1)]))
        let request = try XCTUnwrap(transport.requests.first)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer k")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertNil(request.value(forHTTPHeaderField: ProfileParser.tokenHeader))
        XCTAssertEqual(body(request) as NSDictionary, ["name": "Jana", "password": "1234"] as NSDictionary)
    }

    func testCreateSendsTheSettings() async throws {
        let transport = FakeTransport()
        transport.on("/dvb/profiles", HTTPResponse(status: 201, body: json(["token": "t", "name": "Petr", "version": 1, "settings": [:]])))
        let settings = ProfileDocument(channelPrefs: ChannelPrefs(hidden: ["ct:CH_4"]), thumbnails: true)
        _ = try await DVBClient(config: config, transport: transport).createProfile(name: "Petr", password: "heslo", settings: settings)
        let sent = body(try XCTUnwrap(transport.requests.first))
        XCTAssertEqual(transport.requests.first?.url?.path, "/dvb/profiles")
        XCTAssertEqual(ProfileDocument(json: sent["settings"]!), settings)
    }

    func testTokenCallsUseTheHeader() async throws {
        let transport = FakeTransport()
        // last matching route wins: the general one first
        transport.on("/dvb/profile", HTTPResponse(status: 200, body: json(["name": "Jana", "version": 7, "settings": ["v": 1]])))
        transport.on("/dvb/profile{", HTTPResponse(status: 200, body: json(["version": 8, "updated": "2026-10-24T10:00:00Z"])))
        transport.on("/dvb/profile/logout", HTTPResponse(status: 204, body: Data()))
        let client = DVBClient(config: config, transport: transport)
        let snapshot = try await client.profile(token: "tok")
        XCTAssertEqual(snapshot.version, 7)
        XCTAssertEqual(snapshot.settings.channelPrefs, ChannelPrefs())
        let version = try await client.saveProfile(token: "tok", settings: ProfileDocument())
        XCTAssertEqual(version, 8)
        try await client.logout(token: "tok")
        XCTAssertEqual(transport.requests.map(\.httpMethod), ["GET", "PUT", "POST"])
        XCTAssertEqual(transport.requests.map { $0.value(forHTTPHeaderField: ProfileParser.tokenHeader) }, ["tok", "tok", "tok"])
        XCTAssertNotNil(body(transport.requests[1])["settings"] as? [String: Any])
    }

    func testGatewayErrorTextsAndTheSignedOutRule() async throws {
        let transport = FakeTransport()
        transport.on("/dvb/profile", HTTPResponse(status: 401, body: json(["error": "Profil byl odhlášen", "code": "profileSignedOut"])))
        transport.on("/dvb/profiles", HTTPResponse(status: 409, body: json(["error": "Profil s tímto jménem už existuje"])))
        transport.on("/dvb/profiles/login", HTTPResponse(status: 401, body: json(["error": "Nesprávné jméno nebo heslo"])))
        let client = DVBClient(config: config, transport: transport)
        do {
            _ = try await client.login(name: "Jana", password: "xxxx")
            XCTFail("expected an error")
        } catch let error as CTError {
            XCTAssertEqual(error.userMessage, "Nesprávné jméno nebo heslo")
            XCTAssertNotEqual(error.code, ProfileParser.signedOutCode, "a wrong password is not a lost session")
        }
        do {
            _ = try await client.createProfile(name: "Jana", password: "1234", settings: ProfileDocument())
            XCTFail("expected an error")
        } catch let error as CTError {
            XCTAssertEqual(error.userMessage, "Profil s tímto jménem už existuje")
        }
        do {
            _ = try await client.profile(token: "old")
            XCTFail("expected an error")
        } catch let error as CTError {
            XCTAssertEqual(error.code, ProfileParser.signedOutCode)
        }
        // only the code signs out: the app key's 401 (with its challenge) is a key problem, not a lost session
        transport.on("/dvb/profile", HTTPResponse(status: 401, body: json(["error": "Chybí nebo nesedí klíč"]),
                                                  headers: ["www-authenticate": "Bearer realm=\"cztvhub\""]))
        do {
            _ = try await client.profile(token: "old")
            XCTFail("expected an error")
        } catch let error as CTError {
            XCTAssertEqual(error.userMessage, "Chybí nebo nesedí klíč")
            XCTAssertNotEqual(error.code, ProfileParser.signedOutCode)
        }
    }

    func testA401WithoutTheCodeKeepsTheProfile() async {
        let transport = FakeTransport()
        transport.on("/dvb/profile", HTTPResponse(status: 401, body: json(["error": "Profil byl odhlášen"])))
        do {
            _ = try await DVBClient(config: config, transport: transport).saveProfile(token: "t", settings: ProfileDocument())
            XCTFail("expected an error")
        } catch let error as CTError {
            XCTAssertEqual(error.userMessage, "Profil byl odhlášen")
            XCTAssertNotEqual(error.code, ProfileParser.signedOutCode, "no code, no sign-out")
        } catch {
            XCTFail("unexpected \(error)")
        }
    }

    func testMalformedSessionIsAnError() async {
        let transport = FakeTransport()
        transport.on("/dvb/profiles/login", HTTPResponse(status: 200, body: json(["name": "Jana", "version": 1])))
        do {
            _ = try await DVBClient(config: config, transport: transport).login(name: "Jana", password: "1234")
            XCTFail("expected an error")
        } catch {
            XCTAssertEqual((error as? CTError)?.userMessage, "Brána Anténa neodpovídá")
        }
    }
}

// MARK: - controller

@MainActor
final class MemoryCredentials: ProfileCredentialStore {
    var value: ProfileCredentials?
    init(_ value: ProfileCredentials? = nil) { self.value = value }
    func load() -> ProfileCredentials? { value }
    func save(_ credentials: ProfileCredentials) { value = credentials }
    func delete() { value = nil }
}

/// A gateway with one profile, in memory.
final class FakeProfileAPI: ProfileAPI, @unchecked Sendable {
    private let lock = NSLock()
    private var _settings = ProfileDocument(channelPrefs: ChannelPrefs(hidden: ["ct:CH_5"]), thumbnails: false)
    private var _version = 3
    private var _calls: [String] = []
    private var _failSaves = false
    private var _revoked = false

    var calls: [String] { lock.withLock { _calls } }
    var settings: ProfileDocument {
        get { lock.withLock { _settings } }
        set { lock.withLock { _settings = newValue; _version += 1 } }
    }
    var version: Int { lock.withLock { _version } }
    var failSaves: Bool {
        get { lock.withLock { _failSaves } }
        set { lock.withLock { _failSaves = newValue } }
    }
    var revoked: Bool {
        get { lock.withLock { _revoked } }
        set { lock.withLock { _revoked = newValue } }
    }

    private func record(_ call: String) { lock.withLock { _calls.append(call) } }

    private func checkToken(_ token: String) throws {
        if revoked || token != "tok" { throw CTError("Profil byl odhlášen", code: ProfileParser.signedOutCode) }
    }

    func createProfile(name: String, password: String, settings: ProfileDocument) async throws -> ProfileSession {
        record("create")
        lock.withLock { _settings = settings; _version = 1 }
        return ProfileSession(token: "tok", name: name, settings: settings, version: 1)
    }

    func login(name: String, password: String) async throws -> ProfileSession {
        record("login")
        guard password == "1234" else { throw CTError("Nesprávné jméno nebo heslo", code: "HTTP 401") }
        return lock.withLock { ProfileSession(token: "tok", name: "Jana", settings: _settings, version: _version) }
    }

    func profile(token: String) async throws -> ProfileSnapshot {
        record("get")
        try checkToken(token)
        return lock.withLock { ProfileSnapshot(name: "Jana", settings: _settings, version: _version) }
    }

    func saveProfile(token: String, settings: ProfileDocument) async throws -> Int {
        record("put")
        try checkToken(token)
        if failSaves { throw CTError("Bránu Anténa se nepodařilo kontaktovat") }
        self.settings = settings
        return version
    }

    func logout(token: String) async throws {
        record("logout")
    }
}

@MainActor
final class ProfileSyncControllerTests: XCTestCase {
    let own = ProfileDocument(channelPrefs: ChannelPrefs(order: ["ct:CH_2"]), thumbnails: true)

    private func make(backend: MemoryBackend = MemoryBackend(), credentials: MemoryCredentials = MemoryCredentials(),
                      api: FakeProfileAPI, current: ProfileDocument? = nil)
        -> (ProfileSyncController, applied: () -> [ProfileDocument], messages: () -> [ProfileSyncMessage]) {
        let controller = ProfileSyncController(current: current ?? own, credentials: credentials, backend: backend,
                                               debounce: .milliseconds(30))
        var applied: [ProfileDocument] = []
        var messages: [ProfileSyncMessage] = []
        controller.apply = { applied.append($0) }
        controller.onMessage = { messages.append($0) }
        controller.setAPI(api)
        return (controller, { applied }, { messages })
    }

    /// Waits until `done` holds (at most 5 s), then a little longer so a wrong extra push would show up. A fixed
    /// 150 ms wait failed on a loaded CI runner (the 30 ms debounce had not fired yet).
    private func settle(until done: () -> Bool) async {
        let deadline = ContinuousClock.now + .seconds(5)
        while !done(), ContinuousClock.now < deadline {
            try? await Task.sleep(for: .milliseconds(10))
        }
        try? await Task.sleep(for: .milliseconds(100))
    }

    func testSignInChangeDebouncedPushAndSignOut() async throws {
        let api = FakeProfileAPI()
        let credentials = MemoryCredentials()
        let (controller, applied, _) = make(credentials: credentials, api: api)
        try await controller.signIn(name: " Jana ", password: "1234")
        XCTAssertEqual(credentials.value, ProfileCredentials(name: "Jana", token: "tok"))
        XCTAssertEqual(applied().last?.channelPrefs.hidden, ["ct:CH_5"], "the profile's settings are applied")
        XCTAssertEqual(controller.state.local, own)

        controller.settingsChanged(channelPrefs: ChannelPrefs(hidden: ["ct:CH_5", "ct:CH_6"]), thumbnails: false)
        controller.settingsChanged(channelPrefs: ChannelPrefs(hidden: ["ct:CH_6"]), thumbnails: false)
        XCTAssertTrue(controller.state.pending)
        await settle { !controller.state.pending }
        XCTAssertEqual(api.calls.filter { $0 == "put" }.count, 1, "two quick changes are one push")
        XCTAssertEqual(api.settings.channelPrefs.hidden, ["ct:CH_6"])
        XCTAssertFalse(controller.state.pending)
        XCTAssertEqual(controller.state.version, api.version)

        await controller.signOut()
        XCTAssertEqual(api.calls.last, "logout")
        XCTAssertNil(credentials.value)
        XCTAssertEqual(applied().last, own, "the TV's own settings are back")
    }

    func testWrongPasswordKeepsTheTVSignedOut() async {
        let api = FakeProfileAPI()
        let (controller, applied, _) = make(api: api)
        do {
            try await controller.signIn(name: "Jana", password: "0000")
            XCTFail("expected an error")
        } catch {
            XCTAssertEqual((error as? CTError)?.userMessage, "Nesprávné jméno nebo heslo")
        }
        XCTAssertFalse(controller.isSignedIn)
        XCTAssertTrue(applied().isEmpty)
    }

    func testCreateValidatesLocallyAndSendsTheCurrentSettings() async throws {
        let api = FakeProfileAPI()
        let (controller, _, _) = make(api: api)
        do {
            try await controller.create(name: "Petr", password: "1234", repeated: "1235")
            XCTFail("expected an error")
        } catch {
            XCTAssertEqual((error as? CTError)?.userMessage, "Hesla se neshodují")
        }
        XCTAssertTrue(api.calls.isEmpty, "nothing sent for a local error")
        try await controller.create(name: "Petr", password: "1234", repeated: "1234")
        XCTAssertEqual(api.settings, own)
        XCTAssertEqual(controller.state.local, own)
        XCTAssertEqual(controller.profileName, "Petr")
    }

    func testFailedPushStaysPendingAndIsRetried() async throws {
        let api = FakeProfileAPI()
        let (controller, _, _) = make(api: api)
        try await controller.signIn(name: "Jana", password: "1234")
        api.failSaves = true
        controller.settingsChanged(channelPrefs: ChannelPrefs(), thumbnails: true)
        await settle { controller.pushFailed }
        XCTAssertTrue(controller.state.pending)
        XCTAssertTrue(controller.pushFailed)
        api.settings = ProfileDocument(channelPrefs: ChannelPrefs(hidden: ["remote"]), thumbnails: true)  // another TV
        api.failSaves = false
        await controller.refresh()
        XCTAssertEqual(api.calls.suffix(1), ["put"], "pending: the TV sends first, no read")
        XCTAssertFalse(controller.state.pending)
        XCTAssertFalse(controller.pushFailed)
        XCTAssertEqual(api.settings.channelPrefs, ChannelPrefs(), "last write wins")
    }

    func testRemoteVersionIsAppliedOnRefresh() async throws {
        let api = FakeProfileAPI()
        let (controller, applied, _) = make(api: api)
        try await controller.signIn(name: "Jana", password: "1234")
        let count = applied().count
        await controller.refresh()
        XCTAssertEqual(applied().count, count, "the same version is not applied again")
        api.settings = ProfileDocument(channelPrefs: ChannelPrefs(hidden: ["dvb:x"]), thumbnails: true)
        await controller.refresh()
        XCTAssertEqual(applied().last?.channelPrefs.hidden, ["dvb:x"])
        XCTAssertEqual(controller.state.version, api.version)
    }

    func testUnauthorizedSignsOutOnceWithTheMessage() async throws {
        let api = FakeProfileAPI()
        let credentials = MemoryCredentials()
        let (controller, applied, messages) = make(credentials: credentials, api: api)
        try await controller.signIn(name: "Jana", password: "1234")
        api.revoked = true
        await controller.refresh()
        XCTAssertFalse(controller.isSignedIn)
        XCTAssertNil(credentials.value)
        XCTAssertEqual(applied().last, own)
        XCTAssertEqual(messages(), [.signedOut])
        await controller.refresh()
        XCTAssertEqual(messages(), [.signedOut], "said once")
    }

    func testRemovingTheGatewaySignsOutLocally() async throws {
        let api = FakeProfileAPI()
        let (controller, applied, _) = make(api: api)
        try await controller.signIn(name: "Jana", password: "1234")
        controller.setAPI(nil)
        XCTAssertFalse(controller.isSignedIn)
        XCTAssertFalse(controller.hasGateway)
        XCTAssertEqual(applied().last, own)
        do {
            try await controller.signIn(name: "Jana", password: "1234")
            XCTFail("expected an error")
        } catch {
            XCTAssertEqual((error as? CTError)?.userMessage, ProfileRules.needsGateway)
        }
    }

    func testSignedInStateSurvivesARestartWithoutThePassword() async throws {
        let api = FakeProfileAPI()
        let backend = MemoryBackend()
        let credentials = MemoryCredentials()
        let (first, _, _) = make(backend: backend, credentials: credentials, api: api)
        try await first.signIn(name: "Jana", password: "1234")
        let stored = String(decoding: backend.data(forKey: ProfileSyncController.storageKey)!, as: UTF8.self)
        XCTAssertFalse(stored.contains("1234"), "no password stored")
        XCTAssertFalse(stored.contains("tok"), "the token is in the Keychain only")

        let (second, _, _) = make(backend: backend, credentials: credentials, api: api, current: first.state.active)
        XCTAssertEqual(second.profileName, "Jana")
        XCTAssertEqual(second.state.local, own)
        XCTAssertEqual(second.state.version, first.state.version)

        // the Keychain lost the session: signed out, the TV's own settings
        let (third, _, _) = make(backend: backend, credentials: MemoryCredentials(), api: api, current: first.state.active)
        XCTAssertFalse(third.isSignedIn)
        XCTAssertEqual(third.state.active, own)
    }
}

@MainActor
final class ChannelPrefsStoreProfileTests: XCTestCase {
    func testViewerChangesAreReportedProfileAppliesAreNot() {
        let backend = MemoryBackend()
        let store = ChannelPrefsStore(backend: backend)
        var reported: [ChannelPrefs] = []
        store.onChange = { reported.append($0) }
        store.update { $0.toggleHidden("ct:CH_1") }
        XCTAssertEqual(reported, [ChannelPrefs(hidden: ["ct:CH_1"])])
        store.update { $0.setHidden("ct:CH_1", true) }
        XCTAssertEqual(reported.count, 1, "no change, nothing reported")
        store.replace(ChannelPrefs(order: ["dvb:a"]))
        XCTAssertEqual(store.prefs, ChannelPrefs(order: ["dvb:a"]))
        XCTAssertEqual(reported.count, 1, "a profile apply is not a viewer change")
        XCTAssertEqual(ChannelPrefsStore(backend: backend).prefs, ChannelPrefs(order: ["dvb:a"]), "stored")
    }
}
