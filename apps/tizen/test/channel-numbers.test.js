'use strict';
// Channel numbers and zapping (spec/apps.md) against spec/fixtures/channel_numbers_cases.json.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT, json } = require('./load');

const cases = json('channel_numbers_cases.json');
const N = CT.ChannelNumbers;

test('step: golden cases', () => {
  for (const c of cases.step) assert.equal(N.step(c.visible, c.current, c.by), c.key, c.name);
});

test('type: golden cases', () => {
  for (const c of cases.type) {
    assert.deepEqual(N.type(c.buffer, c.digit, c.count), { buffer: c.bufferAfter, commit: c.commit }, c.name);
  }
});

test('resolve: golden cases', () => {
  for (const c of cases.resolve) assert.equal(N.resolve(c.visible, c.number), c.key, c.name);
});
