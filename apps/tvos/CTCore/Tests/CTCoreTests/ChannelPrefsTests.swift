import Foundation
import XCTest
@testable import CTCore

final class ChannelPrefsTests: XCTestCase {
    private func prefs(_ item: [String: Any]) -> ChannelPrefs {
        let raw = item["prefs"] as! [String: Any]
        return ChannelPrefs(order: raw["order"] as! [String], hidden: raw["hidden"] as! [String])
    }

    func testGoldenArrangeCases() throws {
        let cases = try Fixture.json("channel_prefs_cases")["arrange"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 6)
        for item in cases {
            let name = item["name"] as! String
            let result = prefs(item).arrange(item["channels"] as! [String])
            XCTAssertEqual(result.all, item["all"] as! [String], name)
            XCTAssertEqual(result.visible, item["visible"] as! [String], name)
        }
    }

    func testGoldenMoveCases() throws {
        let cases = try Fixture.json("channel_prefs_cases")["move"] as! [[String: Any]]
        XCTAssertEqual(cases.count, 6)
        for item in cases {
            let name = item["name"] as! String
            var value = prefs(item)
            let before = value.hidden
            value.move(item["key"] as! String, by: item["by"] as! Int, in: item["channels"] as! [String])
            XCTAssertEqual(value.order, item["order"] as! [String], name)
            XCTAssertEqual(value.hidden, before, name)
        }
    }

    func testToggleAndReset() {
        var value = ChannelPrefs(order: ["ct:CH_2"])
        value.toggleHidden("ct:CH_1")
        XCTAssertTrue(value.isHidden("ct:CH_1"))
        XCTAssertEqual(value.order, ["ct:CH_2"])  // hiding does not change the order
        value.toggleHidden("ct:CH_1")
        XCTAssertFalse(value.isHidden("ct:CH_1"))
        value.setHidden("ct:CH_24", true)
        value.reset()
        XCTAssertEqual(value, ChannelPrefs())
    }

    func testTypedArrangement() {
        let channels = [DVBChannel(id: "a", number: 1, name: "A"), DVBChannel(id: "b", number: 2, name: "B")]
        let value = ChannelPrefs(order: ["dvb:b"], hidden: ["dvb:a"])
        let result = value.arrange(channels, key: ChannelPrefs.dvbKey)
        XCTAssertEqual(result.all.map(\.id), ["b", "a"])
        XCTAssertEqual(result.visible.map(\.id), ["b"])
        XCTAssertEqual(ChannelPrefs.ctKey(Channels.all[0]), "ct:CH_1")
    }

    @MainActor
    func testStorePersistsAndCaps() {
        let backend = MemoryBackend()
        let store = ChannelPrefsStore(backend: backend)
        store.update { $0.move("ct:CH_1", by: 1, in: Channels.all.map(ChannelPrefs.ctKey)) }
        store.update { $0.toggleHidden("ct:CH_24") }
        XCTAssertEqual(store.internet().all.map(\.id).prefix(2), ["CH_2", "CH_1"])
        XCTAssertFalse(store.internet().visible.contains { $0.id == "CH_24" })
        let reloaded = ChannelPrefsStore(backend: backend)
        XCTAssertEqual(reloaded.prefs, store.prefs)
        XCTAssertNotNil(backend.data(forKey: ChannelPrefsStore.storageKey))

        store.update { $0.hidden = (0..<600).map { "dvb:\($0)" } }
        XCTAssertEqual(store.prefs.hidden.count, ChannelPrefs.maxKeys)
        XCTAssertEqual(store.prefs.hidden.last, "dvb:599")

        backend.set(Data("garbage".utf8), forKey: ChannelPrefsStore.storageKey)
        XCTAssertEqual(ChannelPrefsStore(backend: backend).prefs, ChannelPrefs())
    }
}
