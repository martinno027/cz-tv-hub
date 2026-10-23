'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { CT, json } = require('./load');

const cases = json('ct_antenna_match.json');

test('channel name → CT slugs (golden cases)', () => {
  cases.channels.forEach((c) => assert.deepStrictEqual(CT.CtAntenna.slugs(c.name), c.slugs, c.name));
});

test('Anténa event → CT programme (golden cases)', () => {
  const bySlug = {};
  cases.programmes.schedule.forEach((p) => {
    (bySlug[p.slug] = bySlug[p.slug] || []).push({ idec: p.idec, title: p.title, imageUrl: p.imageUrl,
      start: Date.parse(p.start), end: Date.parse(p.end) });
  });
  cases.programmes.cases.forEach((c) => {
    const got = CT.CtAntenna.programme(c.channel, c.title, Date.parse(c.start), Date.parse(c.end), bySlug);
    assert.strictEqual(got ? got.idec : null, c.idec, c.name);
  });
});
