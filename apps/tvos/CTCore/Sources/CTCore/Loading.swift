import Foundation

/// The loader of one part of the archive that waits for the gateway (T52, spec/apps.md "Loading is always visible"):
/// the grid, the show page, its episode list, a next page. The part keeps its old content, dimmed, while the loader
/// shows; the loader shows only once the part has waited `delay` (a fast answer shows none, no flash). A newer choice
/// replaces the waited one without restarting the wait (its loader stays): only the newest request's answer counts.
public struct LoadingState: Equatable, Sendable {
    /// How long a part waits before its loader shows.
    public static let delay: Duration = .milliseconds(150)
    /// The old content under the loader.
    public static let dimmedOpacity = 0.35
    /// The ring on the TV; the next page's ring is half of it.
    public static let ringSize = 64.0
    public static let smallRingSize = ringSize / 2

    /// The newest request handed out; an answer for an older one is dropped.
    private var latest = 0
    /// Since when the part waits; nil = not waiting.
    public private(set) var since: ContinuousClock.Instant?

    public init() {}

    public var isWaiting: Bool { since != nil }

    /// A request starts: its ticket. Waiting already (a newer choice), the wait goes on from where it was.
    public mutating func begin(at now: ContinuousClock.Instant = .now) -> Int {
        latest += 1
        if since == nil { since = now }
        return latest
    }

    /// The answer (or the error) for `ticket` is there: true = it is the newest, show it (the loader goes);
    /// false = a newer choice replaced it, drop it (the loader stays for the newer one).
    @discardableResult
    public mutating func finish(_ ticket: Int) -> Bool {
        guard ticket == latest, since != nil else { return false }
        since = nil
        return true
    }

    /// Nothing waited for any more (the part was cleared); answers still on their way are dropped.
    public mutating func cancel() {
        latest += 1
        since = nil
    }

    /// Whether the loader (and the dimming) shows at `now`.
    public func isShown(at now: ContinuousClock.Instant = .now) -> Bool {
        guard let since else { return false }
        return now - since >= Self.delay
    }

    /// How long until the loader shows (zero = it shows); nil = not waiting.
    public func untilShown(at now: ContinuousClock.Instant = .now) -> Duration? {
        guard let since else { return nil }
        return max(.zero, Self.delay - (now - since))
    }
}

/// A choice (a season, year or month chip) whose list loads while the list shown stays (T52). A newer choice
/// replaces the waited one; when the newest fails, the chips go back to the choice **whose list is shown** — the
/// one before the first choice still waited for, never a replaced choice that never loaded.
public struct ChoiceLoad<Shown> {
    public private(set) var loading = LoadingState()
    /// What a failing choice puts back; set when the first of the waited choices began.
    public private(set) var fallback: Shown?

    public init() {}

    /// A choice starts loading over `shown` (what is listed now); its ticket.
    public mutating func begin(over shown: @autoclosure () -> Shown, at now: ContinuousClock.Instant = .now) -> Int {
        if !loading.isWaiting { fallback = shown() }
        return loading.begin(at: now)
    }

    /// The answer for `ticket` came: true = show it (the newest choice), false = drop it.
    public mutating func succeed(_ ticket: Int) -> Bool {
        guard loading.finish(ticket) else { return false }
        fallback = nil
        return true
    }

    /// The load for `ticket` failed: what to put back, or nil when a newer choice replaced it (nothing changes).
    public mutating func fail(_ ticket: Int) -> Shown? {
        guard loading.finish(ticket) else { return nil }
        defer { fallback = nil }
        return fallback
    }

    /// Nothing waited for any more.
    public mutating func cancel() {
        loading.cancel()
        fallback = nil
    }
}

/// What the archive grid lists (a category, our recordings, a search), page by page (T52, T57): the decisions of the
/// app's pager, which only adds the requests. A new list's first page keeps the list shown (dimmed under the loader)
/// until it answers; only the newest answer counts; no next page is asked while a first page is waited for.
public struct ArchivePaging: Equatable, Sendable {
    public private(set) var cards: [ArchiveCard] = []
    public private(set) var genres: [ArchiveGenre] = []
    public private(set) var attribution = ProgrammeDetails.defaultAttribution
    /// A first page came (or failed): an empty list says so instead of waiting.
    public private(set) var loaded = false
    /// The gateway's own text in an answer ("iVysílání teď neodpovídá"; our recordings still listed).
    public private(set) var notice: String?
    /// The first page could not be loaded.
    public private(set) var failure: String?
    /// The next page of the list shown; nil = none (or not known).
    public private(set) var nextPage: Int?
    /// A first page on its way (the loader over the grid).
    public private(set) var loading = LoadingState()
    /// A next page on its way (the small loader below the last row).
    public private(set) var more = LoadingState()

    public init() {}

    /// Nothing listed, nothing waited for.
    public mutating func reset() {
        loading.cancel()
        clear()
    }

    /// The list shown stays, undimmed, but nothing is waited for (a search being typed).
    public mutating func stopWaiting() {
        loading.cancel()
    }

    /// A new list's first page starts: its ticket. The list shown stays until the answer and pages no more meanwhile.
    /// Its next page is kept (`beginMore` asks none while waiting): a cancelled load leaves the list paging (T57).
    public mutating func begin(at now: ContinuousClock.Instant = .now) -> Int {
        more.cancel()
        return loading.begin(at: now)
    }

    /// The first page for `ticket` came: true = it is shown (the newest), false = dropped.
    @discardableResult
    public mutating func succeed(_ ticket: Int, page: ArchivePage) -> Bool {
        guard loading.finish(ticket) else { return false }
        clear()
        apply(page)
        notice = page.error
        loaded = true
        return true
    }

    /// The first page for `ticket` failed with `message`: true = the failure is shown, false = dropped.
    @discardableResult
    public mutating func fail(_ ticket: Int, message: String) -> Bool {
        guard loading.finish(ticket) else { return false }
        clear()
        failure = message
        loaded = true
        return true
    }

    /// The load for `ticket` was cancelled (the grid left, a newer choice, back on the list already shown). Still the
    /// newest: the wait ends and the list shown stays as it was, its next pages too; a newer load keeps its wait.
    /// T57: the loader stayed for good when the viewer went back to the list shown before its replacement answered
    /// (no new load starts for the list already shown), and that list no longer paged.
    public mutating func abandon(_ ticket: Int) {
        loading.finish(ticket)
    }

    /// The next page to ask for, with its ticket; nil while none is due (one on its way, a first page waited for).
    public mutating func beginMore() -> (ticket: Int, page: Int)? {
        guard !more.isWaiting, !loading.isWaiting, let next = nextPage else { return nil }
        return (more.begin(), next)
    }

    /// The next page for `ticket` came (`page`) or failed (nil: asked again when the end shows again).
    public mutating func finishMore(_ ticket: Int, page: ArchivePage?) {
        guard more.finish(ticket) else { return }
        if let page { apply(page) }
    }

    private mutating func clear() {
        more.cancel()
        cards = []
        genres = []
        loaded = false
        notice = nil
        failure = nil
        nextPage = nil
    }

    private mutating func apply(_ page: ArchivePage) {
        let known = Set(cards.map(\.id))
        cards += page.cards.filter { !known.contains($0.id) }
        if genres.isEmpty { genres = page.genres }
        attribution = page.attribution
        nextPage = page.nextPage
    }
}
