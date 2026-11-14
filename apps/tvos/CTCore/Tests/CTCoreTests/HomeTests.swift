import XCTest
@testable import CTCore

/// Domů's rows (spec/apps.md "Domů", T58).
final class HomeTests: XCTestCase {
    private func notice(configured: Bool = true, tailnet: String? = nil, failure: HomeRules.StatusFailure? = nil,
                        paused: Bool = false) -> HomeRules.Notice? {
        HomeRules.gatewayNotice(configured: configured, tailnetLine: tailnet, failure: failure, recordingPaused: paused)
    }

    func testGatewayNotice() {
        XCTAssertNil(notice(configured: false, tailnet: TailnetRules.connectingLine, failure: .unreachable, paused: true),
                     "no gateway configured: no notice")
        XCTAssertNil(notice())
        XCTAssertEqual(notice(failure: .unreachable)?.text, "Brána antény není dostupná.")
        XCTAssertEqual(notice(failure: .rejected)?.text,
                       "Brána antény odmítla klíč. Zkontrolujte ho v Nastavení → Server.")
        XCTAssertEqual(notice(paused: true)?.text, "Nahrávání je pozastavené: na disku nahrávek dochází místo.")
        XCTAssertEqual(notice(failure: .unreachable, paused: true)?.text, "Brána antény není dostupná.",
                       "a failed check says nothing about the recording")
        XCTAssertEqual(notice(failure: .unreachable, paused: true)?.isWarning, true)
    }

    /// The status answer read through the client: 401 = the key, anything else = not reachable, cancelled = nothing.
    func testStatusFailureFrom401() async throws {
        func failure(_ response: HTTPResponse) async -> HomeRules.StatusFailure? {
            let transport = FakeTransport()
            transport.on("/dvb/status", response)
            let client = DVBClient(config: GatewayConfig(address: "127.0.0.1:1", key: "k")!, transport: transport)
            do {
                _ = try await client.status()
                return nil
            } catch {
                return HomeRules.statusFailure(error)
            }
        }
        let rejected = await failure(HTTPResponse(status: 401, body: Data(#"{"error": "Chybí nebo nesedí klíč"}"#.utf8)))
        XCTAssertEqual(rejected, .rejected)
        let broken = await failure(HTTPResponse(status: 502, body: Data("Bad Gateway".utf8)))
        XCTAssertEqual(broken, .unreachable)
        XCTAssertEqual(HomeRules.statusFailure(URLError(.notConnectedToInternet)), .unreachable)
        XCTAssertNil(HomeRules.statusFailure(URLError(.cancelled)))
        XCTAssertNil(HomeRules.statusFailure(CancellationError()))
    }

    /// While Tailscale in the app connects, its state instead of "not reachable" (not as a warning).
    func testTailscaleConnectingWins() {
        let connecting = notice(tailnet: TailnetRules.connectingLine, failure: .unreachable)
        XCTAssertEqual(connecting?.text, TailnetRules.connectingLine)
        XCTAssertEqual(connecting?.isWarning, false)
    }

    /// The first ten ČT and Anténa channels; the Chamber's streams are left out wherever they are in the order.
    func testLiveRowLeavesOutTheChamber() {
        let ct = Channels.all.map(MixedChannel.ct)
        let antenna = (1...6).map { MixedChannel.antenna(DVBChannel(id: "a\($0)", number: $0, name: "A\($0)")) }
        let psp = PSPStreams.all.map(MixedChannel.parliament)
        let visible = [ct[0]] + psp + Array(ct.dropFirst()) + antenna
        let row = HomeRules.liveRow(visible)
        XCTAssertEqual(row.count, 10)
        XCTAssertNotEqual(row, Array(visible.prefix(10)), "the rule differs from a plain prefix")
        XCTAssertEqual(row, Array((ct + antenna).prefix(10)))
        XCTAssertEqual(HomeRules.liveRow(psp), [])
        XCTAssertEqual(HomeRules.liveRow([ct[1]]), [ct[1]])
    }
}
