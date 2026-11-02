/*
 * Always on top (spec/web.md "Always on top (T48)"): the player bar's 📌 „Vždy navrchu" and the key P put the
 * picture into a Picture-in-Picture window, which stays above all other windows.
 * - Document Picture-in-Picture (Chrome/Edge 116+, secure context only): the playing <video> element itself is moved
 *   into the small window (never a second stream; hls.js stays attached), with the player's message and a bar
 *   (⏸/▶, 🔊/🔇, „Zpět do okna"). The page shows „Přehrává se v okně navrchu." with „Vrátit sem". Closing the window
 *   in any way puts the element back where it was, playing.
 * - Video Picture-in-Picture where only `video.requestPictureInPicture` exists (Safari, Chrome over plain HTTP):
 *   the browser's own picture window; the page keeps its player.
 * - Neither (Firefox): the button is hidden.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var ICON = '📌';
  var OPEN = 'Vždy navrchu';
  var BACK = 'Zpět do okna';
  var PLACEHOLDER = 'Přehrává se v okně navrchu.';
  var RETURN = 'Vrátit sem';
  var FAILED = 'Okno navrchu se nepodařilo otevřít.';
  var HTTPS_HINT = 'Navrchu je jen obraz. Okno s ovládáním navrchu má Chrome a Edge na adrese https://…:8443/web/.';
  var WIDTH = 480; // the window's width in CSS px; its height follows the video's aspect
  var ASPECT = 9 / 16; // before the video knows its size
  var MIN_CHROMIUM = 116; // Document Picture-in-Picture

  /** 'document' | 'video' | 'none': what this browser can put on top. */
  function mode(win, video) {
    var dpip = win.documentPictureInPicture;
    if (win.isSecureContext !== false && dpip && typeof dpip.requestWindow === 'function') return 'document';
    var doc = video && video.ownerDocument;
    if (video && typeof video.requestPictureInPicture === 'function' && !(doc && doc.pictureInPictureEnabled === false) &&
      !video.disablePictureInPicture) return 'video';
    return 'none';
  }

  /** A Chromium 116+ that lacks Document PiP only because the page is plain HTTP: it would have it over HTTPS. */
  function httpsWouldHelp(win) {
    if (win.isSecureContext !== false) return false;
    var ua = (win.navigator && win.navigator.userAgent) || '';
    var m = /(?:Chrome|Chromium|Edg)\/(\d+)/.exec(ua);
    return !!m && parseInt(m[1], 10) >= MIN_CHROMIUM;
  }

  /** The window's size for a video of `w` × `h` px (0 = unknown). */
  function size(w, h) {
    var ratio = w > 0 && h > 0 ? h / w : ASPECT;
    return { width: WIDTH, height: Math.round(WIDTH * ratio) };
  }

  /** P (not while typing, not a browser shortcut). */
  function isPipKey(event, activeTag) {
    if (event.ctName || event.ctrlKey || event.metaKey || event.altKey) return false;
    if (activeTag === 'INPUT' || activeTag === 'TEXTAREA') return false;
    return event.key === 'p' || event.key === 'P' || (!event.key && event.keyCode === 80);
  }

  /**
   * `win` = window, `hooks` = {video() → the <video>, playerOpen(), playPause(), toggleMute(), muted(), message() →
   * the player's message text or '', toast(text), render(controller), cssUrl: the window's stylesheet (absolute)}.
   * `state`: 'off' | 'opening' | 'document' | 'video'.
   */
  function Controller(win, hooks) {
    this.win = win;
    this.hooks = hooks || {};
    this.state = 'off';
    this.pipWin = null;
    this.home = null; // {parent, next}: where the element goes back to
    this.ui = null; // the window's own elements
    this.hinted = false;
    var self = this;
    var video = this.video();
    if (video && video.addEventListener) {
      video.addEventListener('leavepictureinpicture', function () {
        if (self.state === 'video') self.set('off');
      });
      ['play', 'pause', 'playing', 'volumechange'].forEach(function (name) {
        video.addEventListener(name, function () { self.renderWindow(); });
      });
    }
  }

  Controller.prototype.video = function () {
    return this.hooks.video ? this.hooks.video() : null;
  };

  Controller.prototype.mode = function () {
    return mode(this.win, this.video());
  };

  /** Whether the button is offered: a way exists and something plays. */
  Controller.prototype.available = function () {
    return this.mode() !== 'none';
  };

  /** The page's placeholder is up (the picture is in the Document PiP window, or on its way there). */
  Controller.prototype.away = function () {
    return this.state === 'document' || this.state === 'opening';
  };

  Controller.prototype.set = function (state) {
    this.state = state;
    if (this.hooks.render) this.hooks.render(this);
  };

  Controller.prototype.toggle = function () {
    return this.state === 'off' ? this.open() : this.back();
  };

  /** From a user gesture (click, P). Returns 'document' | 'video' | null (nothing to put on top). */
  Controller.prototype.open = function () {
    var self = this;
    if (this.state !== 'off' || !(this.hooks.playerOpen && this.hooks.playerOpen())) return null;
    var how = this.mode();
    var video = this.video();
    var request;
    if (how === 'document') {
      var s = size(video.videoWidth, video.videoHeight);
      try {
        request = this.win.documentPictureInPicture.requestWindow({ width: s.width, height: s.height });
      } catch (e) {
        request = Promise.reject(e);
      }
      this.set('opening');
      request.then(function (pipWin) { self.attach(pipWin); }, function () { self.failed(); });
      return 'document';
    }
    if (how === 'video') {
      try {
        request = video.requestPictureInPicture();
      } catch (e) {
        request = Promise.reject(e);
      }
      request.then(function () {
        self.set('video');
        if (!self.hinted && httpsWouldHelp(self.win) && self.hooks.toast) {
          self.hinted = true;
          self.hooks.toast(HTTPS_HINT);
        }
      }, function () { self.failed(); });
      return 'video';
    }
    return null;
  };

  Controller.prototype.failed = function () {
    this.set('off');
    if (this.hooks.toast) this.hooks.toast(FAILED);
  };

  /** The Document PiP window is there: its page, then the element moved in. */
  Controller.prototype.attach = function (pipWin) {
    var self = this;
    var video = this.video();
    if (this.state !== 'opening' || !video || !(this.hooks.playerOpen && this.hooks.playerOpen())) {
      try {
        pipWin.close(); // the player closed meanwhile
      } catch (e) { /* gone */ }
      if (this.state === 'opening') this.set('off');
      return;
    }
    this.pipWin = pipWin;
    this.ui = build(pipWin.document, this.hooks.cssUrl);
    this.ui.playPause.addEventListener('click', function () { self.playPause(); });
    this.ui.mute.addEventListener('click', function () { self.toggleMute(); });
    this.ui.back.addEventListener('click', function () { self.back(); });
    pipWin.document.addEventListener('keydown', function (e) { self.onKey(e); });
    pipWin.addEventListener('pagehide', function () { self.restore(); });
    this.home = { parent: video.parentNode, next: video.nextSibling };
    var playing = !video.paused;
    this.ui.stage.appendChild(video); // moved, not copied: one element, one stream
    if (playing && video.paused) playVideo(video);
    this.set('document');
    this.renderWindow();
  };

  /** The element back into the player (the window closed or is closing), still playing if it was. */
  Controller.prototype.restore = function () {
    if (this.state !== 'document') return;
    var video = this.video();
    var home = this.home;
    this.pipWin = null;
    this.ui = null;
    this.home = null;
    if (video && home && home.parent) {
      var playing = !video.paused;
      var next = home.next && home.next.parentNode === home.parent ? home.next : null;
      home.parent.insertBefore(video, next);
      if (playing && video.paused) playVideo(video);
    }
    this.set('off');
  };

  /** „Zpět do okna", „Vrátit sem", P: the picture back into the page. */
  Controller.prototype.back = function () {
    if (this.state === 'document') {
      var pipWin = this.pipWin;
      this.restore();
      try {
        if (pipWin && !pipWin.closed) pipWin.close();
      } catch (e) { /* gone */ }
      return 'back';
    }
    if (this.state === 'video') {
      var doc = this.video() ? this.video().ownerDocument : null;
      if (doc && doc.pictureInPictureElement && doc.exitPictureInPicture) {
        var p = doc.exitPictureInPicture();
        if (p && p.catch) p.catch(function () {});
      }
      this.set('off');
      return 'back';
    }
    if (this.state === 'opening') this.set('off'); // attach() then closes the window it gets
    return null;
  };

  /** The player closed (ended, T46 popup took over, Back): nothing stays on top. */
  Controller.prototype.playerClosed = function () {
    if (this.state !== 'off') this.back();
  };

  Controller.prototype.playPause = function () {
    if (this.hooks.playPause) this.hooks.playPause();
    this.renderWindow();
  };

  Controller.prototype.toggleMute = function () {
    if (this.hooks.toggleMute) this.hooks.toggleMute();
    this.renderWindow();
  };

  /** Keys in the window: Space play/pause, M mute, P or Esc back. */
  Controller.prototype.onKey = function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var k = e.key;
    if (k === ' ' || k === 'Spacebar' || k === 'MediaPlayPause') this.playPause();
    else if (k === 'm' || k === 'M') this.toggleMute();
    else if (k === 'p' || k === 'P' || k === 'Escape' || k === 'Esc') this.back();
    else return;
    if (e.preventDefault) e.preventDefault();
  };

  /** The window's bar and message for the player's state now. */
  Controller.prototype.renderWindow = function () {
    var ui = this.ui;
    var video = this.video();
    if (!ui || !video) return;
    var paused = !!video.paused;
    var pause = paused ? 'Přehrát' : 'Pozastavit';
    ui.playPause.textContent = paused ? '▶' : '⏸';
    ui.playPause.setAttribute('title', pause);
    ui.playPause.setAttribute('aria-label', pause);
    var muted = this.hooks.muted ? !!this.hooks.muted() : !!video.muted;
    var mute = CT.Mute ? CT.Mute.label(muted) : (muted ? 'Zapnout zvuk' : 'Vypnout zvuk');
    ui.mute.textContent = muted ? '🔇' : '🔊';
    ui.mute.setAttribute('title', mute);
    ui.mute.setAttribute('aria-label', mute);
    var text = this.hooks.message ? this.hooks.message() || '' : '';
    ui.message.textContent = text;
    ui.message.classList.toggle('visible', !!text);
  };

  function playVideo(video) {
    try {
      var p = video.play();
      if (p && p.catch) p.catch(function () {});
    } catch (e) { /* refused: stays paused */ }
  }

  /** The Document PiP window's page (its own stylesheet; the gateway's CSP allows only 'self' styles). */
  function build(doc, cssUrl) {
    var mk = function (tag, cls, text) {
      var e = doc.createElement(tag);
      if (cls) e.className = cls;
      if (text !== undefined) e.textContent = text;
      return e;
    };
    doc.title = 'Televize';
    if (cssUrl) {
      var link = doc.createElement('link');
      link.rel = 'stylesheet';
      link.href = cssUrl;
      doc.head.appendChild(link);
    }
    doc.body.className = 'web-pip-window';
    doc.documentElement.setAttribute('lang', 'cs');
    var stage = mk('div', 'pip-stage');
    var message = mk('div', 'pip-message');
    var bar = mk('div', 'pip-bar');
    var playPause = mk('button', 'pip-button pip-playpause');
    var mute = mk('button', 'pip-button pip-mute');
    var back = mk('button', 'pip-button pip-back', BACK);
    playPause.type = mute.type = back.type = 'button';
    bar.appendChild(playPause);
    bar.appendChild(mute);
    bar.appendChild(back);
    doc.body.appendChild(stage);
    doc.body.appendChild(message);
    doc.body.appendChild(bar);
    return { stage: stage, message: message, bar: bar, playPause: playPause, mute: mute, back: back };
  }

  /** The player bar's buttons (hidden where nothing can go on top) and the page's placeholder. */
  function render(controller, buttons, line) {
    var shown = controller.available();
    var on = controller.state !== 'off';
    var text = on ? BACK : OPEN;
    (buttons || []).forEach(function (button) {
      if (!button) return;
      button.classList.toggle('hidden', !shown);
      button.classList.toggle('web-pip-on', on);
      button.textContent = ICON + ' ' + text;
      button.setAttribute('title', text);
      button.setAttribute('aria-label', text);
    });
    if (line) line.classList.toggle('visible', controller.away());
  }

  CT.WebPip = { ICON: ICON, OPEN: OPEN, BACK: BACK, PLACEHOLDER: PLACEHOLDER, RETURN: RETURN, FAILED: FAILED,
    HTTPS_HINT: HTTPS_HINT, WIDTH: WIDTH, mode: mode, httpsWouldHelp: httpsWouldHelp, size: size, isPipKey: isPipKey,
    Controller: Controller, build: build, render: render };
})(typeof window !== 'undefined' ? window : global);
