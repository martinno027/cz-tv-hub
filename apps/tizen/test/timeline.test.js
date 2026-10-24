'use strict';
// Anténa timeline player (spec/apps.md "Anténa timeline player") against spec/fixtures/timeline_cases.json.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { CT, json } = require('./load');

const T = CT.Timeline;
const cases = json('timeline_cases.json');
const at = (iso) => Date.parse(iso);
const pieces = cases.map.pieces.map((p) => ({ position: p.position, time: at(p.time), duration: p.duration }));
const programmes = cases.programmes.map((p) => ({ title: p.title, start: at(p.start), end: at(p.end) }));
const KEY = 'dev-key-dev-key-dev-key-dev-key-0000';
const config = new CT.DvbConfig('http://192.168.1.5:8098', KEY);
const SAMPLE = fs.readFileSync(path.join(__dirname, '..', '..', '..', 'spec', 'fixtures', 'dvb', 'dvb_timeline.json'), 'utf8');

test('map: position to wall clock', () => {
  for (const c of cases.map.toTime) assert.equal(T.toTime(pieces, c.position), at(c.time), c.name);
});

test('map: wall clock to position', () => {
  for (const c of cases.map.toPosition) assert.equal(T.toPosition(pieces, at(c.time)), c.position, c.name);
});

test('map: a growing window and a seek out of the window run on with the position', () => {
  assert.equal(T.extrapolate(pieces, 5460), at('2026-10-24T10:36:00Z'));
  assert.equal(T.extrapolate(pieces, -20), at('2026-10-24T08:59:40Z'));
  assert.equal(T.extrapolate(pieces, 4000), T.toTime(pieces, 4000));
  assert.equal(T.duration(pieces), 5400);
  assert.deepEqual(T.span(pieces), { start: at('2026-10-24T09:00:00Z'), end: at('2026-10-24T10:35:00Z') });
});

test('edge: seek, previous, next or stay', () => {
  for (const c of cases.edge) {
    assert.equal(T.edge(c.target, c.duration, c.complete, c.previous, c.next), c.result, c.name);
  }
});

test('hold: offset of a held left/right', () => {
  for (const c of cases.hold) assert.ok(Math.abs(T.holdOffset(c.held) - c.offset) < 1e-9, c.name);
});

test('labels: playhead wall clock in Prague, day against now', () => {
  for (const c of cases.labels) assert.equal(T.label(at(c.time), at(c.now)), c.label, c.name);
});

test('edges: end labels, day only when not the playhead day', () => {
  for (const c of cases.edges) {
    const e = T.edgeLabels(at(c.start), at(c.end), at(c.playhead), at(c.now), false);
    assert.deepEqual(e, { start: c.startLabel, end: c.endLabel }, c.name);
  }
});

test('edges: a growing window ends at "Teď" only while its last piece is within 30 s of now', () => {
  const now = at('2026-10-24T19:00:00Z');
  const start = at('2026-10-24T14:00:00Z');
  assert.equal(T.edgeLabels(start, now - 30 * 1000, now, now, true).end, 'Teď');
  assert.equal(T.edgeLabels(start, now - 31 * 1000, now, now, true).end, '20:59');
  assert.equal(T.edgeLabels(start, now - 10 * 1000, now, now, false).end, '20:59');
});

test('programmeAt: index or null in a gap', () => {
  for (const c of cases.programmeAt) assert.equal(T.programmeAt(programmes, at(c.time)), c.index, c.name);
  // the end itself is not the programme any more: a gap follows "Film"
  assert.equal(T.programmeAt(programmes, at('2026-10-24T11:00:00Z')), null);
});

test('jump: up and down', () => {
  for (const c of cases.jump) {
    const target = T.jump(programmes, at(c.time), c.direction);
    assert.equal(target, c.target === null ? null : at(c.target), c.name);
  }
});

test('resume: progress from the programme start', () => {
  for (const c of cases.resume) {
    const p = { start: at(c.programme.start), end: at(c.programme.end) };
    assert.equal(T.progress(p, at(c.time)), c.progress, c.name);
  }
});

