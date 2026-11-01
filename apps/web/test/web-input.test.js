'use strict';
// Input on the web (spec/web.md "Input"): the keyboard gives the remote's key names, and the pointer's focus walk
// presses the same arrows a remote would; wheel and swipe are arrow presses.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { CT, APP } = require('./load');

const I = CT.WebInput;

/** The Samsung remote's code table, read from keys.js itself (the web must keep every code). */
function samsungCodes() {
  const src = fs.readFileSync(path.join(APP, 'js', 'ui', 'keys.js'), 'utf8');
  const sandbox = { window: { CT: {} } };
  new Function('window', src)(sandbox.window); // eslint-disable-line no-new-func
  const codes = {};
  for (let code = 0; code < 70000; code++) {
    const name = sandbox.window.CT.Keys.name({ keyCode: code });
    if (name) codes[code] = name;
  }
  return codes;
}

const CODES = samsungCodes();

test('keys: every Samsung remote code keeps its name on the web (arrows, OK, Back, digits, media)', () => {
  assert.ok(Object.keys(CODES).length > 30);
  for (const code of Object.keys(CODES)) {
    assert.equal(I.keyName({ keyCode: Number(code) }, CODES, 'BODY'), CODES[code], 'code ' + code);
  }
  const k = (keyCode, extra, tag) => I.keyName(Object.assign({ keyCode }, extra || {}), CODES, tag || 'BODY');
  assert.equal(k(37), 'left');
  assert.equal(k(13), 'enter');
  assert.equal(k(27), 'back'); // Esc
  assert.equal(k(8), 'back'); // Backspace
  assert.equal(k(55), 'd7');
  assert.equal(k(103), 'd7'); // numpad
  assert.equal(k(32), 'playpause'); // Space
  assert.equal(k(179), 'playpause'); // media key
  assert.equal(k(65), null);
});

test('keys: typing keeps Backspace and Space in the field; browser shortcuts stay the browser\'s', () => {
  const k = (keyCode, extra, tag) => I.keyName(Object.assign({ keyCode }, extra || {}), CODES, tag);
  assert.equal(k(8, {}, 'INPUT'), null);
  assert.equal(k(32, {}, 'INPUT'), null);
  assert.equal(k(13, {}, 'INPUT'), 'enter'); // ends typing as on the TV
  assert.equal(k(27, {}, 'INPUT'), 'back');
  assert.equal(k(55, {}, 'INPUT'), 'd7'); // the screen decides (typing returns 'input')
  assert.equal(k(37, { metaKey: true }, 'BODY'), null);
  assert.equal(k(82, { ctrlKey: true }, 'BODY'), null);
  assert.equal(k(37, { altKey: true }, 'BODY'), null);
  assert.equal(I.keyName({ keyCode: 0, ctName: 'enter' }, CODES, 'INPUT'), 'enter'); // the pointer's own presses
});

function rect(left, top, width, height) {
  return { left, top, right: left + width, bottom: top + height, width, height };
}

/**
 * A fake screen: `items` [{id, rect, region}], `move(focusId, key)` → the next focus id (the screen's own key
 * logic). Returns the walk env and the presses made.
 */
function screen(items, move, start) {
  const byId = {};
  items.forEach((it) => { byId[it.id] = it; });
  const regions = {};
  items.forEach((it) => { if (it.regionRect) regions[it.region] = it.regionRect; });
  const state = { focus: start, presses: [] };
  const env = {
    focused: () => (state.focus ? byId[state.focus] : null),
    resolve: (t) => t,
    same: (a, b) => a === b,
    rect: (it) => it.rect,
    regions: (f, t) => (f.region && t.region && f.region !== t.region ? [regions[f.region], regions[t.region]] : null),
    press: (key) => {
      state.presses.push(key);
      state.focus = move(state.focus, key) || state.focus;
    }
  };
  return { env, state, byId };
}

