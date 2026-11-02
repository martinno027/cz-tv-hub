/*
 * Input on the web (spec/web.md "Input (T29)"): the keyboard is the TV remote (its focus shown only while it is
 * used); the mouse and touch work as on any web page: hovering only highlights (CSS), one click acts (web-focus.js
 * adapters: the item becomes the screen's focus and gets OK), right click / long press is long OK, the wheel / a
 * trackpad / a swipe scroll without moving the focus (web-scroll.js), "‹ Zpět" links and the browser's Back go
 * through the history (web-history.js). In the player a mouse move / tap shows the bar, dragging the bar seeks, a
 * double click toggles fullscreen. OK, Back and the other remote actions go through the app's own key handlers
 * (synthetic keydown/keyup events carrying the key name), so the screens stay the Samsung ones.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var LONG_PRESS_MS = 600; // a touch held this long without moving is long OK
  var MOVE_PX = 12; // a press that moved this far is a swipe or a drag, not a tap
  /** `KeyboardEvent.key` → the code the Samsung table knows (events without a keyCode, e.g. some synthetic ones). */
  var KEYS = { ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40, Enter: 13, Escape: 27, Esc: 27,
    Backspace: 8, ' ': 32, Spacebar: 32, MediaPlayPause: 179, PageUp: 33, PageDown: 34 };
  for (var digit = 0; digit <= 9; digit++) KEYS[String(digit)] = 48 + digit;
  var BAR_SLOP_PX = 24; // screen px above and below the player's bar that still grab it

  /**
   * The remote key name of a keyboard event (CT.Platform.keyName): the Samsung codes, plus Space = play/pause,
 * A = AUDIO and M = mute (the Samsung's yellow key).
   * Browser shortcuts (Ctrl, Cmd, Alt — Alt+← is the browser's Back) stay the browser's; while typing, Backspace and
   * Space belong to the field. Synthetic events of this module carry their name in `ctName`.
   */
  function keyName(event, codes, activeTag) {
    if (event.ctName) return event.ctName;
    if (event.ctrlKey || event.metaKey || event.altKey) return null;
    var typing = activeTag === 'INPUT' || activeTag === 'TEXTAREA';
    var code = event.keyCode || KEYS[event.key] || 0;
    if (typing && (code === 8 || code === 32)) return null;
    if (code === 32 || code === 179) return 'playpause';
    if (!typing && (event.key === 'a' || event.key === 'A' || (!event.key && code === 65))) return 'audio'; // AUDIO
    if (!typing && (event.key === 'm' || event.key === 'M' || (!event.key && code === 77))) return 'mute'; // T34
    return codes[code] || null;
  }

  /** The key F (fullscreen), not while typing and not with a browser shortcut's modifier. */
  function isFullscreenKey(event, activeTag) {
    if (event.ctName || event.ctrlKey || event.metaKey || event.altKey) return false;
    if (activeTag === 'INPUT' || activeTag === 'TEXTAREA') return false;
    return event.key === 'f' || event.key === 'F' || (!event.key && event.keyCode === 70);
  }

  /** 0…1: where `x` lies on a bar `rect`. */
  function barFraction(x, rect) {
    if (!rect || !(rect.right > rect.left)) return 0;
    return Math.max(0, Math.min(1, (x - rect.left) / (rect.right - rect.left)));
  }

  function visible(el) {
    return !!el && el.getClientRects().length > 0;
  }

  /**
   * Wires the page: `doc` = document, `app` = CT.App, `opts` = {history: the browser's history (Back links),
   * window: the own-window controller (web-window.js, T46; optional), pip: the always-on-top controller (web-pip.js,
   * T48; optional),
   * scale() → the stage's scale, onChange() after anything that may open or close a layer}. Returns the handlers.
   */
  function install(doc, app, opts) {
    var $ = function (id) { return doc.getElementById(id); };
    var F = CT.WebFocus;
    var mode = new F.Mode();
    var press = null; // {x, y, sx, sy, type, target, moved, long, timer, player, drag, view}
    var views = CT.WebScroll.views(app, doc, opts.scale);
    var lastOsdAt = 0;
    var hover = new F.HoverDetail(app.screens.guide, {
      set: function (fn, ms) { return setTimeout(fn, ms); },
      clear: function (id) { clearTimeout(id); }
    });
    var changed = function () { if (opts.onChange) setTimeout(opts.onChange, 0); };

    function send(name, type) {
      var e;
      try {
        e = new root.Event(type || 'keydown', { bubbles: true, cancelable: true });
      } catch (err) {
        e = doc.createEvent('Event');
        e.initEvent(type || 'keydown', true, true);
      }
      e.ctName = name;
      doc.dispatchEvent(e);
    }

    function ok() {
      send('enter', 'keydown');
      send('enter', 'keyup');
    }

    function editing() {
      var a = doc.activeElement;
      return !!a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA');
    }

    function activeTag() {
      return doc.activeElement ? doc.activeElement.tagName : '';
    }

    function pointerUsed() {
      if (mode.onPointer()) F.hideFocus(doc);
    }

    /** A tab by click/tap: an open Nastavení section closes first (on the TV only Back leaves it). */
    function selectTab(tab) {
      if (app.programme && app.programme.isOpen()) return;
      var settings = app.screens && app.screens.settings;
      if (settings && settings.zone !== 'choice') settings.closeSection();
      app.zone = 'tabs';
      if (app.tab === tab) app.applyFocus();
      else app.showTab(tab);
      app.zone = 'content'; // a click needs no second step into the screen
      if (mode.pointer) F.hideFocus(doc);
    }

    function playerOpen() {
      return !!app.player && app.player.isOpen();
    }

    function osdVisible() {
      return $('osd').classList.contains('visible');
    }

    function fullscreen() {
      CT.WebFullscreen.toggle(doc);
    }

    // -- keys ---------------------------------------------------------------------------------------------------

    /** Before the app's own handler: F, and the first key after the mouse only shows the focus. */
    function onKeyDown(e) {
      if (e.ctName) return;
      if (isFullscreenKey(e, activeTag())) {
        e.preventDefault();
        fullscreen();
        return;
      }
      var name = CT.Keys.name(e);
      if (!name || editing()) return;
      var r = mode.onKey(name);
      if (r.reveal) F.showFocus(app);
      if (!r.act) {
        e.preventDefault();
        e.stopPropagation();
      }
    }

    // -- presses ------------------------------------------------------------------------------------------------

    /** The bar under (or within BAR_SLOP_PX of) the pointer: the plain bar is only 8 px high on the TV. */
    function barAt(x, y) {
      if (!osdVisible()) return null;
      var bars = [$('osd-bar'), $('tl-track')];
      for (var i = 0; i < bars.length; i++) {
        if (!visible(bars[i]) || bars[i].style.visibility === 'hidden') continue;
        var r = bars[i].getBoundingClientRect();
        if (x >= r.left && x <= r.right && y >= r.top - BAR_SLOP_PX && y <= r.bottom + BAR_SLOP_PX) return bars[i];
      }
      return null;
    }

    function onDown(e) {
      if (e.pointerType === 'mouse' && e.button !== 0) return; // right click: contextmenu = long OK
      pointerUsed();
      hover.cancel();
      var target = e.target;
      if (editing() && target === doc.activeElement) return; // typing: the caret is the field's
      if (target.tagName === 'INPUT') e.preventDefault(); // the field opens through its screen (OK), not natively
      press = { x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY, type: e.pointerType, target: target,
        moved: false, long: false, timer: null, player: playerOpen(), drag: null,
        view: e.pointerType !== 'mouse' ? CT.WebScroll.viewAt(target) : null };
      if (press.player) {
        var bar = barAt(e.clientX, e.clientY);
        if (bar) {
          e.preventDefault();
          press.drag = bar;
          dragTo(bar, e.clientX, false);
          return;
        }
      }
      if (e.pointerType !== 'mouse') {
        var p = press;
        p.timer = setTimeout(function () {
          if (press !== p || p.moved) return;
          p.long = true;
          longOk(p.target);
        }, LONG_PRESS_MS);
      }
    }

    function onMove(e) {
      var p = press;
      if (!p) {
        if (e.pointerType !== 'mouse') return;
        if (playerOpen()) {
          if (Date.now() - lastOsdAt > 300) {
            lastOsdAt = Date.now();
            app.player.showOsd();
          }
          return;
        }
        guideHover(e.target);
        return;
      }
      if (p.drag) return dragTo(p.drag, e.clientX, false);
      if (!p.moved && Math.abs(e.clientX - p.sx) < MOVE_PX && Math.abs(e.clientY - p.sy) < MOVE_PX) return;
      p.moved = true;
      clearTimeout(p.timer);
      if (p.view && views[p.view]) views[p.view](p.x - e.clientX, p.y - e.clientY); // the finger drags the content
      p.x = e.clientX;
      p.y = e.clientY;
    }

    /** The guide's detail follows the resting mouse; outside the grid it shows the focus again. */
    function guideHover(target) {
      var inGrid = app.tab === 'guide' && !(app.programme && app.programme.isOpen()) && target.closest &&
        target.closest('#grid');
      if (!inGrid) return hover.out();
      hover.over(target.closest('#grid-rows .cell'));
    }

    function onUp(e) {
      var p = press;
      press = null;
      if (!p) return;
      clearTimeout(p.timer);
      if (p.drag) {
        dragTo(p.drag, e.clientX, true);
        return;
      }
      if (p.long || p.moved) return;
      click(p.target);
      changed();
    }

    /** One click/tap on `target`: what it means where it is. */
    function click(target) {
      var on = function (selector) { return target.closest ? target.closest(selector) : null; };
      var own = opts.window;
      if (own && own.elsewhere) return on('.web-elsewhere-return') ? own.returnHere() : undefined; // T46
      var pip = opts.pip;
      if (pip && pip.away()) return on('.web-pip-return') ? pip.back() : undefined; // T48: the page's line
      if (on('.web-pip') && pip) return pip.toggle();
      if (on('.web-window') && own) return own.toggle();
      if (on('.web-fullscreen')) return fullscreen();
      if (on('.web-mute') && playerOpen()) return muteClick();
      if (on('.web-back-link')) return opts.history.back();
      var tab = on('.tab');
      if (tab) return selectTab(tab.getAttribute('data-tab'));
      if (playerOpen()) return playerTap(target, on);
      if (editing() && on('input') === doc.activeElement) return;
      F.act(app, target, false, ok);
    }

    /**
     * In the player OK is a click on what OK presses there (the error's button, "Přeskočit reklamu", a track of the
     * audio list); a click on the picture is OK (pause) only while the bar is up, else it shows the bar. Under an
     * error only its button runs it, so a stray tap never leaves for iVysílání.
     */
    function playerTap(target, on) {
      var player = app.player;
      if (on('#player-message .button')) return player.errorAction ? ok() : undefined;
      if (on('#skip-ad') && $('skip-ad').classList.contains('visible')) return ok();
      var item = on('.audio-item');
      var tl = player.tl;
      if (item && tl && tl.audioList) {
        tl.audioList.focus = F.indexIn(item);
        return ok();
      }
      if ($('audio-list').classList.contains('visible')) return undefined;
      if (!osdVisible() || player.errorAction) return player.showOsd();
      return ok();
    }

    /** The player bar's 🔊/🔇: the player's own mute (the same as M), the bar stays up. */
    function muteClick() {
      app.player.toggleMute();
      app.player.showOsd();
    }

    /** Right click / long press: long OK where the screen has one (Archiv cards, episodes, the timeline's audio). */
    function longOk(target) {
      if (playerOpen()) {
        var tl = app.player.tl;
        if (tl && tl.tl) tl.openAudioList();
        return;
      }
      F.act(app, target, true, ok);
      changed();
    }

    function onCancel() {
      var p = press;
      press = null;
      if (!p) return;
      clearTimeout(p.timer);
      if (p.drag && app.player.tl) {
        app.player.tl.scrub = null;
        app.player.tl.render();
      }
    }

    // -- the player's bar ---------------------------------------------------------------------------------------

    /** Dragging the bar: the timeline shows the chosen moment and seeks on release; the plain bar seeks at once. */
    function dragTo(bar, x, done) {
      var player = app.player;
      if (!player.isOpen() || !player.target) return;
      var fraction = barFraction(x, bar.getBoundingClientRect());
      var tl = player.tl;
      if (tl && tl.tl) {
        var a = tl.axis(tl.playheadTime());
        var time = a.start + fraction * (a.end - a.start);
        if (done) {
          tl.scrub = null;
          tl.seekToTime(time);
        } else {
          tl.scrub = CT.Timeline.toPosition(tl.tl.pieces, time);
          tl.render();
        }
        tl.showBar();
        return;
      }
      if (player.target.kind === 'live') return;
      var duration = player.backend.duration();
      if (duration > 0 && done) player.backend.seekTo(fraction * duration);
      if (duration > 0 && !done) $('osd-progress').style.width = fraction * 100 + '%';
      player.showOsd();
    }

    // -- wheel --------------------------------------------------------------------------------------------------

    /** Over a virtual view (guide grid, Archiv grid/menu, episodes) the view moves; elsewhere the browser scrolls. */
    function onWheel(e) {
      if (playerOpen()) return;
      pointerUsed();
      var view = CT.WebScroll.viewAt(e.target);
      if (!view || !views[view]) return;
      e.preventDefault();
      var d = CT.WebScroll.wheelDelta(e);
      views[view](d.x, d.y);
    }

    // While the mouse is used, screens that redraw (Živě every 30 s, the guide on every move) must not bring the
    // remote's focus back.
    if (root.MutationObserver) {
      new root.MutationObserver(function () {
        if (mode.pointer && doc.querySelector('.focused')) F.hideFocus(doc);
      }).observe(doc.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
    }
    doc.addEventListener('keydown', onKeyDown, true);
    doc.addEventListener('pointerdown', onDown, true);
    doc.addEventListener('pointermove', onMove, true);
    doc.addEventListener('pointerup', onUp, true);
    doc.addEventListener('pointercancel', onCancel, true);
    doc.addEventListener('wheel', onWheel, { capture: true, passive: false });
    doc.addEventListener('dblclick', function (e) {
      if (opts.pip && opts.pip.away()) return; // the picture is in the window on top (T48)
      if (!playerOpen() || !e.target.closest || e.target.closest('#osd, #audio-list, #skip-ad, #player-message .button')) return;
      fullscreen();
    }, true);
    // right click = long OK (and no context menu: nothing to save or copy)
    doc.addEventListener('contextmenu', function (e) {
      e.preventDefault();
      if (e.pointerType && e.pointerType !== 'mouse') return; // a long touch press is handled by its timer
      pointerUsed();
      longOk(e.target);
    }, true);
    return { send: send, ok: ok, click: click, mode: mode };
  }

  CT.WebInput = {
    keyName: keyName,
    isFullscreenKey: isFullscreenKey,
    barFraction: barFraction,
    install: install
  };
})(typeof window !== 'undefined' ? window : global);
