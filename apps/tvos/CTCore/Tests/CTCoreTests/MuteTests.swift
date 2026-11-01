import XCTest
@testable import CTCore

@MainActor
private final class FakePlayer: MutablePlayer {
    var isMuted = false
}

/// spec/apps.md "Mute (T34)": the state holds for every player until the app closes.
@MainActor
final class MuteTests: XCTestCase {
    func testStartsUnmuted() {
        let mute = PlayerMute()
        let player = FakePlayer()
        mute.attach(player)
        XCTAssertFalse(mute.isMuted)
        XCTAssertFalse(player.isMuted)
        XCTAssertEqual(mute.actionTitle, "Vypnout zvuk")
    }

    func testToggleMutesThePlayerOnScreen() {
        let mute = PlayerMute()
        let player = FakePlayer()
        mute.attach(player)
        mute.toggle()
        XCTAssertTrue(player.isMuted)
        XCTAssertEqual(mute.actionTitle, "Zapnout zvuk")
        mute.toggle()
        XCTAssertFalse(player.isMuted)
        XCTAssertEqual(mute.actionTitle, "Vypnout zvuk")
    }

    func testCarriesToTheNextProgramme() {
        let mute = PlayerMute()
        let first = FakePlayer()
        mute.attach(first)
        mute.toggle()
        mute.detach(first)
        let next = FakePlayer()  // a new player: next programme, the guide, the timeline player
        XCTAssertFalse(next.isMuted)
        mute.attach(next)
        XCTAssertTrue(next.isMuted)
    }

    func testUnmutedCarriesToo() {
        let mute = PlayerMute()
        let first = FakePlayer()
        mute.attach(first)
        mute.toggle()
        mute.toggle()
        let next = FakePlayer()
        next.isMuted = true
        mute.attach(next)
        XCTAssertFalse(next.isMuted)
    }

    /// The old player stopping after the new one appeared must not take the new one's control away.
    func testLateDetachOfTheOldPlayerKeepsTheNewOne() {
        let mute = PlayerMute()
        let old = FakePlayer(), new = FakePlayer()
        mute.attach(old)
        mute.attach(new)
        mute.detach(old)
        mute.toggle()
        XCTAssertTrue(new.isMuted)
        XCTAssertFalse(old.isMuted)
    }
}