test('timeline ids: only the continuous recording, never a DVR uuid that starts with b', () => {
  assert.ok(T.isTimelineId('bnova1-1792832400-1792834200'));
  assert.ok(!T.isTimelineId('b7e0c3a1f2d94c56a8b1e2f3a4b5c6d7'));
  assert.ok(!T.isTimelineId('185a4840a5d3f95fbe885ad76f623d15'));
  assert.ok(!T.isTimelineId(null));
});

test('parse: the sample gateway JSON, unknown fields ignored', () => {
  const tl = T.parse(200, SAMPLE);
  assert.equal(tl.id, 'bnova1-1792832400-1792834200');
  assert.equal(tl.channel, 'nova1');
  assert.equal(tl.playlist, '/dvb/timeline/bnova1-1792832400-1792834200.m3u8');
  assert.equal(tl.complete, true);
  assert.deepEqual(tl.programme, { start: at('2026-10-24T09:00:00Z'), end: at('2026-10-24T09:30:00Z') });
  assert.deepEqual(tl.pieces, [
    { position: 0, time: at('2026-10-24T06:00:00Z'), duration: 10800 },
    { position: 10800, time: at('2026-10-24T09:05:00Z'), duration: 10500.5 }
  ]);
  assert.equal(tl.programmes.length, 4);
  assert.deepEqual(tl.programmes[1], {
    id: 'bnova1-1792832400-1792834200', title: 'Zprávy', subtitle: 'Ranní vydání',
    start: at('2026-10-24T09:00:00Z'), end: at('2026-10-24T09:30:00Z'), breaks: true
  });
  assert.equal(tl.programmes[2].id, null);
  assert.equal(tl.previous, 'bnova1-1792810800-1792821600');
  assert.equal(tl.next, null);
  assert.ok(!('unknownFieldIsIgnored' in tl));
});

test('parse: bad values are an error, not a crash', () => {
  const base = JSON.parse(SAMPLE);
  const broken = (change) => {
    const o = JSON.parse(JSON.stringify(base));
    change(o);
    return JSON.stringify(o);
  };
  const bad = [
    'not json', '[]', 'null',
    broken((o) => { delete o.playlist; }),
    broken((o) => { o.playlist = 'http://elsewhere.example/x.m3u8'; }),
    broken((o) => { o.playlist = '//elsewhere.example/x.m3u8'; }),
    broken((o) => { o.pieces = []; }),
    broken((o) => { o.pieces = 'x'; }),
    broken((o) => { o.pieces[0].time = 'yesterday'; }),
    broken((o) => { o.pieces[1].duration = '10'; }),
    broken((o) => { o.pieces[1].position = -1; }),
    broken((o) => { o.pieces.reverse(); }),
    broken((o) => { o.pieces[0] = null; }),
    broken((o) => { delete o.programme; }),
    broken((o) => { o.programme.end = o.programme.start; }),
    broken((o) => { o.previous = 5; })
  ];
  for (const body of bad) assert.throws(() => T.parse(200, body), (e) => e instanceof CT.CtError, body.slice(0, 60));
  // EPG entries without valid times are dropped like in the guide; `breaks` needs an id
  const lenient = T.parse(200, broken((o) => {
    o.programmes.push(null, { title: 'x' }, { id: null, title: 'y', start: o.programme.start, end: o.programme.end, breaks: true });
    o.complete = 'true';
  }));
  assert.equal(lenient.programmes.length, 5);
  assert.equal(lenient.programmes.filter((p) => p.title === 'y')[0].breaks, false);
  assert.equal(lenient.complete, false);
  assert.throws(() => T.parse(404, '{"error": "Nahrávka není k dispozici"}'),
    (e) => e.userMessage === 'Nahrávka není k dispozici' && e.status === '404');
});

test('open time: "Od začátku" 30 s before the start, "Pokračovat" at start + progress', () => {
  const tl = T.parse(200, SAMPLE);
  assert.equal(T.openTime(tl, undefined), at('2026-10-24T08:59:30Z'));
  assert.equal(T.openTime(tl, 750), at('2026-10-24T09:12:30Z'));
  // 08:59:30 is inside the first piece; 09:12:30 is after the gap, in the second one
  assert.equal(T.toPosition(tl.pieces, T.openTime(tl)), 10770);
  assert.equal(T.toPosition(tl.pieces, T.openTime(tl, 750)), 10800 + 450);
});

