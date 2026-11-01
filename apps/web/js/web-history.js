/*
 * Back = the browser's Back (spec/web.md "Input (T29)"): what the remote's Back closes one step at a time is a
 * browser history entry — a Nastavení section, the Archiv sub-menu, the Archiv show page, the programme page, the
 * player. The app's own Back (Esc/Backspace, "Zpět" buttons) closes as on the TV and the history follows; the
 * browser's Back/Forward (also Alt+←) move the app. Forward reopens what can be reopened (section, programme page,
 * player); the Archiv's own steps cannot (their data is gone), so Forward stops before them.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var MAX_BACKS = 6; // Back presses tried to close one entry (a popup or the grid zone may take one first)

  /**
   * The open layers of the app, outermost first: [{type, key, data}]. `app` = CT.App (or a fake with the same
   * fields): settings section, archive sub-menu and show page, programme page, player.
   */
  function stack(app) {
    var out = [];
    var s = app.screens || {};
    if (app.tab === 'settings' && s.settings && s.settings.zone && s.settings.zone !== 'choice') {
      out.push({ type: 'section', key: 'section:' + s.settings.section, data: s.settings.section });
    }
    var archive = s.archive;
    if (app.tab === 'archive' && archive) {
      if (archive.menu && archive.menu.level === 'sub') {
        out.push({ type: 'sub', key: 'sub:' + archive.menu.category, data: null });
      }
      if (archive.page && archive.page.isOpen()) {
        out.push({ type: 'show', key: 'show:' + (archive.page.card && archive.page.card.id), data: null });
      }
    }
    if (app.programme && app.programme.isOpen()) {
      var p = app.programme;
      out.push({ type: 'programme', key: 'programme:' + (p.entry && p.entry.title) + '@' + (p.entry && p.entry.start),
        data: { entry: p.entry, day: p.day } });
    }
    if (app.player && app.player.isOpen()) out.push({ type: 'player', key: 'player', data: app.player.request });
    return out;
  }

  /** {pop, push}: entries to leave (from the end) and to add, from `before` to `after` (compared by key). */
  function diff(before, after) {
    var k = 0;
    while (k < before.length && k < after.length && before[k].key === after[k].key) k++;
    return { pop: before.length - k, push: after.slice(k), keep: k };
  }

  /** Whether an entry can be opened again by Forward. */
  function reopenable(entry) {
    return entry.type === 'section' || entry.type === 'programme' || entry.type === 'player';
  }

  /**
   * Keeps `history` (window.history) in step with `app`. `back()` presses the app's Back once; `reopen(entry)` opens
   * an entry again (Forward). Returns {sync, onPop, shown} — sync() after anything that may have opened or closed
   * a layer, onPop(state) for popstate.
   */
  function Tracker(app, history, back, reopen) {
    this.app = app;
    this.history = history;
    this.back = back;
    this.reopen = reopen;
    this.shown = []; // the entries the history holds above the base, in order
    this.saved = []; // entries left by Back, for Forward
    this.expect = null; // {depth}: a history.go of our own whose popstate only confirms
    this.pending = null; // entries to push once that popstate came
    history.replaceState({ ctWeb: 0 }, '');
  }

  Tracker.prototype.push = function (entries) {
    var self = this;
    entries.forEach(function (e) {
      self.shown.push(e);
      self.saved = self.saved.slice(0, self.shown.length - 1);
      self.saved[self.shown.length - 1] = e;
      self.history.pushState({ ctWeb: self.shown.length }, '');
    });
  };

  /** After the app changed by itself: leave closed entries (history.go back) and add opened ones. */
  Tracker.prototype.sync = function () {
    if (this.expect) return; // our own history.go is still on its way
    var d = diff(this.shown, stack(this.app));
    if (d.pop > 0) {
      this.shown = this.shown.slice(0, d.keep);
      this.expect = { depth: d.keep };
      this.pending = d.push;
      this.history.go(-d.pop);
      return;
    }
    if (d.push.length) this.push(d.push);
  };

  /** The browser moved in the history (Back, Forward, Alt+←, or our own go()). */
  Tracker.prototype.onPop = function (state) {
    var depth = state && typeof state.ctWeb === 'number' ? state.ctWeb : 0;
    if (this.expect && depth === this.expect.depth) {
      this.expect = null;
      var pending = this.pending || [];
      this.pending = null;
      this.push(pending);
      this.sync();
      return;
    }
    this.expect = null;
    this.pending = null;
    if (depth < this.shown.length) {
      // Back: the app's Back until only `depth` layers are open
      for (var i = 0; i < MAX_BACKS && stack(this.app).length > depth; i++) this.back();
      this.shown = this.shown.slice(0, depth);
      var now = stack(this.app);
      if (now.length > depth) this.push(now.slice(depth)); // could not close: the history stays in step
      return;
    }
    if (depth > this.shown.length) {
      // Forward: open again what was open, as far as it can be
      var reached = this.shown.length;
      while (reached < depth && this.saved[reached] && reopenable(this.saved[reached])) {
        this.reopen(this.saved[reached]);
        if (stack(this.app).length !== reached + 1) break;
        this.shown.push(this.saved[reached]);
        reached++;
      }
      if (reached < depth) {
        this.expect = { depth: reached };
        this.history.go(reached - depth);
      }
    }
  };

  CT.WebHistory = { stack: stack, diff: diff, reopenable: reopenable, Tracker: Tracker, MAX_BACKS: MAX_BACKS };
})(typeof window !== 'undefined' ? window : global);