test('walk: a card grid (Živě) — along the row, then down; no press after the target', () => {
  const items = [];
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 4; c++) items.push({ id: r + ':' + c, rect: rect(100 + c * 400, 200 + r * 250, 360, 220) });
  }
  const move = (id, key) => {
    let [r, c] = id.split(':').map(Number);
    if (key === 'left') c = Math.max(0, c - 1);
    if (key === 'right') c = Math.min(3, c + 1);
    if (key === 'up') r = Math.max(0, r - 1);
    if (key === 'down') r = Math.min(2, r + 1);
    return r + ':' + c;
  };
  const s = screen(items, move, '0:0');
  assert.equal(I.walk(s.byId['2:3'], s.env), true);
  assert.equal(s.state.focus, '2:3');
  assert.equal(s.state.presses.length, 5);
  assert.deepEqual(s.state.presses.slice().sort(), ['down', 'down', 'right', 'right', 'right']);
  const again = s.state.presses.length;
  assert.equal(I.walk(s.byId['2:3'], s.env), true); // already there: nothing pressed
  assert.equal(s.state.presses.length, again);
});

test('walk: Archiv menu and grid side by side — Right into the grid first, never Down in the menu', () => {
  const side = rect(0, 100, 400, 900);
  const main = rect(450, 100, 1400, 900);
  const items = [];
  for (let m = 0; m < 6; m++) items.push({ id: 'm' + m, rect: rect(20, 120 + m * 80, 360, 70), region: 'side', regionRect: side });
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 4; c++) items.push({ id: 'g' + r + c, rect: rect(470 + c * 340, 120 + r * 300, 320, 280), region: 'main', regionRect: main });
  }
  const move = (id, key) => {
    if (id[0] === 'm') {
      const m = Number(id[1]);
      if (key === 'up') return 'm' + Math.max(0, m - 1);
      if (key === 'down') return 'm' + Math.min(5, m + 1);
      if (key === 'right') return 'g00'; // the grid keeps its own focus
      return id;
    }
    let r = Number(id[1]);
    let c = Number(id[2]);
    if (key === 'left' && c === 0) return 'm0';
    if (key === 'left') c--;
    if (key === 'right') c = Math.min(3, c + 1);
    if (key === 'up') r = Math.max(0, r - 1);
    if (key === 'down') r = Math.min(2, r + 1);
    return 'g' + r + c;
  };
  const s = screen(items, move, 'm0');
  assert.equal(I.walk(s.byId.g21, s.env), true);
  assert.equal(s.state.presses[0], 'right');
  assert.ok(!s.state.presses.includes('up'));
  // back to a menu item: Left out of the grid, then Down in the menu
  const s2 = screen(items, move, 'g10');
  assert.equal(I.walk(s2.byId.m3, s2.env), true);
  assert.equal(s2.state.presses[0], 'left');
});

test('walk: guide rows of uneven cells — up/down keep the time, left/right finish on the row', () => {
  // rows: cells [start, end] in px; up/down go to the cell covering the focused cell's start
  const rows = [[[0, 300], [300, 450], [450, 900]], [[0, 120], [120, 700], [700, 900]], [[0, 500], [500, 600], [600, 900]]];
  const items = [];
  rows.forEach((cells, r) => cells.forEach(([a, b], c) => items.push({ id: r + ':' + c, rect: rect(200 + a, 300 + r * 100, b - a, 90) })));
  const move = (id, key) => {
    let [r, c] = id.split(':').map(Number);
    if (key === 'left') c = Math.max(0, c - 1);
    if (key === 'right') c = Math.min(rows[r].length - 1, c + 1);
    if (key === 'up' || key === 'down') {
      const at = rows[r][c][0];
      r = key === 'up' ? Math.max(0, r - 1) : Math.min(rows.length - 1, r + 1);
      c = rows[r].findIndex(([a, b]) => at >= a && at < b);
    }
    return r + ':' + c;
  };
  const s = screen(items, move, '0:0');
  assert.equal(I.walk(s.byId['2:1'], s.env), true);
  assert.ok(s.state.presses.length <= 4, s.state.presses.join(' '));
});