test('ad-break skip: positions from programme start − 30 s, as wall clock', () => {
  const p = { start: at('2026-10-24T09:00:00Z') };
  const breaks = [{ start: 330, end: 450, confidence: 1 }];
  assert.equal(T.skipTime(p, breaks, at('2026-10-24T09:05:00Z')), at('2026-10-24T09:07:00Z'));
  assert.equal(T.skipTime(p, breaks, at('2026-10-24T09:06:58Z')), null); // < 3 s before the end
  assert.equal(T.skipTime(p, breaks, at('2026-10-24T09:04:59Z')), null);
});

function gateway(routes, seen) {
  return new CT.DvbClient(config, (req) => {
    seen.push(req);
    const hit = routes.find(([part]) => req.url.includes(part));
    if (!hit) return Promise.reject(new Error('no route ' + req.url));
    return Promise.resolve(hit[1]);
  });
}

test('planner: a continuous recording opens the timeline at the mapped position, with the key', async () => {
  const seen = [];
  const c = gateway([['.m3u8', { status: 200, body: '#EXTM3U' }], ['/dvb/timeline/', { status: 200, body: SAMPLE }]], seen);
  const planner = new CT.Planner(new CT.CtApi(() => Promise.reject(new Error('no CT'))), null, Date.now, () => c);
  const id = 'bnova1-1792832400-1792834200';
  const t = await planner.plan({ kind: 'dvbRecorded', channelId: 'nova1', title: 'Zprávy', recordingId: id, fromStart: true });
  assert.equal(seen[0].url, 'http://192.168.1.5:8098/dvb/timeline/' + id);
  assert.equal(seen[0].headers.Authorization, 'Bearer ' + KEY);
  assert.equal(t.kind, 'timeline');
  assert.equal(t.alwaysSeek, true);
  assert.equal(t.url, 'http://192.168.1.5:8098/dvb/timeline/' + id + '.m3u8');
  assert.equal(t.startPositionMs, 10770 * 1000);
  assert.equal(t.timeline.id, id);
  assert.equal(t.dvb, c);
  const resumed = await planner.plan({ kind: 'dvbRecorded', channelId: 'nova1', title: 'Zprávy', recordingId: id, fromStart: false, resumeSeconds: 750 });
  assert.equal(resumed.startPositionMs, 11250 * 1000);
  const next = await planner.timelineWindow(id, 'Zprávy', at('2026-10-24T07:00:00Z'));
  assert.equal(next.startPositionMs, 3600 * 1000);
});

test('planner: a TVHeadend DVR recording keeps the recording playlist', async () => {
  const seen = [];
  const c = gateway([['/dvb/', { status: 200, body: '#EXTM3U' }]], seen);
  const planner = new CT.Planner(new CT.CtApi(() => Promise.reject(new Error('no CT'))), null, Date.now, () => c);
  const t = await planner.plan({ kind: 'dvbRecorded', channelId: 'c', title: 't', recordingId: 'b7e0c3a1f2d94c56a8b1e2f3a4b5c6d7', fromStart: false });
  assert.equal(t.kind, 'vod');
  assert.equal(seen[0].url, 'http://192.168.1.5:8098/dvb/recording/b7e0c3a1f2d94c56a8b1e2f3a4b5c6d7.m3u8');
});

test('planner: a timeline the gateway refuses shows its Czech text', async () => {
  const c = gateway([['/dvb/timeline/', { status: 404, body: '{"error": "Nahrávka není k dispozici"}' }]], []);
  const planner = new CT.Planner(new CT.CtApi(() => Promise.reject(new Error('no CT'))), null, Date.now, () => c);
  await assert.rejects(planner.plan({ kind: 'dvbRecorded', channelId: 'c', title: 't', recordingId: 'bnova1-1792832400-1792834200' }),
    (e) => e.userMessage === 'Nahrávka není k dispozici');
});
