'use strict';
// Scrolling like a web page (spec/web.md "Input (T29)"): the wheel / trackpad / swipe move the view, never the focus.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

const S = CT.WebScroll;

test('wheel delta: pixels, lines, Shift = horizontal, trackpad sideways', () => {
  assert.deepEqual(S.wheelDelta({ deltaX: 0, deltaY: 100, deltaMode: 0 }), { x: 0, y: 100 });
  assert.deepEqual(S.wheelDelta({ deltaX: 0, deltaY: 3, deltaMode: 1 }), { x: 0, y: 120 });
  assert.deepEqual(S.wheelDelta({ deltaX: 0, deltaY: 100, deltaMode: 0, shiftKey: true }), { x: 100, y: 0 });
  assert.deepEqual(S.wheelDelta({ deltaX: -30, deltaY: 4, deltaMode: 0 }), { x: -30, y: 4 });
});

test('lines: whole lines out of accumulated pixels, the rest kept (both directions)', () => {
  assert.deepEqual(S.lines(170, 80), { lines: 2, rest: 10 });
  assert.deepEqual(S.lines(-170, 80), { lines: -2, rest: -10 });
  assert.deepEqual(S.lines(30, 80), { lines: 0, rest: 30 });
});

test('the guide moves its view (px through the stage scale, whole rows), the focus is not touched', () => {
  const calls = [];
  const guide = { focusRow: 3, focusSlot: 2, scrollView: (dx, rows) => { calls.push([dx, rows]); return true; } };
  const app = { screens: { guide } };
  const views = S.views(app, {}, () => 0.5);
  views['#grid'](100, 0); // trackpad sideways
  views['#grid'](0, 30); // 76 * 0.5 = 38 px a row: not yet
  views['#grid'](0, 30); // 60 px: one row
  assert.deepEqual(calls, [[200, 0], [0, 0], [0, 1]]);
  assert.deepEqual([guide.focusRow, guide.focusSlot], [3, 2]);
});

test('the Archiv grid: one line per notch (capped step), the next page near the end, focus untouched', () => {
  let loaded = null;
  const lines = { top: 0, rows: 6, visible: 2, synced: 0, sync(row) { assert.equal(row, null); this.synced++; } };
  const archive = { grid: { focus: 0, lines, nextPage: 2, loading: false }, loadPage: (p) => { loaded = p; }, renderTmdb() {},
    shown() { return this.grid; } };
  const views = S.views({ screens: { archive } }, {}, () => 1);
  assert.equal(views['#ar-grid'](0, 100), false); // 350 px a line, capped at 120: not yet
  assert.equal(views['#ar-grid'](0, 30), true);
  assert.equal(lines.top, 1);
  views['#ar-grid'](0, 240);
  views['#ar-grid'](0, 240);
  assert.equal(lines.top, 4); // the end: 6 rows − 2 visible
  assert.equal(loaded, 2);
  assert.equal(archive.grid.focus, 0);
  assert.equal(views['#ar-grid'](0, -1000), true);
  assert.equal(lines.top, 0);
});

test('the Archiv grid while new cards load (T52): the wheel scrolls the dimmed old cards, no next page for them', () => {
  let loaded = null;
  const oldLines = { top: 0, rows: 6, visible: 2, sync() {} };
  const newLines = { top: 0, rows: 0, visible: 2, sync() {} };
  const old = { focus: 0, lines: oldLines, nextPage: 2, loading: false };
  const archive = { grid: { focus: 0, lines: newLines, nextPage: null, loading: true }, loadPage: (p) => { loaded = p; },
    renderTmdb() {}, shown() { return old; } };
  const views = S.views({ screens: { archive } }, {}, () => 1);
  views['#ar-grid'](0, 240);
  views['#ar-grid'](0, 240);
  assert.equal(oldLines.top, 4);
  assert.equal(newLines.top, 0);
  assert.equal(loaded, null);
});

test('viewAt: the TV\'s virtual lists; everything else scrolls natively', () => {
  const at = (sel) => ({ closest: (s) => (s === sel ? {} : null) });
  assert.equal(S.viewAt(at('#grid')), '#grid');
  assert.equal(S.viewAt(at('#sh-episodes')), '#sh-episodes');
  assert.equal(S.viewAt(at('#screen-live')), null);
  assert.equal(S.viewAt(null), null);
});