test('walk: an unreachable target stops (no endless presses)', () => {
  const items = [{ id: 'a', rect: rect(0, 0, 100, 100) }, { id: 'b', rect: rect(0, 500, 100, 100) }];
  const s = screen(items, () => null, 'a'); // nothing moves
  assert.equal(I.walk(s.byId.b, s.env), false);
  assert.ok(s.state.presses.length <= 2);
  const loop = screen(items.concat([{ id: 'c', rect: rect(500, 0, 100, 100) }]), (id) => (id === 'a' ? 'c' : 'a'), 'a');
  assert.equal(I.walk(loop.byId.b, loop.env), false);
  assert.ok(loop.state.presses.length < 10);
});

test('direction: same row → left/right, stacked → up/down, side-by-side regions → left/right', () => {
  assert.equal(I.direction(rect(0, 0, 100, 50), rect(300, 10, 100, 50)), 'right');
  assert.equal(I.direction(rect(300, 0, 100, 50), rect(0, 10, 100, 50)), 'left');
  assert.equal(I.direction(rect(0, 0, 100, 50), rect(300, 200, 100, 50)), 'down');
  assert.equal(I.direction(rect(0, 200, 100, 50), rect(0, 0, 100, 50)), 'up');
  assert.equal(I.direction(rect(0, 0, 100, 50), rect(300, 200, 100, 50), rect(0, 0, 200, 900), rect(250, 0, 900, 900)), 'right');
});

test('wheel and swipe: arrow presses per step, the rest kept; the finger moving up = Down', () => {
  assert.deepEqual(I.wheelKeys(170, false), { keys: ['down', 'down'], rest: 10 });
  assert.deepEqual(I.wheelKeys(-90, false), { keys: ['up'], rest: -10 });
  assert.deepEqual(I.wheelKeys(30, false), { keys: [], rest: 30 });
  assert.deepEqual(I.wheelKeys(100, true).keys, ['right']);
  assert.deepEqual(I.swipeKeys(0, -150).keys, ['down', 'down']);
  assert.deepEqual(I.swipeKeys(0, 80).keys, ['up']);
  assert.deepEqual(I.swipeKeys(-100, 10).keys, ['right']);
  assert.equal(I.swipeKeys(0, -150).used, 140);
});

test('player bar: the pointer position as a fraction of the bar', () => {
  const bar = rect(100, 900, 1000, 10);
  assert.equal(I.barFraction(600, bar), 0.5);
  assert.equal(I.barFraction(50, bar), 0);
  assert.equal(I.barFraction(5000, bar), 1);
  assert.equal(I.barFraction(600, rect(0, 0, 0, 0)), 0);
});

test('long OK where the screens have it (Archiv cards, show episodes)', () => {
  assert.equal(I.LONG_OK, '.ar-card, .sh-ep');
  const archive = fs.readFileSync(path.join(APP, 'js', 'ui', 'archive.js'), 'utf8');
  const show = fs.readFileSync(path.join(APP, 'js', 'ui', 'show.js'), 'utf8');
  const all = fs.readdirSync(path.join(APP, 'js', 'ui')).filter((f) => f.endsWith('.js'))
    .filter((f) => /new Cards\.LongPress\(/.test(fs.readFileSync(path.join(APP, 'js', 'ui', f), 'utf8')));
  assert.deepEqual(all.sort(), ['archive.js', 'show.js']); // a new long OK place must be added to LONG_OK
  assert.ok(/LongPress/.test(archive) && /LongPress/.test(show));
});

test('keys by `key` when an event has no keyCode (Esc/Backspace close the player, arrows, OK, digits)', () => {
  const k = (key, tag) => I.keyName({ key, keyCode: 0 }, CODES, tag || 'BODY');
  assert.equal(k('Escape'), 'back');
  assert.equal(k('Backspace'), 'back');
  assert.equal(k('Backspace', 'INPUT'), null);
  assert.equal(k('ArrowUp'), 'up');
  assert.equal(k('Enter'), 'enter');
  assert.equal(k(' '), 'playpause');
  assert.equal(k('7'), 'd7');
  assert.equal(k('PageDown'), 'chdown');
  assert.equal(k('a'), null);
  assert.equal(I.keyName({ key: 'Escape' }, CODES, 'BODY'), 'back'); // no keyCode property at all
});
