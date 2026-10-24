import Foundation
import XCTest
@testable import CTCore

final class HouseholdDefaultReducerTests: XCTestCase {
    private func state(_ raw: [String: Any]) -> HouseholdDefaultState<String> {
        HouseholdDefaultState(own: raw["own"] as! Bool, local: raw["local"] as! String,
                              householdDefault: raw["default"] as? String, version: raw["version"] as! Int)
    }

    private func event(_ raw: [String: Any]) -> HouseholdDefaultEvent<String> {
        switch raw["type"] as! String {
        case "defaults": return .defaults(raw["settings"] as? String, version: raw["version"] as! Int)
        case "change": return .change(raw["settings"] as! String)
        case "reset": return .reset
        case let other: fatalError("unknown event \(other)")
        }
    }

    func testGoldenMigration() throws {
        let cases = try Fixture.json("household_default_cases")["migrate"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 5)
        for item in cases {
            let stored = try JSONDecoder().decode(ChannelPrefs.self, from: JSONSerialization.data(withJSONObject: item["stored"]!))
            XCTAssertEqual(HouseholdDefault.migratedOwn(stored), item["own"] as! Bool, item["name"] as! String)
        }
    }

    func testGoldenSequences() throws {
        let cases = try Fixture.json("household_default_cases")["sequences"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 3)
        var steps = 0
        for item in cases {
            let name = item["name"] as! String
            var current = state(item["start"] as! [String: Any])
            for (index, step) in (item["steps"] as! [[String: Any]]).enumerated() {
                current = HouseholdDefault.reduce(current, event(step["event"] as! [String: Any]), empty: "empty")
                XCTAssertEqual(current, state(step["state"] as! [String: Any]), "\(name) step \(index)")
                XCTAssertEqual(current.effective(builtin: "builtin"), step["effective"] as! String, "\(name) step \(index)")
                steps += 1
            }
        }
        XCTAssertEqual(steps, 9)
    }
}

final class HouseholdDefaultsClientTests: XCTestCase {
    let config = GatewayConfig(baseURL: URL(string: "http://pi:8098")!, key: "k")

    private func json(_ object: Any) -> Data { try! JSONSerialization.data(withJSONObject: object) }

    func testFetchesWithTheAppKeyAndParses() async throws {
        let transport = FakeTransport()
        transport.on("/dvb/defaults", HTTPResponse(status: 200, body: json(
            ["channelPrefs": ["order": ["ct:CH_24", 7], "hidden": ["ct:CH_5"], "extra": true], "version": 4,
             "updated": "2026-10-24T10:00:00Z"])))
        let defaults = try await DVBClient(config: config, transport: transport).householdDefaults()
        XCTAssertEqual(defaults, HouseholdDefaults(channelPrefs: ChannelPrefs(order: ["ct:CH_24"], hidden: ["ct:CH_5"]), version: 4))
        let request = try XCTUnwrap(transport.requests.first)
        XCTAssertEqual(request.httpMethod, "GET")
        XCTAssertEqual(request.url?.path, "/dvb/defaults")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer k")
    }

    func testNullIsNoDefault() throws {
        let defaults = try HouseholdDefaultsParser.parse(status: 200, body: json(["channelPrefs": NSNull(), "version": 0, "updated": NSNull()]))
        XCTAssertEqual(defaults, HouseholdDefaults(channelPrefs: nil, version: 0))
    }

    func testUnknownShapesAreErrorsNotGuesses() {
        let bodies: [[String: Any]] = [
            ["channelPrefs": NSNull()],                       // no version
            ["channelPrefs": NSNull(), "version": true],     // a bool is not a version
            ["channelPrefs": NSNull(), "version": 1.5],
            ["channelPrefs": NSNull(), "version": -1],
            ["version": 2],                                   // channelPrefs missing
            ["channelPrefs": ["ct:CH_1"], "version": 2],      // not an object
        ]
        for body in bodies {
            XCTAssertThrowsError(try HouseholdDefaultsParser.parse(status: 200, body: json(body)), "\(body)")
        }
        XCTAssertThrowsError(try HouseholdDefaultsParser.parse(status: 200, body: Data("[]".utf8)))
    }

