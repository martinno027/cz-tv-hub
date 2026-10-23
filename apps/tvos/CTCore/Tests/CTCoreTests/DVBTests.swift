import Foundation
import XCTest
@testable import CTCore

final class DVBTests: XCTestCase {
    private func dvbFixture(_ name: String) throws -> HTTPResponse {
        try Fixture.response("dvb/\(name)")
    }

    // MARK: config

    func testGatewayAddressNormalisation() throws {
        let key = "dev-key-dev-key-dev-key-dev-key-0000"
        XCTAssertEqual(GatewayConfig(address: "pi.tailnet.ts.net", key: key)?.baseURL.absoluteString, "http://pi.tailnet.ts.net:8098")
        XCTAssertEqual(GatewayConfig(address: " 192.168.1.5:9000 ", key: key)?.baseURL.absoluteString, "http://192.168.1.5:9000")
        XCTAssertEqual(GatewayConfig(address: "http://127.0.0.1:8098/dvb/", key: key)?.baseURL.absoluteString, "http://127.0.0.1:8098")
        XCTAssertEqual(GatewayConfig(address: "https://pi.ts.net", key: key)?.baseURL.absoluteString, "https://pi.ts.net:8098")
        XCTAssertNil(GatewayConfig(address: "", key: key))
        XCTAssertNil(GatewayConfig(address: "pi", key: "  "))
        XCTAssertNil(GatewayConfig(address: "ftp://pi", key: key))
        let config = try XCTUnwrap(GatewayConfig(address: "pi.local", key: key))
        XCTAssertEqual(config.address, "pi.local:8098")
        XCTAssertEqual(config.authorizationHeaders, ["Authorization": "Bearer \(key)"])
        XCTAssertEqual(config.url("/dvb/live/x.m3u8").absoluteString, "http://pi.local:8098/dvb/live/x.m3u8")
    }

    func testConfigRoundTripsThroughJSON() throws {
        let config = try XCTUnwrap(GatewayConfig(address: "pi.local", key: "k"))
        XCTAssertEqual(try JSONDecoder().decode(GatewayConfig.self, from: JSONEncoder().encode(config)), config)
    }

    // MARK: parsing (fixtures captured from the dev gateway)

    func testChannels() throws {
        let response = try dvbFixture("dvb_tvos_channels")
        let channels = try DVBParser.channels(status: response.status, body: response.body)
        XCTAssertEqual(channels.map(\.name), ["ČT1 HD", "ČT2 HD", "Nova HD", "Prima HD"])
        XCTAssertEqual(channels.map(\.number), [1, 2, 3, 4])
        XCTAssertEqual(channels[0].iconPath, "/dvb/icon/\(channels[0].id)")
        XCTAssertNil(channels[1].iconPath)
    }

    func testEPG() throws {
        let response = try dvbFixture("dvb_tvos_epg_today")
        let epg = try DVBParser.epg(status: response.status, body: response.body)
        XCTAssertEqual(epg.count, 4)
        let ct1 = try XCTUnwrap(epg["cb2de4250e92ae9a4c549f7e48885ff8"])
        XCTAssertEqual(ct1, ct1.sorted { $0.start < $1.start })
        let states = Set(ct1.compactMap(\.recording?.state))
        XCTAssertEqual(states, [.completed, .failed, .recording, .scheduled])
        let past = try XCTUnwrap(ct1.first { $0.id.hasPrefix("dvr:") })
        XCTAssertEqual(past.recording?.state, .completed)
        XCTAssertEqual("dvr:" + (past.recording?.id ?? ""), past.id)
        XCTAssertFalse(past.title.isEmpty)
        XCTAssertFalse(past.genre.isEmpty)
        XCTAssertTrue(past.subtitle.hasPrefix("Díl"))
        // Channels without autorec only have plain EPG events.
        XCTAssertTrue(epg["6305cb90c435e2dd0c152ef77fb58987"]!.allSatisfy { $0.recording == nil })
    }

