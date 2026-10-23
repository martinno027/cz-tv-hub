'use strict';
// spec/apps.md "Image prefetch": the window around the focus, its order, and the fetch queue's limits.
const test = require('node:test');
const assert = require('node:assert');
const { CT } = require('./load');

const H = 3600 * 1000;
const M = 60 * 1000;

/** A row of hour-long programmes from 0 h to 24 h, each with its own image key `r<row>@<hour>`. */
function hourRows(count) {
  const rows = [];
  for (let r = 0; r < count; r++) {
    const slots = [];
    for (let h = 0; h < 24; h++) slots.push({ start: h * H, end: (h + 1) * H, item: { key: 'r' + r + '@' + h } });
    rows.push(slots);
  }
  return rows;
}

const imageOf = (item) => (item.key ? { key: item.key, kind: 'ct', final: true } : null);

test('window: rows 3 above to 8 below, ±3 h, nearest first by rows then time', () => {
  const keys = CT.Prefetch.window(hourRows(20), { row: 5, time: 10 * H + 30 * M }, imageOf).map((r) => r.key);
  const rowsSeen = new Set(keys.map((k) => Number(/^r(\d+)/.exec(k)[1])));
  assert.deepEqual([...rowsSeen].sort((a, b) => a - b), [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]);
  // ±3 h around 10:30 → programmes 7..13 (7:00-8:00 ends 2.5 h before, 13:00 starts 2.5 h after)
  assert.deepEqual(keys.filter((k) => k.startsWith('r5@')), ['r5@10', 'r5@9', 'r5@11', 'r5@8', 'r5@12', 'r5@7', 'r5@13']);
  assert.equal(keys[0], 'r5@10'); // the focused programme first
  assert.deepEqual(keys.slice(7, 9), ['r6@10', 'r4@10']); // then the next rows, below before above
  assert.equal(keys.length, 12 * 7);
});

test('window: clipped at the grid edges, fillers and missing images skipped, each key once', () => {
  const rows = hourRows(3);
  rows[1][2] = { start: 2 * H, end: 3 * H, item: null }; // "Mimo vysílání"
  rows[1][1].item = { key: '' }; // no image
  rows[2][1].item = { key: 'r0@1' }; // same image as another programme
  const keys = CT.Prefetch.window(rows, { row: 0, time: 1 * H + 10 * M }, imageOf).map((r) => r.key);
  assert.ok(!keys.some((k) => k.startsWith('r1@1') || k.startsWith('r1@2')));
  assert.equal(keys.filter((k) => k === 'r0@1').length, 1);
  assert.equal(keys[0], 'r0@1');
});

test('moved: a new window after more than 1 h or 3 rows', () => {
  const last = { row: 4, time: 10 * H };
  assert.equal(CT.Prefetch.moved(null, last), true);
  assert.equal(CT.Prefetch.moved(last, { row: 7, time: 11 * H }), false);
  assert.equal(CT.Prefetch.moved(last, { row: 8, time: 10 * H }), true);
  assert.equal(CT.Prefetch.moved(last, { row: 1, time: 10 * H }), false);
  assert.equal(CT.Prefetch.moved(last, { row: 4, time: 11 * H + M }), true);
});

/** An ImageLoader whose fetches wait until the test answers them. */
function loader(opts) {
  const pending = [];
  const revoked = [];
  let now = 0;
  const l = new CT.ImageLoader(Object.assign({
    fetch: (req) => new Promise((resolve) => pending.push({ req, resolve })),
    revoke: (url) => revoked.push(url),
    now: () => now
  }, opts || {}));
  return { l, pending, revoked, setNow: (t) => { now = t; } };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
const ct = (n) => ({ key: 'ct:' + n, kind: 'ct', final: true });
const gw = (n, final) => ({ key: 'gw:' + n, kind: 'gw', final: final !== false });

function answer(p, key, status) {
  const i = p.pending.findIndex((x) => x.req.key === key);
  const [x] = p.pending.splice(i, 1);
  x.resolve({ status, url: status === 200 ? 'blob:' + key : null });
}

test('loader: at most 4 CT and 2 gateway requests at a time, the focused one ahead of the queue', async () => {
  const p = loader();
  p.l.prefetch([ct(1), ct(2), ct(3), ct(4), ct(5), ct(6), gw(1), gw(2), gw(3)]);
  assert.deepEqual(p.pending.map((x) => x.req.key), ['ct:1', 'ct:2', 'ct:3', 'ct:4', 'gw:1', 'gw:2']);
  let shown = null;
  p.l.load(gw(9), (url) => { shown = url; });
  answer(p, 'gw:1', 200);
  await tick();
  assert.equal(p.pending[p.pending.length - 1].req.key, 'gw:9'); // before gw:3
  answer(p, 'gw:9', 200);
  await tick();
  assert.equal(shown, 'blob:gw:9');
  assert.equal(p.l.load(gw(9)), 'blob:gw:9'); // cached
});

test('loader: requests that left the window are dropped, running ones finish', async () => {
  const p = loader({ limits: { ct: 1, gw: 1 } });
  p.l.prefetch([ct(1), ct(2), ct(3)]);
  p.l.prefetch([ct(4)]);
  answer(p, 'ct:1', 200);
  await tick();
  assert.deepEqual(p.pending.map((x) => x.req.key), ['ct:4']);
  assert.equal(p.l.get('ct:1'), 'blob:ct:1');
});

test('loader: a 404 is not asked again (finished recording) or for 60 s (growing recording)', async () => {
  const p = loader();
  p.l.prefetch([gw('done'), gw('rec', false)]);
  answer(p, 'gw:done', 404);
  answer(p, 'gw:rec', 404);
  await tick();
  p.setNow(CT.Prefetch.RETRY_MS - 1);
  p.l.prefetch([gw('done'), gw('rec', false)]);
  assert.equal(p.pending.length, 0);
  let status = null;
  p.l.load(gw('done'), (url, s) => { status = s; });
  assert.equal(status, 0); // answered at once, no request
  p.setNow(CT.Prefetch.RETRY_MS);
  p.l.prefetch([gw('done'), gw('rec', false)]);
  assert.deepEqual(p.pending.map((x) => x.req.key), ['gw:rec']);
});

test('loader: LRU of about 300 images, evicted and cleared blob URLs revoked', async () => {
  const p = loader({ max: 2, limits: { ct: 10, gw: 10 } });
  p.l.prefetch([ct(1), ct(2), ct(3), gw(1)]);
  ['ct:1', 'ct:2', 'ct:3', 'gw:1'].forEach((k) => answer(p, k, 200));
  await tick();
  assert.deepEqual(p.revoked, ['blob:ct:1', 'blob:ct:2']);
  p.l.clear('gw'); // thumbnails switched off: CT images stay
  assert.deepEqual(p.revoked, ['blob:ct:1', 'blob:ct:2', 'blob:gw:1']);
  assert.equal(p.l.get('ct:3'), 'blob:ct:3');
  assert.equal(CT.Prefetch.MAX_IMAGES, 300);
});

test('loader: an answer arriving after clear() is revoked, not cached', async () => {
  const p = loader();
  p.l.prefetch([gw(1), ct(1)]);
  p.l.clear('gw');
  answer(p, 'gw:1', 200);
  answer(p, 'ct:1', 200);
  await tick();
  assert.deepEqual(p.revoked, ['blob:gw:1']);
  assert.equal(p.l.get('gw:1'), null);
  assert.equal(p.l.get('ct:1'), 'blob:ct:1');
});
