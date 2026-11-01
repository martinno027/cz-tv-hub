/*
 * Kanály: drag and drop (spec/web.md, T31): a channel row is dragged with the mouse (or a long touch and drag) to a
 * new place; a line between rows shows where it lands; dropping saves the order exactly as the ▲▼ buttons do — the
 * same ChannelPrefs.move steps, then app.setChannelPrefs (prefs stored, profile told, numbers renumbered). ▲▼ and the
 * keyboard keep working.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var MOVE_PX = 12; // as web-input.js: a press that moved this far is a drag, not a click
  var TOUCH_HOLD_MS = 450; // a touch held this long on a row picks it up (then the finger drags it)
  var EDGE_PX = 60; // a drag this close to the list's top or bottom edge scrolls it (screen px)
  var EDGE_STEP_PX = 24; // per tick (50 ms) while held there

  /** Scrolling while dragging near an edge of the visible list: -1 up, +1 down, 0 none. */
  function edgeScroll(y, rect) {
    if (y < rect.top + EDGE_PX) return -1;
    if (y > rect.bottom - EDGE_PX) return 1;
    return 0;
  }

  /** The gap (0 … rows) a pointer at `y` points to: before the first row whose middle is below it. */
  function dropIndex(y, rects) {
    for (var i = 0; i < rects.length; i++) if (y < (rects[i].top + rects[i].bottom) / 2) return i;
    return rects.length;
  }

  /**
   * The prefs after moving `key` (in the list `keys`) into gap `gap`: as many ▲/▼ presses as places (ChannelPrefs.move,
   * the buttons' own rule). Returns {prefs, steps}; steps 0 = nothing changes.
   */
  function reorder(keys, prefs, key, gap) {
    var P = CT.ChannelPrefs;
    var from = keys.indexOf(key);
    if (from < 0) return { prefs: prefs, steps: 0 };
    var to = gap > from ? gap - 1 : gap;
    var dir = to > from ? 1 : -1;
    var p = prefs;
    for (var i = from; i !== to; i += dir) p = P.move(keys, p, key, dir);
    return { prefs: p, steps: to - from };
  }

  /** Wires the Kanály list: `doc` = document, `app` = CT.App. */
  function install(doc, app) {
    var drag = null; // {key, keys, row, sx, sy, y, active, timer, gap, pointer}
    var scroller = null; // the interval scrolling the list while the drag rests near an edge
    var line = null;

    function settings() {
      return app.screens.settings;
    }

    function rowsOf() {
      return settings().chItems.filter(function (item) { return !!item.key; }).map(function (item) {
        return { key: item.key, keys: item.keys, el: item.controls[0].parentNode };
      });
    }

    function lineAt(rows, gap) {
      var box = doc.getElementById('set-channels');
      if (!line) {
        line = doc.createElement('div');
        line.className = 'web-drop-line';
      }
      if (line.parentNode !== box) box.appendChild(line);
      var edge = gap < rows.length ? rows[gap].el.offsetTop : rows[rows.length - 1].el.offsetTop + rows[rows.length - 1].el.offsetHeight;
      line.style.top = edge - 3 + 'px';
      line.classList.remove('hidden');
    }

    function stop() {
      clearInterval(scroller);
      scroller = null;
      if (drag) {
        clearTimeout(drag.timer);
        if (drag.row) drag.row.classList.remove('web-dragging');
      }
      drag = null;
      doc.body.classList.remove('web-dnd');
      if (line) line.classList.add('hidden');
    }

    function begin() {
      drag.active = true;
      drag.row.classList.add('web-dragging');
      doc.body.classList.add('web-dnd');
    }

    /** Near the visible list's top or bottom the list scrolls (also while the pointer rests there). */
    function autoScroll() {
      var box = doc.getElementById('screen-settings');
      var dir = edgeScroll(drag.y, box.getBoundingClientRect());
      clearInterval(scroller);
      scroller = null;
      if (!dir) return;
      scroller = setInterval(function () {
        if (!drag || !drag.active) return stop();
        var before = box.scrollTop;
        box.scrollTop += dir * EDGE_STEP_PX / (CT.WebStage.last ? CT.WebStage.last.scale : 1);
        if (box.scrollTop === before) return;
        update(drag.y, true);
      }, 50);
    }

    function update(y, scrolling) {
      drag.y = y;
      if (!scrolling) autoScroll();
      var rows = rowsOf();
      drag.gap = dropIndex(y, rows.map(function (r) { return r.el.getBoundingClientRect(); }));
      lineAt(rows, drag.gap);
    }

    doc.addEventListener('pointerdown', function (e) {
      stop();
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      var row = e.target.closest ? e.target.closest('#set-channels .ch-row') : null;
      if (!row || app.tab !== 'settings') return;
      var item = rowsOf().filter(function (r) { return r.el === row; })[0];
      if (!item) return; // the source switches and "Obnovit výchozí pořadí" are no channels
      drag = { key: item.key, keys: item.keys, row: row, sx: e.clientX, sy: e.clientY, active: false, timer: null, gap: null,
        pointer: e.pointerType };
      if (e.pointerType !== 'mouse') {
        var d = drag;
        d.timer = setTimeout(function () {
          if (drag !== d) return;
          begin();
          update(d.sy);
        }, TOUCH_HOLD_MS);
      }
    }, true);

    doc.addEventListener('pointermove', function (e) {
      if (!drag) return;
      if (!drag.active) {
        if (Math.abs(e.clientX - drag.sx) < MOVE_PX && Math.abs(e.clientY - drag.sy) < MOVE_PX) return;
        if (drag.pointer !== 'mouse') return stop(); // a touch that moves before the hold scrolls the list
        begin();
      }
      update(e.clientY);
    }, true);

    // while a touch drags a row, the list must not scroll under the finger
    doc.addEventListener('touchmove', function (e) {
      if (drag && drag.active) e.preventDefault();
    }, { capture: true, passive: false });

    doc.addEventListener('pointerup', function () {
      var d = drag;
      if (!d || !d.active || d.gap === null) return stop();
      stop();
      var r = reorder(d.keys, app.channelPrefs, d.key, d.gap);
      if (r.steps) app.setChannelPrefs(r.prefs); // as ▲▼: stored, profile changed, the list renumbered
    }, true);

    doc.addEventListener('pointercancel', stop, true);
  }

  CT.WebDnd = { dropIndex: dropIndex, reorder: reorder, edgeScroll: edgeScroll, install: install, MOVE_PX: MOVE_PX,
    EDGE_PX: EDGE_PX };
})(typeof window !== 'undefined' ? window : global);
