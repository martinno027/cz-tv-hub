'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { CT, response } = require('./load');

function dvbEntry(state) {
  return { source: 'dvb', item: { recording: state ? { id: 'rec-1', state: state } : null } };
}

test('only completed or still recording Anténa programmes have a thumbnail', () => {
  assert.equal(CT.Thumbs.recordingId(dvbEntry('completed')), 'rec-1');
  assert.equal(CT.Thumbs.recordingId(dvbEntry('recording')), 'rec-1');
  assert.equal(CT.Thumbs.recordingId(dvbEntry('scheduled')), null);
  assert.equal(CT.Thumbs.recordingId(dvbEntry('failed')), null);
  assert.equal(CT.Thumbs.recordingId(dvbEntry(null)), null);
  assert.equal(CT.Thumbs.recordingId({ source: 'ct', item: {} }), null);
  assert.equal(CT.Thumbs.recordingId(null), null);
});

test('both the gateway and this TV must have them on', () => {
  const entry = dvbEntry('completed');
  assert.equal(CT.Thumbs.wanted(true, true, entry), 'rec-1');
  assert.equal(CT.Thumbs.wanted(false, true, entry), null);
  assert.equal(CT.Thumbs.wanted(undefined, true, entry), null);
  assert.equal(CT.Thumbs.wanted(true, false, entry), null);
  assert.equal(CT.Thumbs.path('a b'), '/dvb/recording/a%20b/thumb.jpg');
});

test('memory cache: LRU, refresh of growing recordings, every evicted URL revoked', () => {
  const revoked = [];
  const cache = new CT.ThumbCache(2, (url) => revoked.push(url));
  cache.put('a', 'blob:a', 0, true);
  cache.put('b', 'blob:b', 0, false);
  assert.equal(cache.get('a', 1000), 'blob:a'); // a is now the newest
  cache.put('c', 'blob:c', 1000, true);
  assert.deepEqual(revoked, ['blob:b']);
  assert.equal(cache.get('b', 1000), null);
  cache.put('g', 'blob:g1', 0, false);
  assert.deepEqual(revoked, ['blob:b', 'blob:a']);
  assert.equal(cache.get('g', CT.Thumbs.REFRESH_MS - 1), 'blob:g1');
  assert.equal(cache.get('g', CT.Thumbs.REFRESH_MS), null); // growing recording: ask again
  cache.put('g', 'blob:g2', CT.Thumbs.REFRESH_MS, true);
  assert.deepEqual(revoked, ['blob:b', 'blob:a', 'blob:g1']);
  cache.clear();
  assert.deepEqual(revoked.slice(3).sort(), ['blob:c', 'blob:g2']);
  assert.equal(cache.get('c', 0), null);
});

test('status: thumbnails flag, missing = off', () => {
  const st = CT.DvbParser.parseStatus(200, JSON.stringify({ enabled: true, thumbnails: true }));
  assert.equal(st.thumbnails, true);
  const old = CT.DvbParser.parseStatus(response('dvb/dvb_status.json').status, response('dvb/dvb_status.json').body);
  assert.equal(old.thumbnails, false);
});
