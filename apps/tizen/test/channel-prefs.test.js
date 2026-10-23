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
  assert.deepEqual(p, { order: [], hidden: ['ct:CH_2'], sourcesOff: [] });
  assert.ok(P.isHidden(p, 'ct:CH_2'));
  p = P.move(['ct:CH_1', 'ct:CH_2'], p, 'ct:CH_2', -1);
  p = P.toggle(p, 'ct:CH_2');
  assert.deepEqual(p, { order: ['ct:CH_2', 'ct:CH_1'], hidden: [], sourcesOff: [] }, 'showing keeps the order');
  assert.deepEqual(P.reset(), { order: [], hidden: [], sourcesOff: [] });
});

test('parse is defensive and serialise round-trips', () => {
  for (const raw of [null, undefined, 'not json', '[1,2]', 42, [], { order: 'x', hidden: { a: 1 } }]) {
    assert.deepEqual(P.parse(raw), { order: [], hidden: [], sourcesOff: [] }, JSON.stringify(raw));
  }
  assert.deepEqual(P.parse({ order: ['ct:CH_1', 5, '', 'ct:CH_1', 'dvb:a'], hidden: ['dvb:a', null] }),
    { order: ['ct:CH_1', 'dvb:a'], hidden: ['dvb:a'], sourcesOff: [] });
  const many = Array.from({ length: 600 }, (_, i) => 'dvb:' + i);
  assert.equal(P.parse({ order: many, hidden: many }).order.length, P.MAX_KEYS);
  const p = { order: ['dvb:b', 'ct:CH_1'], hidden: ['ct:CH_4'], sourcesOff: ['dvb'] };
  assert.deepEqual(P.parse(P.serialise(p)), p);
});

test('sources on and off', () => {
  let p = P.parse({ order: ['dvb:a', 'ct:CH_1'], hidden: ['dvb:b'] });
  assert.equal(P.isSourceOff(p, 'dvb'), false, 'missing sourcesOff = both on');
  p = P.setSource(p, 'dvb', false);
  assert.ok(P.isSourceOff(p, 'dvb'));
  assert.deepEqual(p, { order: ['dvb:a', 'ct:CH_1'], hidden: ['dvb:b'], sourcesOff: ['dvb'] }, 'order and hidden stay');
  p = P.setSource(P.setSource(p, 'dvb', false), 'ct', false);
  assert.deepEqual(p.sourcesOff, ['dvb', 'ct'], 'no duplicates');
  assert.deepEqual(P.setSource(p, 'dvb', true).sourcesOff, ['ct']);
  assert.deepEqual(P.setSource(p, 'xyz', false).sourcesOff, ['dvb', 'ct'], 'unknown source ignored');
  assert.deepEqual(P.parse({ sourcesOff: ['dvb', 'tv', 5, 'dvb'] }).sourcesOff, ['dvb']);
  // moving and toggling keep the switches; reset turns both sources on again
  assert.deepEqual(P.move(['ct:CH_1', 'ct:CH_2'], p, 'ct:CH_2', -1).sourcesOff, ['dvb', 'ct']);
  assert.deepEqual(P.toggle(p, 'ct:CH_1').sourcesOff, ['dvb', 'ct']);
  assert.deepEqual(P.reset().sourcesOff, []);
});
