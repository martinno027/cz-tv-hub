'use strict';
// "Kanály" (spec/apps.md) against spec/fixtures/channel_prefs_cases.json.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT, json } = require('./load');

const cases = json('channel_prefs_cases.json');
const P = CT.ChannelPrefs;

test('arrange: golden cases', () => {
  for (const c of cases.arrange) {
    const r = P.arrange(c.channels, c.prefs);
    assert.deepEqual(r.all, c.all, c.name);
    assert.deepEqual(r.visible, c.visible, c.name);
  }
});

test('move: golden cases', () => {
  for (const c of cases.move) {
    const before = JSON.stringify(c.prefs);
    const r = P.move(c.channels, c.prefs, c.key, c.by);
    assert.deepEqual(r.order, c.order, c.name);
    assert.deepEqual(r.hidden, c.prefs.hidden, c.name + ' (hidden unchanged)');
    assert.equal(JSON.stringify(c.prefs), before, c.name + ' (input not mutated)');
  }
});

test('toggle and reset', () => {
  let p = P.toggle(P.empty(), 'ct:CH_2');
  assert.deepEqual(p, { order: [], hidden: ['ct:CH_2'] });
  assert.ok(P.isHidden(p, 'ct:CH_2'));
  p = P.move(['ct:CH_1', 'ct:CH_2'], p, 'ct:CH_2', -1);
  p = P.toggle(p, 'ct:CH_2');
  assert.deepEqual(p, { order: ['ct:CH_2', 'ct:CH_1'], hidden: [] }, 'showing keeps the order');
  assert.deepEqual(P.reset(), { order: [], hidden: [] });
});

test('parse is defensive and serialise round-trips', () => {
  for (const raw of [null, undefined, 'not json', '[1,2]', 42, [], { order: 'x', hidden: { a: 1 } }]) {
    assert.deepEqual(P.parse(raw), { order: [], hidden: [] }, JSON.stringify(raw));
  }
  assert.deepEqual(P.parse({ order: ['ct:CH_1', 5, '', 'ct:CH_1', 'dvb:a'], hidden: ['dvb:a', null] }),
    { order: ['ct:CH_1', 'dvb:a'], hidden: ['dvb:a'] });
  const many = Array.from({ length: 600 }, (_, i) => 'dvb:' + i);
  assert.equal(P.parse({ order: many, hidden: many }).order.length, P.MAX_KEYS);
  const p = { order: ['dvb:b', 'ct:CH_1'], hidden: ['ct:CH_4'] };
  assert.deepEqual(P.parse(P.serialise(p)), p);
});
