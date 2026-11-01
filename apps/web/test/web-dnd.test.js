'use strict';
// Kanály drag and drop (spec/web.md, T31): the drop saves exactly what the ▲▼ buttons would.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

const D = CT.WebDnd;
const P = CT.ChannelPrefs;
const KEYS = ['ct:CH_1', 'ct:CH_2', 'ct:CH_24', 'ct:CH_4', 'dvb:nova', 'dvb:prima'];

/** The prefs ▲/▼ presses give (the buttons' own path: ChannelPrefs.move on the shown list). */
function presses(prefs, key, n) {
  let p = prefs;
  for (let i = 0; i < Math.abs(n); i++) p = P.move(KEYS, p, key, n > 0 ? 1 : -1);
  return p;
}

test('dropping a row = the same prefs as that many ▲▼ presses (down and up)', () => {
  const start = P.parse({ order: [], hidden: ['ct:CH_2'], sourcesOff: [] });
  // ČT1 dropped between Nova and Prima (gap 5) = ▼ four times
  const down = D.reorder(KEYS, start, 'ct:CH_1', 5);
  assert.equal(down.steps, 4);
  assert.deepEqual(down.prefs, presses(start, 'ct:CH_1', 4));
  assert.deepEqual(P.arrange(KEYS, down.prefs).all, ['ct:CH_2', 'ct:CH_24', 'ct:CH_4', 'dvb:nova', 'ct:CH_1', 'dvb:prima']);
  assert.deepEqual(down.prefs.hidden, ['ct:CH_2']); // hidden channels stay hidden
  // Prima dropped first (gap 0) = ▲ five times
  const up = D.reorder(KEYS, start, 'dvb:prima', 0);
  assert.equal(up.steps, -5);
  assert.deepEqual(up.prefs, presses(start, 'dvb:prima', -5));
  assert.equal(P.arrange(KEYS, up.prefs).all[0], 'dvb:prima');
  // to the end (gap = rows)
  assert.equal(P.arrange(KEYS, D.reorder(KEYS, start, 'ct:CH_24', 6).prefs).all[5], 'ct:CH_24');
});

test('dropping where it was (on its own gaps) changes nothing', () => {
  const start = P.empty();
  assert.equal(D.reorder(KEYS, start, 'ct:CH_24', 2).steps, 0);
  assert.equal(D.reorder(KEYS, start, 'ct:CH_24', 3).steps, 0);
  assert.equal(D.reorder(KEYS, start, 'nope', 0).steps, 0);
});

test('drop position: the gap before the first row whose middle is below the pointer', () => {
  const rows = [0, 1, 2].map((i) => ({ top: 100 + i * 80, bottom: 170 + i * 80 }));
  assert.equal(D.dropIndex(90, rows), 0);
  assert.equal(D.dropIndex(140, rows), 1); // past the first row's middle (135)
  assert.equal(D.dropIndex(214, rows), 1);
  assert.equal(D.dropIndex(216, rows), 2);
  assert.equal(D.dropIndex(400, rows), 3);
});

test('a drag near the visible list\'s top or bottom scrolls it (long channel lists)', () => {
  const rect = { top: 100, bottom: 900 };
  assert.equal(D.edgeScroll(120, rect), -1);
  assert.equal(D.edgeScroll(500, rect), 0);
  assert.equal(D.edgeScroll(880, rect), 1);
  assert.equal(D.edgeScroll(100 + D.EDGE_PX, rect), 0);
});
