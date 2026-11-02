import XCTest
@testable import CTCore

/// T52: the archive's loader shows only after 150 ms of waiting, and the newest request wins
/// (spec/apps.md "Loading is always visible").
final class LoadingStateTests: XCTestCase {
    private let start = ContinuousClock.now

    func testSpecValues() {
        XCTAssertEqual(LoadingState.delay, .milliseconds(150))
        XCTAssertEqual(LoadingState.dimmedOpacity, 0.35)
        XCTAssertEqual(LoadingState.ringSize, 64)
        XCTAssertEqual(LoadingState.smallRingSize, 32, "the next page's loader is half size")
    }

    func testShownOnlyAfterTheDelay() {
        var state = LoadingState()
        XCTAssertFalse(state.isShown(at: start))
        XCTAssertNil(state.untilShown(at: start), "not waiting: nothing to show")
        _ = state.begin(at: start)
        XCTAssertTrue(state.isWaiting)
        XCTAssertFalse(state.isShown(at: start), "no flash at once")
        XCTAssertFalse(state.isShown(at: start + .milliseconds(149)), "a fast answer shows none")
        XCTAssertTrue(state.isShown(at: start + .milliseconds(150)))
        XCTAssertTrue(state.isShown(at: start + .seconds(5)))
        XCTAssertEqual(state.untilShown(at: start + .milliseconds(100)), .milliseconds(50))
        XCTAssertEqual(state.untilShown(at: start + .seconds(1)), .zero)
    }

    func testTheAnswerEndsTheWait() {
        var state = LoadingState()
        let ticket = state.begin(at: start)
        XCTAssertTrue(state.finish(ticket))
        XCTAssertFalse(state.isWaiting)
        XCTAssertFalse(state.isShown(at: start + .seconds(1)), "gone when the answer is there")
    }

    func testTheNewestRequestWins() {
        var state = LoadingState()
        let first = state.begin(at: start)
        let second = state.begin(at: start + .milliseconds(100))
        XCTAssertNotEqual(first, second)
        XCTAssertFalse(state.finish(first), "the replaced choice's answer is dropped")
        XCTAssertTrue(state.isWaiting, "its loader stays for the newer choice")
        // the wait goes on from the first choice: the loader does not restart its 150 ms
        XCTAssertTrue(state.isShown(at: start + .milliseconds(150)))
        XCTAssertTrue(state.finish(second))
        XCTAssertFalse(state.isWaiting)
        XCTAssertFalse(state.finish(second), "an answer counts once")
    }

    func testANewWaitStartsAfresh() {
        var state = LoadingState()
        let first = state.begin(at: start)
        state.finish(first)
        _ = state.begin(at: start + .seconds(1))
        XCTAssertFalse(state.isShown(at: start + .seconds(1) + .milliseconds(100)), "the old wait does not count")
        XCTAssertTrue(state.isShown(at: start + .seconds(1) + .milliseconds(150)))
    }

    func testCancelDropsWhatIsOnItsWay() {
        var state = LoadingState()
        let ticket = state.begin(at: start)
        state.cancel()
        XCTAssertFalse(state.isWaiting)
        XCTAssertFalse(state.finish(ticket), "an answer for a cleared part is dropped")
    }
}

/// T52: a failing season/year/month chip goes back to the choice whose episodes are listed.
final class ChoiceLoadTests: XCTestCase {
    func testAFailingChoiceGoesBackToWhatIsListed() {
        var load = ChoiceLoad<String>()
        let a = load.begin(over: "listed")
        XCTAssertTrue(load.loading.isWaiting)
        XCTAssertEqual(load.fail(a), "listed")
        XCTAssertFalse(load.loading.isWaiting)
    }

    /// Chip A, then chip B before A loaded, B fails: back to the listed choice, not to A that never loaded.
    func testAReplacedChoiceIsNeverTheFallback() {
        var load = ChoiceLoad<String>()
        let a = load.begin(over: "listed")
        let b = load.begin(over: "A")  // the chips show A while it is waited for
        XCTAssertFalse(load.succeed(a), "A's late answer is dropped")
        XCTAssertTrue(load.loading.isWaiting, "B is still waited for")
        XCTAssertEqual(load.fail(b), "listed")
    }

    func testAReplacedChoiceFailingChangesNothing() {
        var load = ChoiceLoad<String>()
        let a = load.begin(over: "listed")
        let b = load.begin(over: "A")
        XCTAssertNil(load.fail(a), "A's error is dropped, B still waited for")
        XCTAssertTrue(load.loading.isWaiting)
        XCTAssertTrue(load.succeed(b))
        XCTAssertFalse(load.loading.isWaiting)
    }

    func testAfterASuccessTheNewListIsTheFallback() {
        var load = ChoiceLoad<String>()
        XCTAssertTrue(load.succeed(load.begin(over: "listed")))
        let c = load.begin(over: "B")
        XCTAssertEqual(load.fail(c), "B")
    }

    func testCancelDropsEverything() {
        var load = ChoiceLoad<String>()
        let a = load.begin(over: "listed")
        load.cancel()
        XCTAssertNil(load.fail(a))
        XCTAssertNil(load.fallback)
    }
}