    func testGatewayErrorKeepsItsText() {
        XCTAssertThrowsError(try HouseholdDefaultsParser.parse(status: 401, body: json(["error": "Chybí nebo nesedí klíč"]))) {
            XCTAssertEqual(($0 as? CTError)?.userMessage, "Chybí nebo nesedí klíč")
        }
    }
}

/// A gateway's `GET /dvb/defaults`, in memory.
final class FakeDefaultsAPI: HouseholdDefaultsAPI, @unchecked Sendable {
    private let lock = NSLock()
    private var _answer: Result<HouseholdDefaults, Error>

    init(_ answer: HouseholdDefaults) { _answer = .success(answer) }

    var answer: Result<HouseholdDefaults, Error> {
        get { lock.withLock { _answer } }
        set { lock.withLock { _answer = newValue } }
    }

    func householdDefaults() async throws -> HouseholdDefaults { try answer.get() }
}

@MainActor
final class HouseholdDefaultControllerTests: XCTestCase {
    let d1 = ChannelPrefs(order: ["ct:CH_24", "ct:CH_1"], hidden: ["ct:CH_5"])
    let d2 = ChannelPrefs(hidden: ["ct:CH_6"], sourcesOff: ["psp"])

    /// The app's objects, wired by `connect` as AppModel does.
    @MainActor
    private final class TV {
        static let gateway = URL(string: "http://pi:8098")!

        let backend: MemoryBackend
        let prefs: ChannelPrefsStore
        let profile: ProfileSyncController
        let household: HouseholdDefaultController
        var thumbnails = true

        init(backend: MemoryBackend = MemoryBackend(), credentials: MemoryCredentials = MemoryCredentials(),
             profileAPI: FakeProfileAPI? = nil, defaultsAPI: FakeDefaultsAPI? = nil) {
            self.backend = backend
            prefs = ChannelPrefsStore(backend: backend)
            let thumbnails = true
            profile = ProfileSyncController(current: ProfileDocument(channelPrefs: prefs.prefs, thumbnails: thumbnails),
                                            credentials: credentials, backend: backend, debounce: .milliseconds(30))
            household = HouseholdDefaultController(
                prefs: prefs,
                tvOwn: profile.isSignedIn ? profile.state.local?.channelPrefs ?? ChannelPrefs() : prefs.prefs,
                backend: backend
            )
            household.connect(profile: profile, thumbnails: { [unowned self] in self.thumbnails },
                              applyThumbnails: { [unowned self] in self.thumbnails = $0 })
            profile.setAPI(profileAPI)
            household.setAPI(defaultsAPI, gateway: defaultsAPI.map { _ in Self.gateway })
            profile.apply(profile.state.active)
        }
    }

    func testFreshTVFollowsTheDefaultAndANewerOne() async {
        let api = FakeDefaultsAPI(HouseholdDefaults(channelPrefs: d1, version: 1))
        let tv = TV(defaultsAPI: api)
        XCTAssertFalse(tv.household.own)
        await tv.household.refresh()
        XCTAssertEqual(tv.prefs.prefs, d1)
        XCTAssertEqual(tv.profile.state.active.channelPrefs, d1, "the profile's current settings follow it")
        api.answer = .success(HouseholdDefaults(channelPrefs: d2, version: 2))
        await tv.household.refresh()
        XCTAssertEqual(tv.prefs.prefs, d2)
        api.answer = .success(HouseholdDefaults(channelPrefs: d1, version: 1))
        await tv.household.refresh()
        XCTAssertEqual(tv.prefs.prefs, d2, "an older version does not replace it")
        api.answer = .success(HouseholdDefaults(channelPrefs: nil, version: 3))
        await tv.household.refresh()
        XCTAssertEqual(tv.prefs.prefs, ChannelPrefs(), "cleared on /admin: the built-in order")
    }

