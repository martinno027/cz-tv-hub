'use strict';
// "Pokračovat" (spec/apps.md) against spec/fixtures/resume_cases.json.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT, response, json } = require('./load');

const cases = json('resume_cases.json');
const DAY = 24 * 3600 * 1000;

function memory() {
  let data = null;
  return { load: () => data, save: (d) => { data = JSON.parse(JSON.stringify(d)); }, peek: () => data };
}

test('rules and labels: golden cases', () => {
  assert.equal(CT.ResumeRules.MIN_SECONDS, cases.minSeconds);
  assert.equal(CT.ResumeRules.END_MARGIN_SECONDS, cases.endMarginSeconds);
  assert.equal(CT.ResumeRules.MAX_ENTRIES, cases.maxEntries);
  assert.equal(CT.ResumeRules.MAX_AGE_DAYS, cases.maxAgeDays);
  for (const c of cases.offer) {
    assert.equal(CT.ResumeRules.decide(c.progress, c.length), c.action, JSON.stringify(c));
    if (c.label) assert.equal(CT.ResumeRules.label(c.progress), c.label);
  }
});

test('store: offer, clear when watched, ignore short, age and size limits', () => {
  let now = Date.parse('2026-10-18T12:00:00Z');
  const backend = memory();
  const store = new CT.ResumeStore(backend, () => now);
  store.save('ct:1', 1390, 3600);
  assert.deepEqual(store.offer('ct:1'), { progress: 1390, label: '23:10' });
  store.save('ct:1', 3500, 3600);
  assert.equal(store.offer('ct:1'), null);
  assert.deepEqual(Object.keys(backend.peek()), []);
  store.save('ct:2', 30, 3600);
  assert.equal(store.offer('ct:2'), null);
  store.save('dvb:r', 600, 1800);
  now += 31 * DAY;
  assert.equal(store.offer('dvb:r'), null);
  store.save('ct:3', 100, 3600);
  assert.deepEqual(Object.keys(backend.peek()), ['ct:3']);
  for (let i = 0; i < 205; i++) {
    now += 1000;
    store.save('ct:n' + i, 100, 3600);
  }
  const kept = Object.keys(backend.peek());
  assert.equal(kept.length, 200);
  assert.ok(kept.includes('ct:n204') && !kept.includes('ct:3'));
  assert.equal(new CT.ResumeStore({ load: () => '[1]', save() {} }).offer('x'), null);
});

test('planner: start-over resume golden cases', async () => {
  for (const c of cases.startover) {
    const now = Date.parse(c.now);
    const api = new CT.CtApi((req) => Promise.resolve(response('live_CH_1.json')));
    const planner = new CT.Planner(api, null, () => now);
    const t = await planner.plan({ kind: 'startover', channelId: 'CH_1', title: 'x', start: Date.parse(c.start),
      resumeSeconds: c.progress });
    assert.equal(t.seekToWallClock, Date.parse(c.expect.seekToWallClock), c.name);
    assert.equal(t.fallbackOffsetMs, c.expect.fallbackOffsetSeconds * 1000, c.name);
  }
});

test('planner: archive and Anténa recordings start at the progress', async () => {
  const now = Date.parse('2026-10-17T18:00:00Z');
  const vod = await new CT.Planner(new CT.CtApi(() => Promise.resolve(response('vod_ok.json'))), null, () => now).plan({
    kind: 'programme', channelId: 'CH_1', title: 'x', idec: '1', start: Date.parse('2026-10-17T08:00:00Z'),
    end: Date.parse('2026-10-17T09:00:00Z'), isPlayableNow: true, resumeSeconds: 1390 });
  assert.equal(vod.startPositionMs, 1390 * 1000);
  const c = new CT.DvbClient(new CT.DvbConfig('http://pi:8098', 'k'.repeat(32)),
    () => Promise.resolve({ status: 200, body: '#EXTM3U' }));
  const planner = new CT.Planner(new CT.CtApi(() => Promise.reject(new Error('x'))), null, () => now, () => c);
  const rec = await planner.plan({ kind: 'dvbRecorded', channelId: 'c', title: 't', recordingId: 'r', fromStart: false,
    resumeSeconds: 600 });
  assert.equal(rec.startPositionMs, 600 * 1000);
});

test('selection offers Pokračovat first when this TV remembers a position', () => {
  const now = Date.parse('2026-10-17T18:00:00Z');
  const store = new CT.ResumeStore(memory(), () => now);
  const ch = CT.Channels.byId('CH_1');
  const p = (idec, from, to) => CT.Entries.ct(ch, { idec, title: 'T', episodeTitle: '', description: '', genre: '',
    imageUrl: '', start: Date.parse(from), end: Date.parse(to), isPlayableNow: true });
  const ended = p('a', '2026-10-17T08:00:00Z', '2026-10-17T09:00:00Z');
  const airing = p('b', '2026-10-17T17:30:00Z', '2026-10-17T19:00:00Z');

  const plain = CT.Entries.select(ended, now, store);
  assert.equal(plain.type, 'play');
  assert.equal(plain.request.resumeKey, 'ct:a');
  assert.equal(plain.request.lengthSeconds, 3600);

  store.save('ct:a', 1390, 3600);
  store.save('ct:b', 600, 5400);
  const s1 = CT.Entries.select(ended, now, store);
  assert.equal(s1.type, 'choice');
  assert.equal(s1.resumeLabel, '23:10');
  assert.equal(s1.resume.resumeSeconds, 1390);
  assert.equal(s1.resume.kind, 'programme');
  assert.equal(s1.fromStart.resumeSeconds, undefined);
  assert.equal(s1.live, null);

  const s2 = CT.Entries.select(airing, now, store);
  assert.deepEqual([s2.resume.kind, s2.fromStart.kind, s2.live.kind], ['startover', 'startover', 'live']);
  assert.equal(s2.live.resumeKey, undefined);

  const rec = CT.Entries.dvb({ id: 'c', number: 1, name: 'Nova HD', iconPath: null }, { id: 'e', channelId: 'c', title: 't',
    subtitle: '', description: '', genre: '', start: Date.parse('2026-10-17T10:00:00Z'),
    end: Date.parse('2026-10-17T10:30:00Z'), recording: { id: 'r1', state: 'completed' } });
  store.save('dvb:r1', 300, 1800);
  const s3 = CT.Entries.select(rec, now, store);
  assert.deepEqual([s3.resume.kind, s3.resume.recordingId, s3.resume.resumeSeconds, s3.resumeLabel],
    ['dvbRecorded', 'r1', 300, '5:00']);
});
