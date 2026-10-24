'use strict';
// spec/apps.md "Across days": past the first/last cell of a row the guide continues in the previous/next day, at that
// day's end/start in the same row. Regression: the screen passed 'start'/'end' as a bare string, so selectDay saw no
// edge and opened the other day at 20:00 instead.
const test = require('node:test');
const assert = require('node:assert');
const { CT } = require('./load');

global.window = global; // guide.js is a browser script; this test only drives its key handling (no DOM needed)
require('../app/js/ui/guide.js');

const MIN = 60 * 1000;

function screenAtEdge(slotIndex) {
  const app = { now: Date.parse('2026-10-24T18:00:00Z') };
  const guide = new CT.GuideScreen(app);
  const start = CT.Days.bounds('2026-10-24')[0];
  guide.day = '2026-10-24';
  guide.days = CT.Days.guideDays('2026-10-24');
  guide.axisStart = start;
  guide.rows = [{ slots: [{ start: start, end: start + 60 * MIN, item: {} }, { start: start + 60 * MIN, end: start + 120 * MIN, item: {} }] }];
  guide.focusRow = 0;
  guide.focusSlot = slotIndex;
  guide.calls = [];
  guide.selectDay = function (day, opts) { this.calls.push([day, opts]); };
  return guide;
}

test('right past the last cell opens the next day at its start', () => {
  const guide = screenAtEdge(1);
  assert.strictEqual(guide.onKey('right'), true);
  assert.deepStrictEqual(guide.calls, [['2026-10-25', { edge: 'start' }]]);
});

test('left past the first cell opens the previous day at its end', () => {
  const guide = screenAtEdge(0);
  assert.strictEqual(guide.onKey('left'), true);
  assert.deepStrictEqual(guide.calls, [['2026-10-23', { edge: 'end' }]]);
});