    func testFirstChangeMakesTheListOwnFromWhatItShowed() async {
        let api = FakeDefaultsAPI(HouseholdDefaults(channelPrefs: d1, version: 1))
        let tv = TV(defaultsAPI: api)
        await tv.household.refresh()
        XCTAssertFalse(tv.household.canReset, "already following the default")
        tv.prefs.update { $0.toggleHidden("ct:CH_2") }
        XCTAssertTrue(tv.household.own)
        XCTAssertEqual(tv.prefs.prefs, ChannelPrefs(order: d1.order, hidden: ["ct:CH_5", "ct:CH_2"]), "starts from the default")
        XCTAssertTrue(tv.household.canReset)

        api.answer = .success(HouseholdDefaults(channelPrefs: d2, version: 2))
        await tv.household.refresh()
        XCTAssertEqual(tv.prefs.prefs.hidden, ["ct:CH_5", "ct:CH_2"], "the default no longer applies")
        XCTAssertEqual(tv.household.cached?.version, 2, "but it is cached")

        tv.household.reset()
        XCTAssertFalse(tv.household.own)
        XCTAssertEqual(tv.prefs.prefs, d2, "Obnovit výchozí follows the default again")
        XCTAssertEqual(tv.profile.state.active.channelPrefs, d2)
    }

    func testResetWithoutDefaultIsTheBuiltInOrder() {
        let tv = TV()
        tv.prefs.update { $0.setOn(.ct, false) }
        XCTAssertTrue(tv.household.own)
        tv.household.reset()
        XCTAssertEqual(tv.prefs.prefs, ChannelPrefs())
        XCTAssertFalse(tv.household.own)
    }

    func testStateSurvivesARestartAndOfflineStartsUseTheCache() async {
        let backend = MemoryBackend()
        let api = FakeDefaultsAPI(HouseholdDefaults(channelPrefs: d1, version: 5))
        do {
            let tv = TV(backend: backend, defaultsAPI: api)
            await tv.household.refresh()
        }
        api.answer = .failure(URLError(.notConnectedToInternet))
        let again = TV(backend: backend, defaultsAPI: api)
        await again.household.refresh()
        XCTAssertFalse(again.household.own, "a list that came from the default is not migrated into own")
        XCTAssertEqual(again.household.cached, HouseholdDefaults(channelPrefs: d1, version: 5))
        XCTAssertEqual(again.prefs.prefs, d1)

        again.prefs.update { $0.toggleHidden("ct:CH_1") }
        let third = TV(backend: backend)
        XCTAssertTrue(third.household.own)
        XCTAssertEqual(third.prefs.prefs.hidden, ["ct:CH_5", "ct:CH_1"])
    }

    func testPrefsFromBeforeT11AreOwn() async {
        let backend = MemoryBackend()
        backend.set(try! JSONEncoder().encode(ChannelPrefs(order: ["ct:CH_24"])), forKey: ChannelPrefsStore.storageKey)
        let tv = TV(backend: backend, defaultsAPI: FakeDefaultsAPI(HouseholdDefaults(channelPrefs: d1, version: 1)))
        await tv.household.refresh()
        XCTAssertTrue(tv.household.own)
        XCTAssertEqual(tv.prefs.prefs, ChannelPrefs(order: ["ct:CH_24"]))

        let fresh = MemoryBackend()
        fresh.set(try! JSONEncoder().encode(ChannelPrefs()), forKey: ChannelPrefsStore.storageKey)
        XCTAssertFalse(TV(backend: fresh).household.own, "empty stored prefs follow the default")
    }

    func testSignedInTheProfileAppliesAndSigningOutFollowsTheNewDefault() async throws {
        let profileAPI = FakeProfileAPI()  // its profile hides ct:CH_5, thumbnails off
        let api = FakeDefaultsAPI(HouseholdDefaults(channelPrefs: d1, version: 1))
        let tv = TV(profileAPI: profileAPI, defaultsAPI: api)
        await tv.household.refresh()
        try await tv.profile.signIn(name: "Jana", password: "1234")
        XCTAssertEqual(tv.prefs.prefs, ChannelPrefs(hidden: ["ct:CH_5"]))
        XCTAssertFalse(tv.thumbnails)

        api.answer = .success(HouseholdDefaults(channelPrefs: d2, version: 2))
        await tv.household.refresh()
        XCTAssertEqual(tv.prefs.prefs, ChannelPrefs(hidden: ["ct:CH_5"]), "signed in, the default does not apply")
        tv.prefs.update { $0.toggleHidden("ct:CH_1") }
        XCTAssertFalse(tv.household.own, "a change in the profile does not make the TV's list its own")
        XCTAssertTrue(tv.household.canReset)

        await tv.profile.signOut()
        XCTAssertEqual(tv.prefs.prefs, d2, "back to following the default, the newer one")
        XCTAssertTrue(tv.thumbnails, "the TV's own switch is back")
        XCTAssertEqual(tv.profile.state.active, ProfileDocument(channelPrefs: d2, thumbnails: true))
    }

