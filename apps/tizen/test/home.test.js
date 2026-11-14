'use strict';
// Domů (spec/apps.md "Domů", T58): what its blocks show and how the focus moves between its rows.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

const H = CT.Home;

function item(source, id) {
  return { key: source + ':' + id, source, channel: { id, name: id } };
}

test('gateway notice: only with a gateway, after a status check; failed first, then recording paused', () => {
  assert.equal(H.notice(false, { failed: true }), null);
  assert.equal(H.notice(true, null), null); // not checked yet
  assert.equal(H.notice(true, { failed: true, recordingPaused: false }), 'Brána antény není dostupná.');
  assert.equal(H.notice(true, { failed: true, badKey: true, recordingPaused: false }),
    'Brána antény odmítla klíč. Zkontrolujte ho v Nastavení → Server.');
  assert.notEqual(H.BAD_KEY, H.GATEWAY_DOWN);
  assert.equal(H.notice(true, { failed: false, recordingPaused: true }),
    'Nahrávání je pozastavené: na disku nahrávek dochází místo.');
  assert.equal(H.notice(true, { failed: false, recordingPaused: false }), null);
});

test('Teď běží: the first 10 visible ČT/Anténa channels with their Kanály numbers; Chamber streams left out', () => {
  const list = ['CH_1', 'CH_2', 'CH_24'].map((id) => item('ct', id))
    .concat([item('psp', 'stream1')])
    .concat(['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'].map((id) => item('dvb', id)));
  const now = H.nowItems(list);
  assert.equal(now.length, 10);
  assert.deepEqual(now.map((x) => x.item.key).slice(0, 4), ['ct:CH_1', 'ct:CH_2', 'ct:CH_24', 'dvb:a']);
  assert.deepEqual(now.map((x) => x.number), [1, 2, 3, 5, 6, 7, 8, 9, 10, 11]);
  assert.deepEqual(H.nowItems(list.slice(0, 2)).map((x) => x.number), [1, 2]);
  assert.deepEqual(H.nowItems([]), []);
});

test('Sněmovna: only the visible streams that are live now, with their numbers', () => {
  const list = [item('ct', 'CH_1'), item('psp', 'stream1'), item('psp', 'stream2'), item('psp', 'stream3')];
  const states = { stream1: 'off', stream2: 'live', stream3: 'unknown' };
  assert.deepEqual(H.pspItems(list, (id) => states[id]).map((x) => [x.item.key, x.number]), [['psp:stream2', 3]]);
  assert.deepEqual(H.pspItems(list, () => 'off'), []);
});

test('focus: left/right in a row, up/down between rows keeping the column as far as the row allows, up from the first row leaves', () => {
  const rows = [3, 11, 1];
  assert.deepEqual(H.move(rows, { row: 0, col: 2 }, 'right'), { row: 0, col: 2 });
  assert.deepEqual(H.move(rows, { row: 0, col: 2 }, 'left'), { row: 0, col: 1 });
  assert.deepEqual(H.move(rows, { row: 0, col: 0 }, 'left'), { row: 0, col: 0 });
  assert.deepEqual(H.move(rows, { row: 0, col: 2 }, 'down'), { row: 1, col: 2 });
  assert.deepEqual(H.move(rows, { row: 1, col: 8 }, 'up'), { row: 0, col: 2 });
  assert.deepEqual(H.move(rows, { row: 1, col: 8 }, 'down'), { row: 2, col: 0 });
  assert.deepEqual(H.move(rows, { row: 2, col: 0 }, 'down'), { row: 2, col: 0 });
  assert.equal(H.move(rows, { row: 0, col: 1 }, 'up'), null);
});
