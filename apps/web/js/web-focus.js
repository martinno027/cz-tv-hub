/*
 * Mouse like on any web page, keyboard like the remote (spec/web.md "Input (T29)"):
 * - the remote's focus (`.focused`) is shown only while the keyboard is used; the first mouse action hides it, so a
 *   click never leaves a second highlighted item (a white day chip next to the selected "Dnes");
 * - one click acts: the clicked item becomes the screen's focus directly (no arrow walking through other items,
 *   which would switch Archiv filters or scroll the guide on the way) and gets OK; right click / long press is
 *   long OK where the screen has one.
 * The adapters below know where each Samsung screen keeps its focus; a screen whose focus model changes changes its
 * adapter here too (spec/web.md "Where it lives").
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  /** Keys whose first press only shows the focus (it was hidden: acting on an unseen item would surprise). */
  var REVEAL_ONLY = { left: true, right: true, up: true, down: true, enter: true };

  /**
   * Keyboard vs pointer: `pointer` true while the mouse/touch was used last. onKey(name) → {reveal, act}: reveal =
   * the focus becomes visible now; act = the key goes on to the app.
   */
  function Mode() {
    this.pointer = true; // a web page starts without a remote focus
  }

  Mode.prototype.onKey = function (name) {
    if (!this.pointer) return { reveal: false, act: true };
    this.pointer = false;
    return { reveal: true, act: !REVEAL_ONLY[name] };
  };

  Mode.prototype.onPointer = function () {
    var was = this.pointer;
    this.pointer = true;
    return !was; // true = the focus has to disappear now
  };

  function indexIn(el) {
    return el && el.parentNode ? Array.prototype.indexOf.call(el.parentNode.children, el) : -1;
  }

  /**
   * Adapters: [{match: selector, focus(app, el) → false when not applicable, long?(app, el)}]. `focus` makes `el` the
   * screen's focus (and `app.zone` the content); the click then sends OK through the app's own key handling.
   */
  var ADAPTERS = [
    { match: '#ar-menu .button', focus: function (app, el) { CT.ArchiveCards.Menu.focus = indexIn(el); } },
    { match: '#pg-buttons .button', focus: function (app, el) { app.programme.focus = indexIn(el); } },
    { match: '#sh-buttons .button', focus: function (app, el) {
      var page = app.screens.archive.page;
      page.zone = 'buttons';
      page.button = indexIn(el);
    } },
    { match: '#sh-seasons .day', focus: function (app, el) {
      var page = app.screens.archive.page;
      page.zone = 'seasons';
      page.seasonFocus = indexIn(el);
    } },
    { match: '#sh-years .day', focus: function (app, el) {
      var page = app.screens.archive.page;
      page.zone = 'years';
      page.yearFocus = indexIn(el);
    } },
    { match: '#sh-months .day', focus: function (app, el) {
      var page = app.screens.archive.page;
      page.zone = 'months';
      page.monthFocus = indexIn(el);
    } },
    { match: '.sh-ep', focus: function (app, el) {
      var page = app.screens.archive.page;
      var at = page.episodes.indexOf(Number(el.dataset.line));
      if (at < 0) return false;
      page.zone = 'episodes';
      page.episode = at;
    }, long: function (app) {
      var page = app.screens.archive.page;
      var e = page.focusedEpisode();
      if (e && app.resumeStore.offer(CT.Archive.resumeKey(e))) page.offerFromStart();
    } },
    { match: '#screen-live .card', focus: function (app, el) {
      var live = app.screens.live;
      var at = live.cards.map(function (c) { return c.el; }).indexOf(el);
      if (at < 0) return false;
      live.focus = at;
      app.zone = 'content';
    } },
    { match: '#days .day', focus: function (app, el) {
      var guide = app.screens.guide;
      guide.zone = 'days';
      guide.dayFocus = indexIn(el);
      app.zone = 'content';
    } },
    { match: '#grid-rows .cell', focus: function (app, el) {
      var guide = app.screens.guide;
      var at = slotOf(guide, el);
      if (!at) return false;
      var slot = guide.rows[at[0]].slots[at[1]];
      guide.zone = 'grid';
      guide.focusRow = at[0];
      guide.focusSlot = at[1];
      guide.anchor = Math.max(slot.start, Math.min(slot.end - 60000, guide.anchor));
      guide.liveMode = false;
      app.zone = 'content';
    } },
    { match: '#ar-side .ar-menu-item', focus: function (app, el) {
      var archive = app.screens.archive;
      archive.zone = 'menu';
      archive.menuFocus = indexIn(el);
      app.zone = 'content';
    } },
    { match: '#ar-sorts .day', focus: function (app, el) {
      var archive = app.screens.archive;
      archive.zone = 'sorts';
      archive.sortFocus = indexIn(el);
      app.zone = 'content';
    } },
    { match: '#ar-search', focus: function (app) {
      app.screens.archive.zone = 'field';
      app.zone = 'content';
    } },
    { match: '.ar-card', focus: function (app, el) {
      var archive = app.screens.archive;
      archive.zone = 'grid';
      // the grid on screen: the old cards stay choosable while new ones load (T52)
      (archive.stale || archive.grid).focus = Number(el.dataset.index);
      app.zone = 'content';
    }, long: function (app) {
      var archive = app.screens.archive;
      if (archive.isContinue()) archive.offerRemove();
    } },
    { match: '#set-choice .choice', focus: function (app, el) {
      var settings = app.screens.settings;
      settings.zone = 'choice';
      settings.section = el.getAttribute('data-section');
      app.zone = 'content';
    } },
    { match: '#set-channels .ch-name, #set-channels .ch-btn', focus: function (app, el) {
      var settings = app.screens.settings;
      for (var r = 0; r < settings.chItems.length; r++) {
        var c = settings.chItems[r].controls.indexOf(el);
        if (c < 0) continue;
        settings.zone = 'channels';
        settings.chRow = r;
        settings.chCol = c;
        app.zone = 'content';
        return true;
      }
      return false;
    } },
    { match: '#set-key, #set-address, #set-save, #set-clear, #set-thumbs, #set-key-reveal', focus: function (app, el) {
      var settings = app.screens.settings;
      var at = settings.items.indexOf(el);
      if (at < 0) return false;
      settings.zone = 'form';
      settings.focus = at;
      app.zone = 'content';
    } },
    { match: '#pf-mode-login, #pf-mode-create', focus: function (app, el) {
      var settings = app.screens.settings;
      settings.zone = 'profile';
      settings.pfMode = el.id === 'pf-mode-login' ? 'login' : 'create';
      settings.setProfileMessage('', '');
      settings.renderProfile();
      app.zone = 'content';
      return 'done'; // choosing the form is the whole action
    } },
    { match: '#pf-name, #pf-password, #pf-again, #pf-submit, #pf-logout', focus: function (app, el) {
      var settings = app.screens.settings;
      var at = settings.profileItems().indexOf(el.id);
      if (at < 0) return false;
      settings.zone = 'profile';
      settings.pfFocus = at;
      app.zone = 'content';
    } }
  ];

  /** The adapter for a clicked node (the closest matching element), or null: {adapter, el}. */
  function adapterFor(node) {
    for (var i = 0; node && node.closest && i < ADAPTERS.length; i++) {
      var el = node.closest(ADAPTERS[i].match);
      if (el) return { adapter: ADAPTERS[i], el: el };
    }
    return null;
  }

  /**
   * A click (or, `long`, a right click / long press) on `node`: the item becomes the focus and gets OK (`ok()`), or
   * long OK where the screen has one. Returns true when it acted.
   */
  function act(app, node, long, ok) {
    var found = adapterFor(node);
    if (!found) return false;
    var a = found.adapter;
    if (long && !a.long) return false;
    var r = a.focus(app, found.el);
    if (r === false) return false;
    if (r === 'done') return true;
    if (long) a.long(app, found.el);
    else ok();
    return true;
  }

  /** [row, slot] of a guide cell element, or null. */
  function slotOf(guide, el) {
    for (var r = 0; r < guide.rows.length; r++) {
      var slots = guide.rows[r].slots;
      for (var s = 0; s < slots.length; s++) if (slots[s].el === el) return [r, s];
    }
    return null;
  }

  var HOVER_DETAIL_MS = 200;

  /**
   * The guide's detail panel follows the mouse (spec/web.md "Input (T29)"): the programme under the pointer once it
   * rests ~0.2 s on it (no focus change, no scrolling); leaving the grid shows the focused (last clicked) one again.
   * `timers` = {set(fn, ms) → id, clear(id)}. over(cell) with the cell element under the pointer (or null), out()
   * when the pointer is no longer over the grid, cancel() on a press.
   */
  function HoverDetail(guide, timers) {
    this.guide = guide;
    this.timers = timers;
    this.timer = null;
    this.cell = null;
    this.showing = false; // the panel shows a hovered programme, not the focus
  }

  HoverDetail.prototype.over = function (cell) {
    if (cell === this.cell) return;
    this.cell = cell;
    this.timers.clear(this.timer);
    if (!cell) return; // between cells: the last one stays
    var self = this;
    this.timer = this.timers.set(function () {
      var at = slotOf(self.guide, cell);
      var slot = at ? self.guide.rows[at[0]].slots[at[1]] : null;
      if (!slot || !slot.item) return;
      self.showing = true;
      self.guide.showDetail(slot.item);
    }, HOVER_DETAIL_MS);
  };

  HoverDetail.prototype.out = function () {
    this.cancel();
    if (!this.showing) return;
    this.showing = false;
    var focused = this.guide.focused();
    this.guide.showDetail(focused ? focused.item : null);
  };

  HoverDetail.prototype.cancel = function () {
    this.timers.clear(this.timer);
    this.timer = null;
    this.cell = null;
  };

  /** Every element that shows the remote's focus loses it (pointer mode). */
  function hideFocus(doc) {
    var list = doc.querySelectorAll('.focused');
    for (var i = 0; i < list.length; i++) list[i].classList.remove('focused');
  }

  /** The app draws its focus again (keyboard mode), wherever it is now. */
  function showFocus(app) {
    app.applyFocus();
    if (app.programme && app.programme.isOpen()) app.programme.applyFocus();
    if (CT.ArchiveCards && CT.ArchiveCards.Menu.isOpen()) CT.ArchiveCards.Menu.render();
    var tl = app.player && app.player.tl;
    if (tl && tl.audioList) tl.renderAudioList();
  }

  CT.WebFocus = { Mode: Mode, ADAPTERS: ADAPTERS, adapterFor: adapterFor, act: act, indexIn: indexIn, slotOf: slotOf,
    HoverDetail: HoverDetail, HOVER_DETAIL_MS: HOVER_DETAIL_MS,
    hideFocus: hideFocus, showFocus: showFocus };
})(typeof window !== 'undefined' ? window : global);
