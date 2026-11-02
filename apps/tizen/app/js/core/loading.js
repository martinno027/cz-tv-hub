/*
 * "Loading is always visible" (spec/apps.md "Archiv" → Layout, T52): a part of the archive that waits for the
 * gateway (the grid, the show page, its episode list) shows a loader, but only after DELAY_MS (a fast answer shows
 * none, no flash). New content for the part (a menu item, sub-genre, sort, search, category; a season, year or month
 * chip; a show opened) puts the loader over the part, its old content dimmed and kept; the part's next page puts a
 * small loader below its last row. A newer choice replaces the waited one: the wait goes on (its loader stays) and
 * only the newest request's answer counts. No DOM here: the screens draw `place()`.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var DELAY_MS = 150;
  var REPLACE = 'replace'; // new content for the part: the loader over it, the old content dimmed
  var MORE = 'more'; // the part's next page: a small loader below its last row

  function defaultTimers() {
    return {
      set: function (fn, ms) { return root.setTimeout(fn, ms); },
      clear: function (t) { root.clearTimeout(t); }
    };
  }

  /**
   * One part's wait. `onShown()` is called when the loader is due (DELAY_MS after the wait began); `timers`
   * ({set, clear}) only for tests.
   */
  function Wait(onShown, timers) {
    this.onShown = onShown || function () {};
    this.timers = timers || defaultTimers();
    this.latest = 0;
    this.kind = null; // REPLACE | MORE of the newest request; null = not waiting
    this.shown = false;
    this.timer = null;
  }

  /**
   * A request for the part starts (`kind` REPLACE or MORE): its ticket. Waiting already (a newer choice), the wait
   * goes on from where it was: the loader shown stays, one not shown yet keeps its time.
   */
  Wait.prototype.begin = function (kind) {
    var self = this;
    this.latest += 1;
    var waiting = this.kind !== null;
    // a new choice outranks a next page that was waited for (that answer is dropped)
    this.kind = kind === MORE && (!waiting || this.kind === MORE) ? MORE : REPLACE;
    if (!waiting) {
      this.timer = this.timers.set(function () {
        self.timer = null;
        if (self.kind === null) return;
        self.shown = true;
        self.onShown();
      }, DELAY_MS);
    }
    return this.latest;
  };

  /**
   * The answer (or the error) for `ticket` is there: true = the newest, take it (the wait is over); false = a newer
   * request replaced it, drop it (the wait and its loader go on).
   */
  Wait.prototype.finish = function (ticket) {
    if (ticket !== this.latest || this.kind === null) return false;
    this.stop();
    return true;
  };

  /** Nothing is waited for any more (the part closed or cleared); answers still on their way are dropped. */
  Wait.prototype.cancel = function () {
    this.latest += 1;
    this.stop();
  };

  Wait.prototype.stop = function () {
    if (this.timer !== null) this.timers.clear(this.timer);
    this.timer = null;
    this.kind = null;
    this.shown = false;
  };

  Wait.prototype.waiting = function () {
    return this.kind !== null;
  };

  /** Whether `ticket` is still the newest request waited for (an older answer is dropped). */
  Wait.prototype.current = function (ticket) {
    return this.kind !== null && ticket === this.latest;
  };

  /** Where the loader is now: 'none', 'over' (the part dimmed under it) or 'below' (small, under the last row). */
  Wait.prototype.place = function () {
    if (!this.shown || this.kind === null) return 'none';
    return this.kind === MORE ? 'below' : 'over';
  };

  CT.Loading = {
    DELAY_MS: DELAY_MS,
    REPLACE: REPLACE,
    MORE: MORE,
    Wait: Wait,
    /** A page of a paged list: page 1 is new content (over), a later page is more of it (below). */
    kindOfPage: function (page) {
      return page > 1 ? MORE : REPLACE;
    }
  };
})(typeof window !== 'undefined' ? window : global);
