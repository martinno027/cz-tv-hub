'use strict';
// spec/apps.md "Clock (T22)": the header clock's text and when it changes next.
const test = require('node:test');
const assert = require('node:assert');
const { CT, json } = require('./load');

const CASES = json('clock_cases.json');

test('clock: Prague H:MM whatever the device zone, DST included', () => {
  assert.ok(CASES.format.length >= 7);
  for (const c of CASES.format) assert.equal(CT.Days.clock(Date.parse(c.utc)), c.text, c.name);
});

test('untilNextMinute: the next update falls on the minute boundary', () => {
  const at = (iso) => CT.Days.untilNextMinute(Date.parse(iso));
  assert.equal(at('2026-10-25T13:30:00.000Z'), 60000);
  assert.equal(at('2026-10-25T13:30:00.001Z'), 59999);
  assert.equal(at('2026-10-25T13:30:59.990Z'), 10);
  assert.equal(at('2026-10-25T13:30:42.000Z'), 18000);
});
