'use strict';
// spec/apps.md "TV průvodce": which grid opens, which chip is highlighted, what a chip selects.
const test = require('node:test');
const assert = require('node:assert');
const { CT, json } = require('./load');

const CASES = json('guide_days_cases.json');

test('openDay: the broadcast day containing now', () => {
  for (const c of CASES.openDay) assert.equal(CT.Days.openDay(Date.parse(c.now)), c.day, c.name);
});

test('chipDay: the calendar day of a programme start', () => {
  for (const c of CASES.chipDay) assert.equal(CT.Days.chipDay(Date.parse(c.time)), c.day, c.name);
});

test('select: grid day and anchor of a chip', () => {
  for (const c of CASES.select) {
    const s = CT.Days.select(c.chip, Date.parse(c.now));
    assert.equal(s.day, c.day, c.name);
    assert.equal(s.anchor, c.anchor === 'now' ? 'now' : Date.parse(c.anchor), c.name);
  }
});

test('labels follow the clock: a programme at 0:10 is "Dnes" once midnight has passed', () => {
  const now = Date.parse('2026-10-23T22:20:00Z'); // 00:20 Prague
  const start = Date.parse('2026-10-23T22:10:00Z'); // 00:10
  assert.equal(CT.Days.label(CT.Days.chipDay(start), CT.Days.today(now)), 'Dnes');
  assert.equal(CT.Days.label(CT.Days.chipDay(start), CT.Days.today(now - 30 * 60 * 1000)), 'Zítra');
});