    func testEPGSkipsMalformedAndDuplicateEvents() throws {
        let body = Data("""
        {"date": "2026-10-18", "events": [
          {"id": "1", "channel": "c", "title": "A", "start": "2026-10-18T06:00:00Z", "end": "2026-10-18T07:00:00Z",
           "recording": {"id": "r1", "state": "completedWarning"}},
          {"id": "1", "channel": "c", "title": "A", "start": "2026-10-18T06:00:00Z", "end": "2026-10-18T07:00:00Z"},
          {"id": "2", "channel": "c", "start": "2026-10-18T07:00:00Z"},
          {"channel": "c", "start": "2026-10-18T07:00:00Z", "end": "2026-10-18T08:00:00Z"},
          {"id": "3", "channel": "c", "start": "2026-10-18T05:00:00Z", "end": "2026-10-18T05:30:00Z", "recording": {"state": "completed"}},
          42
        ]}
        """.utf8)
        let events = try DVBParser.epg(status: 200, body: body)["c"]!
        XCTAssertEqual(events.map(\.id), ["3", "1"])
        XCTAssertNil(events[0].recording, "a recording without id is unusable")
        XCTAssertEqual(events[1].recording?.state, .unknown)
    }

    func testStatus() throws {
        let response = try dvbFixture("dvb_tvos_status")
        let status = try DVBParser.status(status: response.status, body: response.body)
        XCTAssertTrue(status.enabled)
        XCTAssertEqual(status.retentionDays, 3)
        XCTAssertEqual(status.tuners.count, 2)
        XCTAssertTrue(status.tvheadendReachable)
    }

