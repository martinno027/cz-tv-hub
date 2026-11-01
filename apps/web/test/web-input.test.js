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
  assert.equal(k(65), 'audio'); // A = the remote's AUDIO (the keyboard has no AUDIO key)
  assert.equal(k(66), null);
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

test('player bar: the pointer position as a fraction of the bar', () => {
  const bar = rect(100, 900, 1000, 10);
  assert.equal(I.barFraction(600, bar), 0.5);
  assert.equal(I.barFraction(50, bar), 0);
  assert.equal(I.barFraction(5000, bar), 1);
  assert.equal(I.barFraction(600, rect(0, 0, 0, 0)), 0);
});

test('long OK where the screens have it (Archiv cards, show episodes): a right click / long press reaches them', () => {
  const longOnes = CT.WebFocus.ADAPTERS.filter((a) => a.long).map((a) => a.match).sort();
  assert.deepEqual(longOnes, ['.ar-card', '.sh-ep']);
  const all = fs.readdirSync(path.join(APP, 'js', 'ui')).filter((f) => f.endsWith('.js'))
    .filter((f) => /new Cards\.LongPress\(/.test(fs.readFileSync(path.join(APP, 'js', 'ui', f), 'utf8')));
  assert.deepEqual(all.sort(), ['archive.js', 'show.js']); // a new long OK place needs an adapter with `long`
});

test('F toggles fullscreen, not while typing and not with a shortcut modifier', () => {
  const F = I.isFullscreenKey;
  assert.equal(F({ key: 'f' }, 'BODY'), true);
  assert.equal(F({ key: 'F' }, 'BODY'), true);
  assert.equal(F({ keyCode: 70 }, 'BODY'), true);
  assert.equal(F({ key: 'f' }, 'INPUT'), false);
  assert.equal(F({ key: 'f', ctrlKey: true }, 'BODY'), false);
  assert.equal(F({ key: 'f', metaKey: true }, 'BODY'), false);
  assert.equal(F({ key: 'g' }, 'BODY'), false);
  assert.equal(F({ key: 'f', ctName: 'enter' }, 'BODY'), false);
});

test('Alt+← stays the browser\'s Back (never a remote key)', () => {
  assert.equal(I.keyName({ keyCode: 37, key: 'ArrowLeft', altKey: true }, CODES, 'BODY'), null);
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
  assert.equal(k('b'), null);
  assert.equal(I.keyName({ key: 'Escape' }, CODES, 'BODY'), 'back'); // no keyCode property at all
});

test('A is the audio key, but a letter while typing', () => {
  assert.equal(I.keyName({ key: 'a', keyCode: 65 }, CODES, 'BODY'), 'audio');
  assert.equal(I.keyName({ key: 'A', keyCode: 65 }, CODES, 'BODY'), 'audio');
  assert.equal(I.keyName({ key: 'a', keyCode: 65 }, CODES, 'INPUT'), null);
  assert.equal(I.keyName({ key: 'a', keyCode: 65, metaKey: true }, CODES, 'BODY'), null);
});
