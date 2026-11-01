/*
 * Scrolling like a web page (spec/web.md "Input (T29)"): the wheel, a trackpad (horizontally too) and a swipe move
 * what is on screen without moving the remote's focus. Živě and Nastavení are ordinary scroll containers on the web
 * (web.css); the guide grid, the Archiv grid and menu and a show's episodes build only the lines near what is shown
 * (the TV's virtual lists), so they move their own view here.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var LINE_PX = 40; // a wheel "line" (deltaMode 1)
  var MAX_STEP_PX = 120; // screen px of wheel or swipe per line at most (one notch is about 100)

  /** A wheel event's delta in screen px: {x, y} (Shift + wheel = horizontal, as browsers do). */
  function wheelDelta(e) {
    var unit = e.deltaMode === 1 ? LINE_PX : e.deltaMode === 2 ? 800 : 1;
    var x = (e.deltaX || 0) * unit;
    var y = (e.deltaY || 0) * unit;
    if (e.shiftKey && !x) return { x: y, y: 0 };
    return { x: x, y: y };
  }

  /**
   * Whole lines out of accumulated px: {lines, rest}. `step` = a line's height in screen px (the stage is scaled).
   */
  function lines(accumulated, step) {
    var n = accumulated >= 0 ? Math.floor(accumulated / step) : -Math.floor(-accumulated / step);
    return { lines: n, rest: accumulated - n * step };
  }

  /**
   * The virtual views by selector: function (dx, dy) in screen px → whether the view moved. `scale()` = the stage's
   * scale (screen px per stage px).
   */
  function views(app, doc, scale) {
    var GRID_ROW = 76;
    var CARD_ROW = 350; // archive.js GRID_STEP_Y
    var MENU_ROW = 58; // archive.js MENU_STEP
    var EP_ROW = 76;
    var acc = { guide: 0, cards: 0, menu: 0, episodes: 0 };
    function step(key, dy, rowPx) {
      var r = lines(acc[key] + dy, Math.min(MAX_STEP_PX, rowPx * scale())); // a notch moves at least a line
      acc[key] = r.rest;
      return r.lines;
    }
    function moveLines(list, n) {
      if (!list || !n) return false;
      var before = list.top;
      list.top = Math.max(0, Math.min(list.top + n, Math.max(0, list.rows - list.visible)));
      if (list.top === before) return false;
      list.sync(null);
      return true;
    }
    return {
      '#grid': function (dx, dy) {
        var guide = app.screens.guide;
        return guide.scrollView(dx / scale(), step('guide', dy, GRID_ROW));
      },
      '#ar-grid': function (dx, dy) {
        var archive = app.screens.archive;
        var g = archive.grid;
        var moved = moveLines(g.lines, step('cards', dy, CARD_ROW));
        // near the end the next page comes, as when the focus gets there
        if (moved && g.nextPage && !g.loading && g.lines.top + g.lines.visible >= g.lines.rows - 2) archive.loadPage(g.nextPage);
        if (moved) archive.renderTmdb();
        return moved;
      },
      '#sh-episodes': function (dx, dy) {
        return moveLines(app.screens.archive.page.list, step('episodes', dy, EP_ROW));
      },
      '#ar-side': function (dx, dy) {
        var archive = app.screens.archive;
        var n = step('menu', dy, MENU_ROW);
        if (!n || !archive.menu) return false;
        var box = doc.getElementById('ar-side');
        var visible = Math.max(1, Math.floor(box.clientHeight / MENU_ROW));
        var top = Math.max(0, Math.min(archive.menuTop + n, Math.max(0, archive.menu.items.length - visible)));
        if (top === archive.menuTop) return false;
        archive.menuTop = top;
        box.firstChild.style.transform = 'translateY(' + -top * MENU_ROW + 'px)';
        return true;
      }
    };
  }

  /** The virtual view under `node` (a selector of views()), or null: the rest scrolls natively. */
  function viewAt(node) {
    var selectors = ['#grid', '#ar-grid', '#sh-episodes', '#ar-side'];
    for (var i = 0; node && node.closest && i < selectors.length; i++) {
      if (node.closest(selectors[i])) return selectors[i];
    }
    return null;
  }

  CT.WebScroll = { wheelDelta: wheelDelta, lines: lines, views: views, viewAt: viewAt, LINE_PX: LINE_PX };
})(typeof window !== 'undefined' ? window : global);
