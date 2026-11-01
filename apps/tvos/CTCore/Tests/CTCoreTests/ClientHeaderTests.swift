import XCTest
@testable import CTCore

/// `X-Cztvhub-Client` (spec/dvb-gateway.md "Sign-in log (T37)"): only on the profile routes and `GET /dvb/status`.
final class ClientHeaderTests: XCTestCase {
    private let header = "tvos/1.0 (123)"
    private let config = GatewayConfig(baseURL: URL(string: "http://pi.local:8098")!, key: "k")

    func testValueIsPlatformVersionAndBuild() {
        XCTAssertEqual(ClientHeader.value(version: "1.0", build: "123"), "tvos/1.0 (123)")
        XCTAssertEqual(ClientHeader.value(version: "1.0", build: "?"), "tvos/1.0", "the gateway would refuse '?'")
        XCTAssertEqual(ClientHeader.value(version: "", build: ""), "tvos")
        XCTAssertEqual(ClientHeader.value(version: "1.0)", build: "1(2"), "tvos/1.0 (12)")
        let long = ClientHeader.value(version: String(repeating: "9", count: 100), build: "1")
        XCTAssertEqual(long.count, ClientHeader.maxLength)
    }

    func testValueOnlyUsesCharactersTheGatewayAccepts() {
        let gatewayRule = try! NSRegularExpression(pattern: "^[A-Za-z0-9 ./()+_-]{1,64}$")
        for (version, build) in [("1.0", "123"), ("1.0 β", "12·3"), ("ä", "\n"), ("1.0", "?")] {
            let value = ClientHeader.value(version: version, build: build)
            XCTAssertEqual(gatewayRule.numberOfMatches(in: value, range: NSRange(value.startIndex..., in: value)), 1, value)
        }
    }

    func testAppliesOnlyToProfileRoutesAndStatus() {
        let yes: [(String, String)] = [
            ("GET", "/dvb/status"), ("POST", "/dvb/profiles"), ("POST", "/dvb/profiles/login"),
            ("GET", "/dvb/profile"), ("PUT", "/dvb/profile"), ("POST", "/dvb/profile/logout"),
        ]
        let no: [(String, String)] = [
            ("POST", "/dvb/status"), ("GET", "/dvb/statuses"), ("GET", "/dvb/channels"), ("GET", "/dvb/epg"),
            ("GET", "/dvb/live/a.m3u8"), ("GET", "/dvb/recording/r.m3u8"), ("GET", "/dvb/timeline/t.m3u8"),
            ("GET", "/dvb/profilex"), ("GET", "/dvb/defaults"), ("GET", "/dvb/archive"), ("GET", "/x/dvb/status"),
        ]
        for (method, path) in yes { XCTAssertTrue(ClientHeader.applies(method: method, path: path), "\(method) \(path)") }
        for (method, path) in no { XCTAssertFalse(ClientHeader.applies(method: method, path: path), "\(method) \(path)") }
    }

