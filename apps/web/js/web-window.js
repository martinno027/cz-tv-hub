/*
 * Own window (spec/web.md "Own window (T46)"): the button ⧉ „Samostatné okno" next to ⛶ in the tab bar opens the
 * page in a popup window (no tabs, toolbar or bookmarks), sized like the current window. The tab it came from stops
 * its player and shows „Televize běží v samostatném okně." with „Vrátit sem". In the popup the button is ⧈ „Zpět do
 * prohlížeče": it closes the popup and the tab takes over again; the popup's own ✕ does the same. The windows talk
 * over BroadcastChannel('televize') (same origin); the opener also watches its handle of the popup (`closed`), so a
 * browser without the channel still gets its tab back. Hidden on phones and tablets (they open a tab, not a popup).
 * Opened from the player (⧉ also in the player bar) it moves playback: the tab hands what plays (request, position,
 * timeline point) to the popup once it announces itself, then stops; the popup starts it.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var NAME = 'televize'; // the popup's window name (window.open target) and the channel's name
  var ICON_OPEN = '⧉';
  var ICON_BACK = '⧈';
  var OPEN = 'Samostatné okno';
  var BACK = 'Zpět do prohlížeče';
  var ELSEWHERE = 'Televize běží v samostatném okně.';
  var RETURN = 'Vrátit sem';
  var BLOCKED = 'Prohlížeč samostatné okno zablokoval – povolte vyskakovací okna pro tuto stránku.';
  var POLL_MS = 1000; // how often the opener looks whether its popup was closed

  /** Phones and tablets (a coarse primary pointer, iPhone/iPad, iPadOS reporting itself as a Mac). */
  function touchDevice(win) {
    var nav = win.navigator || {};
    if (/iPhone|iPad|iPod/.test(nav.userAgent || '')) return true;
    if (nav.platform === 'MacIntel' && nav.maxTouchPoints > 1) return true;
    return !!(win.matchMedia && win.matchMedia('(pointer: coarse)').matches);
  }

  /** Whether this page is the popup (window names survive a reload of the popup). */
  function isPopup(win) {
    return win.name === NAME;
  }

  /** window.open features: a popup at the current window's place and size. */
  function features(win) {
    var n = function (v, fallback) { return typeof v === 'number' && v > 0 ? Math.round(v) : fallback; };
    var parts = ['popup', 'width=' + n(win.innerWidth, 1280), 'height=' + n(win.innerHeight, 720)];
    if (typeof win.screenX === 'number') parts.push('left=' + Math.round(win.screenX));
    if (typeof win.screenY === 'number') parts.push('top=' + Math.round(win.screenY));
    return parts.join(',');
  }

  /**
   * The coordination of one page. `win` = window (or a fake with name, location, open, close, focus, opener,
   * BroadcastChannel, addEventListener, setInterval/clearInterval). `hooks` = {stopPlayer(), render(controller),
   * toast(text), playing() → what plays now or null (taken over by the popup), play(what) in the popup}.
   * `popup`: this page is the popup; `elsewhere`: this tab gave the app to a popup.
   */
  function Controller(win, hooks) {
    this.win = win;
    this.hooks = hooks || {};
    this.popup = isPopup(win);
    this.available = !touchDevice(win) && typeof win.open === 'function';
    this.elsewhere = false;
    this.handle = null; // the popup this tab opened
    this.handoff = null; // what played here when the popup was opened, sent once it announces itself
    this.played = false; // the popup started what it was handed (once; not again after a reload)
    this.timer = null;
    this.channel = null;
    var Channel = win.BroadcastChannel;
    if (Channel) {
      try {
        this.channel = new Channel(NAME);
      } catch (e) {
        this.channel = null;
      }
    }
  }

  Controller.prototype.post = function (type, play) {
    if (!this.channel) return;
    try {
      this.channel.postMessage(play ? { type: type, play: play } : { type: type });
    } catch (e) { /* closed channel: nothing to tell */ }
  };

  /** Listens to the other windows; the popup announces itself and says when it goes away (also by ✕). */
  Controller.prototype.start = function () {
    var self = this;
    if (this.channel) {
      this.channel.onmessage = function (e) { self.onMessage(e && e.data ? e.data : {}); };
    }
    if (this.popup) {
      var gone = function () { self.post('closed'); };
      this.win.addEventListener('pagehide', gone);
      this.win.addEventListener('beforeunload', gone);
      this.post('opened');
    } else {
      this.post('ask'); // a reloaded tab: is a popup already running?
    }
    this.render();
    return this;
  };

  Controller.prototype.onMessage = function (data) {
    var type = data.type;
    if (this.popup) {
      if (type === 'ask') this.post('opened');
      else if (type === 'return') this.win.close();
      else if (type === 'play' && data.play && !this.played) {
        this.played = true;
        if (this.hooks.play) this.hooks.play(data.play);
      }
      return;
    }
    if (type === 'opened') {
      if (this.handoff) this.post('play', this.handoff); // only the tab that opened it from the player
      this.handoff = null;
      this.setElsewhere(true);
    }
    else if (type === 'closed' || type === 'back') this.setElsewhere(false);
  };

  /** The button: open the popup (a tab) or go back to the browser (the popup). */
  Controller.prototype.toggle = function () {
    return this.popup ? this.back() : this.open();
  };

  /** From a tab: the popup, or the blocked message. Returns 'opened' | 'blocked' | null (not offered here). */
  Controller.prototype.open = function () {
    if (!this.available || this.popup) return null;
    var w = null;
    try {
      w = this.win.open(this.win.location.href, NAME, features(this.win));
    } catch (e) {
      w = null;
    }
    if (!w) {
      if (this.hooks.toast) this.hooks.toast(BLOCKED);
      return 'blocked';
    }
    this.handle = w;
    this.handoff = this.hooks.playing ? this.hooks.playing() : null; // before the player stops
    this.watch();
    this.setElsewhere(true);
    return 'opened';
  };

  /** From the popup: the tab takes over, the popup closes. Without its tab (closed meanwhile) the page opens in one. */
  Controller.prototype.back = function () {
    if (!this.popup) return null;
    var opener = null;
    try {
      opener = this.win.opener && !this.win.opener.closed ? this.win.opener : null;
    } catch (e) {
      opener = null;
    }
    this.post('back');
    if (opener) {
      try {
        opener.focus();
      } catch (e) { /* another origin or gone */ }
    } else {
      this.win.open(this.win.location.href, '_blank');
    }
    this.win.close();
    return 'back';
  };

  /** In the tab: „Vrátit sem" closes the popup (by its handle, or by asking it) and the line goes. */
  Controller.prototype.returnHere = function () {
    if (this.popup) return;
    if (this.handle && !this.handle.closed) this.handle.close();
    this.post('return');
    this.setElsewhere(false);
    try {
      this.win.focus();
    } catch (e) { /* nothing to focus */ }
  };

  Controller.prototype.watch = function () {
    var self = this;
    if (this.timer || !this.win.setInterval) return;
    this.timer = this.win.setInterval(function () { self.poll(); }, POLL_MS);
  };

  /** The popup closed without telling (no channel, or a crash): the tab takes over. */
  Controller.prototype.poll = function () {
    if (this.handle && !this.handle.closed) return;
    this.handle = null;
    if (this.timer) this.win.clearInterval(this.timer);
    this.timer = null;
    this.setElsewhere(false);
  };

  Controller.prototype.setElsewhere = function (on) {
    if (this.popup) return;
    on = !!on;
    if (on && this.hooks.stopPlayer) this.hooks.stopPlayer(); // stopped also when it was already on (a reload)
    if (on === this.elsewhere) return;
    this.elsewhere = on;
    this.render();
  };

  Controller.prototype.render = function () {
    if (this.hooks.render) this.hooks.render(this);
  };

  /** The button's icon and label for this page. */
  Controller.prototype.label = function () {
    return this.popup ? BACK : OPEN;
  };

  Controller.prototype.icon = function () {
    return this.popup ? ICON_BACK : ICON_OPEN;
  };

  /** Shows/labels the buttons (tab bar, player bar; hidden on touch devices, never in the popup) and the tab's line. */
  function render(controller, buttons, line) {
    var shown = controller.popup || controller.available;
    (buttons || []).forEach(function (button) {
      if (!button) return;
      var text = controller.label();
      button.classList.toggle('hidden', !shown);
      button.classList.toggle('web-window-back', controller.popup);
      button.textContent = controller.icon() + ' ' + text;
      button.setAttribute('title', text);
      button.setAttribute('aria-label', text);
    });
    if (line) line.classList.toggle('visible', controller.elsewhere);
  }

  CT.WebWindow = { NAME: NAME, ICON_OPEN: ICON_OPEN, ICON_BACK: ICON_BACK, OPEN: OPEN, BACK: BACK,
    ELSEWHERE: ELSEWHERE, RETURN: RETURN, BLOCKED: BLOCKED, POLL_MS: POLL_MS, touchDevice: touchDevice,
    isPopup: isPopup, features: features, Controller: Controller, render: render };
})(typeof window !== 'undefined' ? window : global);