    func testStatusThumbnailsFlag() throws {
        let fixture = try dvbFixture("dvb_tvos_status")
        XCTAssertFalse(try DVBParser.status(status: fixture.status, body: fixture.body).thumbnails, "missing = off")
        let on = Data(#"{"enabled": true, "retentionDays": 3, "tuners": [], "tvheadendReachable": true, "thumbnails": true}"#.utf8)
        XCTAssertTrue(try DVBParser.status(status: 200, body: on).thumbnails)
    }

    func testThumbnailOnlyForRecordedEventsWhenBothAllow() {
        func event(_ state: DVBRecording.State?) -> DVBEvent {
            DVBEvent(id: "e", channel: "c", title: "t", start: Date(timeIntervalSince1970: 0),
                     end: Date(timeIntervalSince1970: 60), recording: state.map { DVBRecording(id: "r", state: $0) })
        }
        XCTAssertEqual(AntennaThumbnails.recordingID(for: event(.completed), gatewayAllows: true, tvAllows: true), "r")
        XCTAssertEqual(AntennaThumbnails.recordingID(for: event(.recording), gatewayAllows: true, tvAllows: true), "r")
        for state in [DVBRecording.State.scheduled, .failed, .unknown] {
            XCTAssertNil(AntennaThumbnails.recordingID(for: event(state), gatewayAllows: true, tvAllows: true))
        }
        XCTAssertNil(AntennaThumbnails.recordingID(for: event(nil), gatewayAllows: true, tvAllows: true))
        XCTAssertNil(AntennaThumbnails.recordingID(for: event(.completed), gatewayAllows: false, tvAllows: true))
        XCTAssertNil(AntennaThumbnails.recordingID(for: event(.completed), gatewayAllows: true, tvAllows: false))
    }

    func testErrorsUseGatewayCzechText() throws {
        for (fixture, message) in [("dvb_tvos_unauthorized", "Chybí nebo nesedí klíč"),
                                   ("dvb_tvos_epg_bad_date", "Program je k dispozici jen od 2026-10-15 do 2026-10-20")] {
            let response = try dvbFixture(fixture)
            XCTAssertThrowsError(try DVBParser.epg(status: response.status, body: response.body)) { error in
                XCTAssertEqual((error as? CTError)?.userMessage, message, fixture)
            }
        }
        let busy = Data(#"{"error": "Tuner je obsazený"}"#.utf8)
        XCTAssertEqual(DVBParser.error(status: 503, body: busy).userMessage, "Tuner je obsazený")
        XCTAssertEqual(DVBParser.error(status: 502, body: Data("Bad Gateway".utf8)).userMessage, "Brána Anténa neodpovídá")
        XCTAssertEqual(DVBParser.error(status: 401, body: Data()).userMessage, "Brána odmítla klíč. Zkontrolujte nastavení.")
    }

    // MARK: client

    func testClientSendsBearerKeyAndBuildsURLs() async throws {
        let transport = FakeTransport()
        try transport.on("/dvb/channels", fixture: "dvb/dvb_tvos_channels")
        try transport.on("/dvb/epg?date=2026-10-18", fixture: "dvb/dvb_tvos_epg_today")
        transport.on("/dvb/live/", HTTPResponse(status: 503, body: Data(#"{"error": "Tuner je obsazený"}"#.utf8)))
        let config = GatewayConfig(address: "127.0.0.1", key: "secret")!
        let client = DVBClient(config: config, transport: transport)

        _ = try await client.channels()
        _ = try await client.epg(day: BroadcastDay(year: 2026, month: 10, day: 18))
        let live = client.liveURL(channelID: "abc")
        XCTAssertEqual(live.absoluteString, "http://127.0.0.1:8098/dvb/live/abc.m3u8")
        XCTAssertEqual(client.recordingURL(id: "r1").absoluteString, "http://127.0.0.1:8098/dvb/recording/r1.m3u8")
        do {
            try await client.preparePlaylist(live)
            XCTFail("expected error")
        } catch let error as CTError {
            XCTAssertEqual(error.userMessage, "Tuner je obsazený")
        }
        XCTAssertEqual(transport.requests.count, 3)
        for request in transport.requests {
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer secret")
        }
    }

    func testThumbnailTellsMissingFromFailed() async throws {
        let transport = FakeTransport()
        transport.on("/recording/ok/thumb.jpg", HTTPResponse(status: 200, body: Data([0xFF, 0xD8, 0xFF, 0xE0])))
        transport.on("/recording/gone/thumb.jpg", HTTPResponse(status: 404, body: Data(#"{"error": "x"}"#.utf8)))
        transport.on("/recording/busy/thumb.jpg", HTTPResponse(status: 503, body: Data()))
        transport.on("/recording/html/thumb.jpg", HTTPResponse(status: 200, body: Data("<html>".utf8)))
        let client = DVBClient(config: GatewayConfig(address: "pi", key: "k")!, transport: transport)
        let ok = try await client.thumbnail(recordingID: "ok")
        let gone = try await client.thumbnail(recordingID: "gone")
        let busy = try await client.thumbnail(recordingID: "busy")
        let html = try await client.thumbnail(recordingID: "html")
        XCTAssertEqual(ok, .image(Data([0xFF, 0xD8, 0xFF, 0xE0])))
        XCTAssertEqual(gone, .missing)
        XCTAssertEqual(busy, .failed, "only a 404 is remembered as missing")
        XCTAssertEqual(html, .failed)
    }

    func testNetworkFailureIsCzech() async {
        let transport = FakeTransport()
        transport.fail("", URLError(.cannotConnectToHost))
        let client = DVBClient(config: GatewayConfig(address: "pi", key: "k")!, transport: transport)
        do {
            _ = try await client.status()
            XCTFail("expected error")
        } catch let error as CTError {
            XCTAssertEqual(error.userMessage, "Bránu Anténa se nepodařilo kontaktovat")
        } catch {
            XCTFail("unexpected \(error)")
        }
    }

    func testStoreCachesAndFindsOnAir() async throws {
        let transport = FakeTransport()
        try transport.on("/dvb/epg", fixture: "dvb/dvb_tvos_epg_today")
        try transport.on("/dvb/channels", fixture: "dvb/dvb_tvos_channels")
        let clock = MutableClock(utc("2026-10-18T06:45:00Z"))
        let store = AntennaStore(client: DVBClient(config: GatewayConfig(address: "pi", key: "k")!, transport: transport),
                                 clock: { clock.now })
        _ = try await store.channels()
        _ = try await store.channels()
        let onAir = await store.onAir(at: clock.now)
        XCTAssertEqual(onAir["cb2de4250e92ae9a4c549f7e48885ff8"]?.recording?.state, .recording)
        let epgRequests = transport.requests.filter { $0.url!.path.hasSuffix("/epg") }.count
        _ = try await store.epg(for: BroadcastDay(containing: clock.now))
        XCTAssertEqual(transport.requests.filter { $0.url!.path.hasSuffix("/epg") }.count, epgRequests, "cached")
        clock.now = clock.now.addingTimeInterval(121)
        _ = try await store.epg(for: BroadcastDay(containing: clock.now))
        XCTAssertEqual(transport.requests.filter { $0.url!.path.hasSuffix("/epg") }.count, epgRequests + 1, "today refreshed")
        XCTAssertEqual(transport.requests.filter { $0.url!.path.hasSuffix("/channels") }.count, 1)
    }

    func testStoreRefreshesTheShownBroadcastDayAfterMidnight() async throws {
        let transport = FakeTransport()
        try transport.on("/dvb/epg", fixture: "dvb/dvb_tvos_epg_today")
        let clock = MutableClock(utc("2026-10-18T22:30:00Z"))  // 0:30 on the 19th: the guide shows the 18th
        let store = AntennaStore(client: DVBClient(config: GatewayConfig(address: "pi", key: "k")!, transport: transport),
                                 clock: { clock.now })
        let shown = BroadcastDay(year: 2026, month: 10, day: 18)
        func epgRequests() -> Int { transport.requests.filter { $0.url!.path.hasSuffix("/epg") }.count }
        _ = try await store.epg(for: shown)
        clock.now = clock.now.addingTimeInterval(121)
        _ = try await store.epg(for: shown)
        XCTAssertEqual(epgRequests(), 2, "recording states of the shown day still change")
        clock.now = utc("2026-10-19T03:10:00Z")  // 05:10: a past day now
        _ = try await store.epg(for: shown)
        clock.now = clock.now.addingTimeInterval(3600)
        _ = try await store.epg(for: shown)
        XCTAssertEqual(epgRequests(), 2, "past days stay cached")
    }
}

final class AntennaRulesTests: XCTestCase {
    let now = utc("2026-10-18T12:00:00Z")

    private func event(_ start: String, _ end: String, _ state: DVBRecording.State?) -> DVBEvent {
        DVBEvent(id: "e", channel: "c", title: "T", start: utc(start), end: utc(end),
                 recording: state.map { DVBRecording(id: "rec", state: $0) })
    }

    func testFuture() {
        XCTAssertEqual(AntennaAction.decide(event("2026-10-18T13:00:00Z", "2026-10-18T14:00:00Z", .scheduled), now: now), .future)
        XCTAssertEqual(AntennaAction.decide(event("2026-10-18T13:00:00Z", "2026-10-18T14:00:00Z", nil), now: now), .future)
    }

    func testAiringOffersStartOverOnlyWhileRecording() {
        XCTAssertEqual(AntennaAction.decide(event("2026-10-18T11:30:00Z", "2026-10-18T12:30:00Z", .recording), now: now),
                       .airing(startOverRecordingID: "rec"))
        XCTAssertEqual(AntennaAction.decide(event("2026-10-18T11:30:00Z", "2026-10-18T12:30:00Z", .scheduled), now: now),
                       .airing(startOverRecordingID: nil))
        XCTAssertEqual(AntennaAction.decide(event("2026-10-18T11:30:00Z", "2026-10-18T12:30:00Z", nil), now: now),
                       .airing(startOverRecordingID: nil))
        // Boundary: start == now is airing.
        XCTAssertEqual(AntennaAction.decide(event("2026-10-18T12:00:00Z", "2026-10-18T12:30:00Z", .recording), now: now),
                       .airing(startOverRecordingID: "rec"))
    }

    func testEndedPlaysOnlyCompletedRecordings() {
        XCTAssertEqual(AntennaAction.decide(event("2026-10-18T10:00:00Z", "2026-10-18T12:00:00Z", .completed), now: now),
                       .recording(id: "rec"))
        for state: DVBRecording.State? in [.failed, .scheduled, .recording, .unknown, nil] {
            XCTAssertEqual(AntennaAction.decide(event("2026-10-18T10:00:00Z", "2026-10-18T11:00:00Z", state), now: now),
                           .notRecorded, String(describing: state))
        }
    }

    func testFixtureStatesMapToActions() throws {
        let response = try Fixture.response("dvb/dvb_tvos_epg_today")
        let ct1 = try DVBParser.epg(status: response.status, body: response.body)["cb2de4250e92ae9a4c549f7e48885ff8"]!
        let airing = try XCTUnwrap(ct1.first { $0.recording?.state == .recording })
        let failed = try XCTUnwrap(ct1.first { $0.recording?.state == .failed })
        let completed = try XCTUnwrap(ct1.first { $0.recording?.state == .completed })
        let moment = airing.start.addingTimeInterval(60)
        XCTAssertEqual(AntennaAction.decide(airing, now: moment), .airing(startOverRecordingID: airing.recording!.id))
        XCTAssertEqual(AntennaAction.decide(failed, now: moment), .notRecorded)
        XCTAssertEqual(AntennaAction.decide(completed, now: moment), .recording(id: completed.recording!.id))
    }
}