    /// Every gateway call of `DVBClient`: the header is on status and the profile calls, on nothing else.
    func testDVBClientSendsItOnlyOnStatusAndProfileCalls() async throws {
        let transport = FakeTransport()
        transport.on("pi.local", HTTPResponse(status: 200, body: Data("{}".utf8)))
        let client = DVBClient(config: config, transport: transport, clientHeader: header)
        var marked: [String] = []
        var unmarked: [String] = []
        func call(_ name: String, sends: Bool, _ run: () async throws -> Void) async {
            let before = transport.requests.count
            try? await run()
            let sent = transport.requests[before...]
            XCTAssertFalse(sent.isEmpty, "\(name) sent nothing")
            for request in sent {
                let value = request.value(forHTTPHeaderField: ClientHeader.field)
                XCTAssertEqual(value, sends ? header : nil, "\(name): \(request.url!.path)")
                if value != nil { marked.append(name) } else { unmarked.append(name) }
                XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer k", name)
            }
        }
        await call("status", sends: true) { _ = try await client.status() }
        await call("login", sends: true) { _ = try await client.login(name: "a", password: "1234") }
        await call("create", sends: true) { _ = try await client.createProfile(name: "a", password: "1234", settings: ProfileDocument()) }
        await call("profile", sends: true) { _ = try await client.profile(token: "t") }
        await call("save", sends: true) { _ = try await client.saveProfile(token: "t", settings: ProfileDocument()) }
        await call("logout", sends: true) { try await client.logout(token: "t") }

        await call("channels", sends: false) { _ = try await client.channels() }
        await call("epg", sends: false) { _ = try await client.epg(day: BroadcastDay(year: 2026, month: 11, day: 1)) }
        await call("breaks", sends: false) { _ = try await client.adBreaks(recordingID: "r") }
        await call("thumb", sends: false) { _ = try await client.thumbnail(recordingID: "r") }
        await call("live", sends: false) { try await client.preparePlaylist(client.liveURL(channelID: "c")) }
        await call("recording", sends: false) { try await client.preparePlaylist(client.recordingURL(id: "r")) }
        await call("timeline", sends: false) { _ = try await client.timeline(id: "t") }
        await call("defaults", sends: false) { _ = try await client.householdDefaults() }
        await call("details", sends: false) { _ = try await client.details(.dvb(eventID: "e")) }
        await call("detailsImage", sends: false) { _ = try await client.detailsImage(path: "/dvb/details/image/a.jpg") }
        await call("archive", sends: false) { _ = try await client.archiveHome() }
        await call("categories", sends: false) { _ = try await client.archiveCategories() }
        await call("category", sends: false) { _ = try await client.archiveCategory("x", sort: .newest, genre: nil, page: 1) }
        await call("search", sends: false) { _ = try await client.archiveSearch("x", page: 1) }
        await call("show", sends: false) { _ = try await client.archiveShow("s") }
        await call("season", sends: false) { _ = try await client.archiveSeason(show: "s", season: "1", page: 1) }
        await call("episodes", sends: false) { _ = try await client.archiveEpisodes(show: "s", query: ArchiveEpisodeQuery(), page: 1) }
        // The two groups really differ (a header on everything or on nothing fails above, and here).
        XCTAssertEqual(Set(marked), ["status", "login", "create", "profile", "save", "logout"])
        XCTAssertFalse(unmarked.isEmpty)
    }

    func testNoHeaderWithoutAValue() async throws {
        let transport = FakeTransport()
        transport.on("/dvb/status", HTTPResponse(status: 200, body: Data("{}".utf8)))
        _ = try await DVBClient(config: config, transport: transport).status()
        XCTAssertNil(transport.requests.first?.value(forHTTPHeaderField: ClientHeader.field))
    }

    /// The player's headers for gateway HLS are the key only.
    func testPlaybackHeadersDoNotCarryIt() {
        XCTAssertEqual(Array(config.authorizationHeaders.keys), ["Authorization"])
    }

    /// Never to ČT (API) or the Chamber's CDN.
    @MainActor
    func testNeverSentToCTOrTheChamber() async throws {
        let transport = FakeTransport()
        transport.on("ceskatelevize.cz", HTTPResponse(status: 200, body: Data("{}".utf8)))
        transport.on("cdn.cra.cz", HTTPResponse(status: 200, body: Data("#EXTM3U\n".utf8)))
        let ct = CTClient(transport: transport)
        _ = try? await ct.liveStreams(channelID: "CH_1")
        _ = try? await ct.vod(idec: "1")
        _ = try? await ct.schedules(slugs: ["ct1"], day: BroadcastDay(year: 2026, month: 11, day: 1))
        await PSPLiveMonitor(transport: transport).check(PSPStreams.all[0])
        XCTAssertEqual(transport.requests.count, 4)
        for request in transport.requests {
            XCTAssertNil(request.value(forHTTPHeaderField: ClientHeader.field), request.url!.absoluteString)
            XCTAssertFalse(request.url!.host()!.contains("pi.local"))
        }
    }
}
