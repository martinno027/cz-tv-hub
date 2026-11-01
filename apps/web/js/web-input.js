/*
 * Input on the web (spec/web.md "Input"): the keyboard is the TV remote, and mouse/touch drive the same remote
 * actions instead of a second set of handlers. Pointing at a focusable element walks the focus there with arrow
 * presses (as the remote would), a click/tap is OK on it, a long press is a held OK (repeated keydowns, then the
 * keyup), the wheel / a swipe is arrow presses, "‹ Zpět" is Back. In the player a mouse move / tap shows the bar and
 * dragging the bar seeks. Every action goes through the app's own key handlers (synthetic keydown/keyup events
 * carrying the key name), so the screens stay the Samsung ones.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  /** What the pointer can focus (the elements the screens mark `.focused`). */
  var FOCUSABLE = '.card, .cell, .day, .button, .choice, .ch-name, .ch-btn, .setting-toggle, .ar-menu-item, ' +
    '.ar-card, .sh-ep, .audio-item, #ar-search, .field input';
  /** Where OK can be held for "long OK" (archive-cards.js LongPress); a new long OK place goes here too. */
  var LONG_OK = '.ar-card, .sh-ep';
  var LONG_OK_MS = 800; // archive-cards.js / timeline.js
  var HOLD_AFTER_MS = 350; // a press this long without moving is a hold (touch: not the start of a swipe)
  var REPEAT_MS = 100; // keydown repeats of a held OK (under the 800 ms release fallback of the screens)
  var REPEAT_UNTIL_MS = LONG_OK_MS + 150; // then quiet until the release: the long OK has fired
  var MOVE_PX = 12; // a press that moved this far is a swipe or a drag, not a tap
  var SWIPE_STEP_PX = 70;
  var WHEEL_STEP = 80; // pixels of wheel delta per arrow press
  var WALK_MAX = 40;
  /** `KeyboardEvent.key` → the code the Samsung table knows (events without a keyCode, e.g. some synthetic ones). */
  var KEYS = { ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40, Enter: 13, Escape: 27, Esc: 27,
    Backspace: 8, ' ': 32, Spacebar: 32, MediaPlayPause: 179, PageUp: 33, PageDown: 34 };
  for (var digit = 0; digit <= 9; digit++) KEYS[String(digit)] = 48 + digit;
  var BAR_SLOP_PX = 24; // screen px above and below the player's bar that still grab it

  /**
   * The remote key name of a keyboard event (CT.Platform.keyName): the Samsung codes, plus Space = play/pause.
   * Browser shortcuts (Ctrl, Cmd, Alt) stay the browser's; while typing, Backspace and Space belong to the field.
   * Synthetic events of this module carry their name in `ctName`.
   */
  function keyName(event, codes, activeTag) {
    if (event.ctName) return event.ctName;
    if (event.ctrlKey || event.metaKey || event.altKey) return null;
    var typing = activeTag === 'INPUT' || activeTag === 'TEXTAREA';
    var code = event.keyCode || KEYS[event.key] || 0;
    if (typing && (code === 8 || code === 32)) return null;
    if (code === 32 || code === 179) return 'playpause';
    return codes[code] || null;
  }

  function centerX(r) {
    return (r.left + r.right) / 2;
  }

  function centerY(r) {
    return (r.top + r.bottom) / 2;
  }

  /**
   * The arrow that brings the focus from rect `f` towards rect `t`: along a row when they share one, or when their
   * regions (the children of their common ancestor, `fr`/`tr`) stand side by side (the Archiv menu and its grid);
   * else up/down (lists, the guide's rows: up/down keep the focused time, then left/right finish on the row).
   */
  function direction(f, t, fr, tr) {
    var rowOverlap = Math.min(f.bottom, t.bottom) - Math.max(f.top, t.top);
    var sideBySide = !!(fr && tr) && Math.min(fr.right, tr.right) - Math.max(fr.left, tr.left) <= 0;
    if (rowOverlap > 0 || sideBySide) return centerX(t) < centerX(f) ? 'left' : 'right';
    return centerY(t) < centerY(f) ? 'up' : 'down';
  }

  function perpendicular(dir, f, t) {
    if (dir === 'left' || dir === 'right') {
      if (centerY(t) === centerY(f)) return null;
      return centerY(t) < centerY(f) ? 'up' : 'down';
    }
    if (centerX(t) === centerX(f)) return null;
    return centerX(t) < centerX(f) ? 'left' : 'right';
  }

  /**
   * Presses arrows until `target` is focused; true when it is. `env` = {focused() → element|null, resolve(target) →
   * the target now (lists re-render) or null, same(a, b), rect(el), regions(f, t) → [rect, rect] | null,
   * press(name)}. Stops when a press changes nothing (after trying the other axis once) or returns to an element
   * seen before.
   */
  function walk(target, env, max) {
    var seen = [];
    var blocked = null;
    for (var i = 0; i < (max || WALK_MAX); i++) {
      var t = env.resolve(target);
      if (!t) return false;
      var f = env.focused();
      if (f && env.same(f, t)) return true;
      var dir = 'down';
      var fr = null;
      var tr = null;
      if (f) {
        fr = env.rect(f);
        tr = env.rect(t);
        var regions = env.regions(f, t);
        dir = direction(fr, tr, regions && regions[0], regions && regions[1]);
      }
      if (blocked && blocked.from === f && blocked.dir === dir) {
        dir = perpendicular(dir, fr, tr);
        if (!dir) return false;
      }
      env.press(dir);
      var now = env.focused();
      if (now === f) {
        if (blocked && blocked.from === f) return false;
        blocked = { from: f, dir: dir };
        continue;
      }
      blocked = null;
      if (now && seen.indexOf(now) >= 0) return false;
      seen.push(now);
    }
    return false;
  }

  /** Wheel → arrow presses: {keys, rest} for an accumulated delta (`step` per press, horizontal with `x`). */
  function wheelKeys(accumulated, horizontal, step) {
    var keys = [];
    var s = step || WHEEL_STEP;
    var rest = accumulated;
    while (Math.abs(rest) >= s) {
      keys.push(horizontal ? (rest > 0 ? 'right' : 'left') : (rest > 0 ? 'down' : 'up'));
      rest -= rest > 0 ? s : -s;
    }
    return { keys: keys, rest: rest };
  }

  /** A swipe of `dx`/`dy` px since the last press → arrows: the finger moving up shows what is below ('down'). */
  function swipeKeys(dx, dy, step) {
    var horizontal = Math.abs(dx) > Math.abs(dy);
    var r = wheelKeys(-(horizontal ? dx : dy), horizontal, step || SWIPE_STEP_PX);
    return { keys: r.keys, used: (horizontal ? -dx : -dy) - r.rest, horizontal: horizontal };
  }

  /** 0…1: where `x` lies on a bar `rect`. */
  function barFraction(x, rect) {
    if (!rect || !(rect.right > rect.left)) return 0;
    return Math.max(0, Math.min(1, (x - rect.left) / (rect.right - rect.left)));
  }

  // -- the page ------------------------------------------------------------------------------------------------

  function visible(el) {
    return !!el && el.getClientRects().length > 0;
  }

  function isHidden(el) {
    return !el || el.classList.contains('hidden');
  }

  /**
   * Wires the page: `doc` = document, `app` = CT.App, `opts` = {back: the "‹ Zpět" element}. Returns the handlers
   * (for the browser check).
   */
  function install(doc, app, opts) {
    var $ = function (id) { return doc.getElementById(id); };
    var lastHover = null;
    var lastMove = { x: -1, y: -1 };
    var press = null; // {el, x, y, at, type, held, swiped, timer, repeat, sx, sy, player, drag}
    var wheelAcc = { x: 0, y: 0 };
    var lastOsdAt = 0;

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

    function tap(name) {
      send(name, 'keydown');
      send(name, 'keyup');
    }

    function layer() {
      if (!isHidden($('player'))) return $('player');
      if (!isHidden($('ar-menu'))) return $('ar-menu');
      if (!isHidden($('programme'))) return $('programme');
      if (!isHidden($('show'))) return $('show');
      return $('stage');
    }

    function focused() {
      var list = layer().querySelectorAll('.focused');
      for (var i = 0; i < list.length; i++) if (visible(list[i])) return list[i];
      return null;
    }

    function focusable(node) {
      var el = node && node.closest ? node.closest(FOCUSABLE) : null;
      return el && layer().contains(el) ? el : null;
    }

    /** A way back to an element after its list re-rendered: the nearest id and the child indexes below it. */
    function pathOf(el) {
      var steps = [];
      var node = el;
      while (node && !node.id && node.parentNode && node.parentNode.children) {
        steps.unshift(Array.prototype.indexOf.call(node.parentNode.children, node));
        node = node.parentNode;
      }
      return { id: node && node.id, steps: steps, el: el };
    }

    function resolve(path) {
      if (doc.contains(path.el)) return path.el;
      var node = path.id ? $(path.id) : null;
      for (var i = 0; node && i < path.steps.length; i++) node = node.children[path.steps[i]];
      return node || null;
    }

    function regions(f, t) {
      var node = f.parentNode;
      while (node && !node.contains(t)) node = node.parentNode;
      if (!node) return null;
      var child = function (el) {
        while (el && el.parentNode !== node) el = el.parentNode;
        return el;
      };
      var cf = child(f);
      var ct = child(t);
      return cf && ct && cf !== ct ? [cf.getBoundingClientRect(), ct.getBoundingClientRect()] : null;
    }

    var env = {
      focused: focused,
      resolve: resolve,
      same: function (a, b) { return a === b || a.contains(b) || b.contains(a); },
      rect: function (el) { return el.getBoundingClientRect(); },
      regions: regions,
      press: function (name) { tap(name); }
    };

    function editing() {
      var a = doc.activeElement;
      return !!a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA');
    }

    /** Focus `el` the remote's way; a field being typed in loses its keyboard first. */
    function focusOn(el) {
      if (editing() && doc.activeElement !== el) doc.activeElement.blur();
      return walk(pathOf(el), env);
    }

    /** A tab by click/tap: an open Nastavení section closes first (on the TV only Back leaves it). */
    function selectTab(tab) {
      if (app.programme && app.programme.isOpen()) return;
      var settings = app.screens && app.screens.settings;
      if (settings && settings.zone !== 'choice') settings.closeSection();
      app.zone = 'tabs';
      if (app.tab === tab) app.applyFocus();
      else app.showTab(tab);
    }

    function playerOpen() {
      return !!app.player && app.player.isOpen();
    }

    function osdVisible() {
      return $('osd').classList.contains('visible');
    }

    // -- hover ------------------------------------------------------------------------------------------------

    function onMove(e) {
      if (e.clientX === lastMove.x && e.clientY === lastMove.y) return; // the page moved, not the mouse
      lastMove = { x: e.clientX, y: e.clientY };
      if (press) return onPressMove(e);
      if (e.pointerType !== 'mouse') return;
      if (playerOpen()) {
        var now = Date.now();
        if (now - lastOsdAt > 300) {
          lastOsdAt = now;
          app.player.showOsd();
        }
        return;
      }
      var el = focusable(e.target);
      if (!el || el === lastHover || editing()) return;
      lastHover = el;
      focusOn(el);
    }

    // -- presses ----------------------------------------------------------------------------------------------

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
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      var target = e.target;
      if (target.closest && target.closest('#web-back')) {
        e.preventDefault();
        press = { kind: 'back' };
        return;
      }
      if (editing() && target === doc.activeElement) return; // typing: the caret is the field's
      e.preventDefault(); // no native focus or text selection: focus is the app's
      press = { el: null, x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY, at: Date.now(), type: e.pointerType,
        held: false, swiped: false, timer: null, repeat: null, player: playerOpen(), drag: null, target: target };
      if (press.player) {
        var bar = barAt(e.clientX, e.clientY);
        if (bar) {
          press.drag = bar;
          dragTo(bar, e.clientX, false);
          return;
        }
      } else {
        var tab = target.closest ? target.closest('.tab') : null;
        if (tab) {
          press.tab = tab.getAttribute('data-tab');
          return;
        }
        press.el = focusable(target);
        if (!press.el) {
          press.el = null;
        }
      }
      var p = press;
      p.timer = setTimeout(function () { if (press === p) startHold(p); }, HOLD_AFTER_MS);
    }

    /** OK held: focus, the keydown, its repeats until the long OK has fired, the keyup on release. */
    function startHold(p) {
      if (p.swiped) return;
      if (!p.player && (!p.el || !focusOn(p.el))) return;
      if (p.player && !playerTapIsOk(p)) return;
      p.held = true;
      var long = p.player ? !!app.player.tl : !!p.el.closest(LONG_OK);
      send('enter', 'keydown');
      if (!long) return;
      var began = Date.now();
      p.repeat = setInterval(function () {
        if (press !== p || Date.now() - began > REPEAT_UNTIL_MS) {
          clearInterval(p.repeat);
          return;
        }
        send('enter', 'keydown');
      }, REPEAT_MS);
    }

    /**
     * In the player OK is a tap on what OK presses there (the error's button, "Přeskočit reklamu", a track of the
     * audio list); a tap on the picture is OK (pause) only while the bar is up, else it shows the bar. Under an
     * error only its button runs it, so a stray tap never leaves for iVysílání.
     */
    function playerTapIsOk(p) {
      var on = function (selector) { return !!(p.target.closest && p.target.closest(selector)); };
      if (on('#player-message .button')) return !!app.player.errorAction;
      if (on('#skip-ad') && $('skip-ad').classList.contains('visible')) return true;
      var item = p.target.closest ? p.target.closest('.audio-item') : null;
      if (item) return walk(pathOf(item), env, 10);
      if ($('audio-list').classList.contains('visible')) return false;
      if (!osdVisible() || app.player.errorAction) {
        app.player.showOsd();
        return false;
      }
      return true;
    }

    function onPressMove(e) {
      var p = press;
      if (p.kind === 'back') return;
      if (p.drag) return dragTo(p.drag, e.clientX, false);
      if (!p.swiped && Math.abs(e.clientX - p.sx) < MOVE_PX && Math.abs(e.clientY - p.sy) < MOVE_PX) return;
      if (p.held) return;
      if (!p.swiped) {
        p.swiped = true;
        clearTimeout(p.timer);
      }
      if (p.type === 'mouse' || p.player) return; // a mouse drag outside the bar does nothing
      var s = swipeKeys(e.clientX - p.x, e.clientY - p.y);
      s.keys.forEach(function (k) { tap(k); });
      if (s.keys.length) {
        if (s.horizontal) p.x -= s.used;
        else p.y -= s.used;
      }
    }

    function onUp(e) {
      var p = press;
      press = null;
      if (!p) return;
      if (p.kind === 'back') {
        if (e.target.closest && e.target.closest('#web-back')) tap('back');
        return;
      }
      clearTimeout(p.timer);
      clearInterval(p.repeat);
      if (p.drag) {
        dragTo(p.drag, e.clientX, true);
        return;
      }
      if (p.held) {
        send('enter', 'keyup');
        return;
      }
      if (p.swiped) return;
      if (p.tab) return selectTab(p.tab);
      if (p.player) {
        if (playerTapIsOk(p)) tap('enter');
        return;
      }
      if (p.el && focusOn(p.el)) tap('enter');
    }

    function onCancel() {
      var p = press;
      press = null;
      if (!p || p.kind === 'back') return;
      clearTimeout(p.timer);
      clearInterval(p.repeat);
      if (p.held) send('enter', 'keyup');
      if (p.drag && app.player.tl) {
        app.player.tl.scrub = null;
        app.player.tl.render();
      }
    }

    // -- the player's bar -------------------------------------------------------------------------------------

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

    function onWheel(e) {
      if (playerOpen() || editing()) return;
      e.preventDefault();
      var unit = e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? 800 : 1;
      var horizontal = Math.abs(e.deltaX) > Math.abs(e.deltaY) || (e.shiftKey && e.deltaX === 0);
      var delta = (horizontal && e.deltaX === 0 ? e.deltaY : horizontal ? e.deltaX : e.deltaY) * unit;
      var axis = horizontal ? 'x' : 'y';
      wheelAcc[axis === 'x' ? 'y' : 'x'] = 0;
      var r = wheelKeys(wheelAcc[axis] + delta, horizontal);
      wheelAcc[axis] = r.rest;
      r.keys.forEach(function (k) { tap(k); });
    }

    doc.addEventListener('pointermove', onMove, true);
    doc.addEventListener('pointerdown', onDown, true);
    doc.addEventListener('pointerup', onUp, true);
    doc.addEventListener('pointercancel', onCancel, true);
    doc.addEventListener('wheel', onWheel, { capture: true, passive: false });
    // no context menu: nothing to save or copy, and a long touch press is long OK
    doc.addEventListener('contextmenu', function (e) { e.preventDefault(); }, true);
    if (opts && opts.back) opts.back.textContent = '‹ Zpět';
    return { send: send, tap: tap, focusOn: focusOn, focused: focused };
  }

  CT.WebInput = {
    FOCUSABLE: FOCUSABLE,
    LONG_OK: LONG_OK,
    keyName: keyName,
    direction: direction,
    walk: walk,
    wheelKeys: wheelKeys,
    swipeKeys: swipeKeys,
    barFraction: barFraction,
    install: install
  };
})(typeof window !== 'undefined' ? window : global);
