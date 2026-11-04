import XCTest
@testable import CTCore

/// The archive grid's pages (T52 loader rules, T57: a cancelled load that is still the newest ends the wait).
final class ArchivePagingTests: XCTestCase {
    private func card(_ id: String) -> ArchiveCard {
        ArchiveCard(id: id, source: .ct, title: id, kind: nil)
    }

    private func page(_ ids: [String], next: Int?, error: String? = nil) -> ArchivePage {
        ArchivePage(title: "", genres: [], cards: ids.map(card), nextPage: next, attribution: "TMDB", error: error)
    }

    /// Filmy listed with a next page.
    private func films() -> ArchivePaging {
        var paging = ArchivePaging()
        let ticket = paging.begin()
        XCTAssertTrue(paging.succeed(ticket, page: page(["f1", "f2"], next: 2)))
        return paging
    }

    /// T57: Filmy → Seriály (slow) → back on Filmy before Seriály answers: the Seriály load is cancelled and no new
    /// one starts (Filmy is the list shown). The wait ends and Filmy pages on as before.
    func testCancelledNewestLoadEndsTheWaitAndKeepsTheListPaging() {
        var paging = films()
        let series = paging.begin()
        XCTAssertTrue(paging.loading.isWaiting)
        XCTAssertNil(paging.beginMore(), "no next page while a first page is waited for")
        paging.abandon(series)
        XCTAssertFalse(paging.loading.isWaiting, "the loader stayed for a load nobody waits for")
        XCTAssertEqual(paging.cards.map(\.id), ["f1", "f2"])
        XCTAssertEqual(paging.beginMore()?.page, 2, "the list shown lost its next page")
        // its late answer, if any, is dropped
        XCTAssertFalse(paging.succeed(series, page: page(["s1"], next: nil)))
        XCTAssertEqual(paging.cards.map(\.id), ["f1", "f2"])
    }

    /// A cancelled load replaced by a newer one leaves the newer one's wait alone (the loader stays for it).
    func testCancelledOlderLoadKeepsTheNewerWait() {
        var paging = films()
        let series = paging.begin()
        let documentaries = paging.begin()
        paging.abandon(series)
        XCTAssertTrue(paging.loading.isWaiting)
        XCTAssertNil(paging.beginMore())
        XCTAssertTrue(paging.succeed(documentaries, page: page(["d1"], next: nil)))
        XCTAssertFalse(paging.loading.isWaiting)
        XCTAssertEqual(paging.cards.map(\.id), ["d1"])
        XCTAssertNil(paging.beginMore(), "the new list has no next page")
    }

    /// The two cases differ: an answer replaces the list, a cancel keeps it.
    func testAnswerReplacesTheListACancelKeepsIt() {
        var answered = films(), cancelled = films()
        let a = answered.begin(), c = cancelled.begin()
        answered.succeed(a, page: page(["s1"], next: nil))
        cancelled.abandon(c)
        XCTAssertNotEqual(answered.cards, cancelled.cards)
        XCTAssertEqual(cancelled.cards.map(\.id), ["f1", "f2"])
    }

    func testOnlyTheNewestAnswerCounts() {
        var paging = films()
        let old = paging.begin()
        let new = paging.begin()
        XCTAssertFalse(paging.succeed(old, page: page(["o1"], next: 2)))
        XCTAssertFalse(paging.fail(old, message: "x"))
        XCTAssertTrue(paging.loading.isWaiting)
        XCTAssertTrue(paging.succeed(new, page: page(["n1"], next: nil, error: "iVysílání teď neodpovídá")))
        XCTAssertEqual(paging.cards.map(\.id), ["n1"])
        XCTAssertEqual(paging.notice, "iVysílání teď neodpovídá")
        XCTAssertTrue(paging.loaded)
    }

    func testFailureClearsTheList() {
        var paging = films()
        let ticket = paging.begin()
        XCTAssertTrue(paging.fail(ticket, message: "Bránu Anténa se nepodařilo kontaktovat"))
        XCTAssertEqual(paging.failure, "Bránu Anténa se nepodařilo kontaktovat")
        XCTAssertTrue(paging.cards.isEmpty)
        XCTAssertTrue(paging.loaded)
        XCTAssertNil(paging.beginMore())
    }

    func testNextPagesOneAtATimeAndAppended() {
        var paging = films()
        let more = try! XCTUnwrap(paging.beginMore())
        XCTAssertEqual(more.page, 2)
        XCTAssertNil(paging.beginMore(), "one next page at a time")
        paging.finishMore(more.ticket, page: page(["f2", "f3"], next: nil))
        XCTAssertEqual(paging.cards.map(\.id), ["f1", "f2", "f3"], "repeated cards are left out")
        XCTAssertNil(paging.beginMore())
    }

    /// A new list cancels a next page still on its way: its answer is not appended to another list.
    func testNextPageOfTheOldListIsDropped() {
        var paging = films()
        let more = try! XCTUnwrap(paging.beginMore())
        let series = paging.begin()
        paging.finishMore(more.ticket, page: page(["f3"], next: 3))
        XCTAssertEqual(paging.cards.map(\.id), ["f1", "f2"])
        XCTAssertTrue(paging.succeed(series, page: page(["s1"], next: nil)))
        XCTAssertEqual(paging.cards.map(\.id), ["s1"])
    }

    func testResetAndStopWaiting() {
        var paging = films()
        _ = paging.begin()
        paging.stopWaiting()
        XCTAssertFalse(paging.loading.isWaiting)
        XCTAssertEqual(paging.cards.map(\.id), ["f1", "f2"])
        paging.reset()
        XCTAssertTrue(paging.cards.isEmpty)
        XCTAssertFalse(paging.loaded)
        XCTAssertNil(paging.beginMore())
    }
}
