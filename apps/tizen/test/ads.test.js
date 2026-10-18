'use strict';
// "Přeskočit reklamu" (spec/ad-breaks.md) against the shared fixtures in spec/fixtures/ads/.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT, response, json } = require('./load');

const KEY = 'dev-key-dev-key-dev-key-dev-key-0000';
const config = new CT.DvbConfig('http://192.168.1.5:8098', KEY);
const cases = json('ads/skip_cases.json');

function client(routes, seen) {
  return new CT.DvbClient(config, (req) => {
    if (seen) seen.push(req);
    const hit = routes.find(([part]) => req.url.includes(part));
    if (!hit) return Promise.reject(new Error('no route ' + req.url));
    return Promise.resolve(typeof hit[1] === 'string' ? response(hit[1]) : hit[1]);
  });
}

test('breaks parse like the golden list: junk dropped, sorted, confidence defaults to 1', () => {
  const r = response('ads/breaks_done.json');
  assert.deepEqual(CT.DvbParser.parseBreaks(r.status, r.body), cases.breaks);
  const pending = response('ads/breaks_pending.json');
  assert.deepEqual(CT.DvbParser.parseBreaks(pending.status, pending.body), []);
  const off = response('ads/breaks_off.json');
  assert.throws(() => CT.DvbParser.parseBreaks(off.status, off.body), (e) => e instanceof CT.CtError && e.status === '404');
  assert.throws(() => CT.DvbParser.parseBreaks(200, 'nope'));
});

test('skip target: golden cases', () => {
  assert.equal(CT.AdBreaks.MIN_REMAINING_SECONDS, cases.minRemainingSeconds);
  for (const c of cases.cases) {
    assert.equal(CT.AdBreaks.skipTarget(cases.breaks, c.position), c.skipTo, 'position ' + c.position);
  }
  assert.equal(CT.AdBreaks.skipTarget([], 10), null);
});

test('client asks the recording breaks with the bearer key', async () => {
  const seen = [];
  const list = await client([['/breaks', 'ads/breaks_done.json']], seen).breaks('abc');
  assert.equal(list.length, 3);
  assert.equal(seen[0].url, 'http://192.168.1.5:8098/dvb/recording/abc/breaks');
  assert.equal(seen[0].headers.Authorization, 'Bearer ' + KEY);
});

test('only finished Anténa recordings carry a recording id for breaks', async () => {
  const c = client([['/dvb/', { status: 200, body: '#EXTM3U' }]]);
  const planner = new CT.Planner(new CT.CtApi(() => Promise.reject(new Error('no CT'))), null, Date.now, () => c);
  const finished = await planner.plan({ kind: 'dvbRecorded', channelId: 'c', title: 't', recordingId: 'r1', fromStart: false });
  assert.equal(finished.recordingId, 'r1');
  const growing = await planner.plan({ kind: 'dvbRecorded', channelId: 'c', title: 't', recordingId: 'r1', fromStart: true });
  assert.equal(growing.recordingId, null);
  const live = await planner.plan({ kind: 'dvbLive', channelId: 'c', title: 't' });
  assert.equal(live.recordingId, null);
});