    func testSigningOutReturnsToTheOwnList() async throws {
        let api = FakeDefaultsAPI(HouseholdDefaults(channelPrefs: d1, version: 1))
        let tv = TV(profileAPI: FakeProfileAPI(), defaultsAPI: api)
        tv.prefs.update { $0.move("ct:CH_2", by: -1, in: ["ct:CH_1", "ct:CH_2"]) }
        let own = tv.prefs.prefs
        try await tv.profile.signIn(name: "Jana", password: "1234")
        await tv.household.refresh()
        await tv.profile.signOut()
        XCTAssertTrue(tv.household.own)
        XCTAssertEqual(tv.prefs.prefs, own)
    }

    func testANewProfileStartsWithWhatTheTVShows() async throws {
        let profileAPI = FakeProfileAPI()
        let tv = TV(profileAPI: profileAPI, defaultsAPI: FakeDefaultsAPI(HouseholdDefaults(channelPrefs: d1, version: 1)))
        await tv.household.refresh()
        try await tv.profile.create(name: "Petr", password: "1234", repeated: "1234")
        XCTAssertEqual(profileAPI.settings.channelPrefs, d1)
        XCTAssertEqual(tv.prefs.prefs, d1)
    }

    func testResetSignedInCopiesTheDefaultIntoTheProfile() async throws {
        let profileAPI = FakeProfileAPI()
        let api = FakeDefaultsAPI(HouseholdDefaults(channelPrefs: d1, version: 1))
        let tv = TV(profileAPI: profileAPI, defaultsAPI: api)
        await tv.household.refresh()
        tv.prefs.update { $0.toggleHidden("ct:CH_1") }
        let own = tv.prefs.prefs
        try await tv.profile.signIn(name: "Jana", password: "1234")
        XCTAssertTrue(tv.household.canReset)
        tv.household.reset()
        XCTAssertEqual(tv.prefs.prefs, d1)
        XCTAssertTrue(tv.profile.state.pending, "sent to the profile")
        XCTAssertFalse(tv.household.canReset, "the profile's list is the default now")
        XCTAssertTrue(tv.household.own, "the TV's own state is untouched")

        api.answer = .success(HouseholdDefaults(channelPrefs: nil, version: 2))
        await tv.household.refresh()
        tv.household.reset()
        XCTAssertEqual(tv.prefs.prefs, ChannelPrefs(), "no default: the built-in order")
        await tv.profile.signOut()
        XCTAssertEqual(tv.prefs.prefs, own)
    }

    func testRemovedOrChangedGatewayDropsTheCache() async {
        let backend = MemoryBackend()
        let api = FakeDefaultsAPI(HouseholdDefaults(channelPrefs: d1, version: 7))
        let tv = TV(backend: backend, defaultsAPI: api)
        await tv.household.refresh()
        tv.household.setAPI(api, gateway: TV.gateway)
        XCTAssertEqual(tv.prefs.prefs, d1, "the same gateway keeps it")

        tv.household.setAPI(api, gateway: URL(string: "http://other:8098")!)
        XCTAssertNil(tv.household.cached)
        XCTAssertEqual(tv.prefs.prefs, ChannelPrefs(), "the built-in order until the new gateway answers")
        api.answer = .success(HouseholdDefaults(channelPrefs: d2, version: 1))
        await tv.household.refresh()
        XCTAssertEqual(tv.prefs.prefs, d2, "its versions count from its own start")

        tv.household.setAPI(nil, gateway: nil)
        XCTAssertNil(tv.household.cached, "Odebrat bránu")
        XCTAssertEqual(tv.prefs.prefs, ChannelPrefs())
        XCTAssertNil(TV(backend: backend).household.cached, "persisted")
    }

    func testTheThumbnailsSwitchIsNotPartOfTheDefault() async {
        let api = FakeDefaultsAPI(HouseholdDefaults(channelPrefs: d1, version: 1))
        let tv = TV(defaultsAPI: api)
        tv.thumbnails = false
        await tv.household.refresh()
        XCTAssertFalse(tv.thumbnails)
        XCTAssertFalse(tv.household.own)
    }
}
