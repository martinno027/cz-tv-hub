'use strict';
// "Poslanecká sněmovna" (spec/apps.md) against spec/fixtures/psp_cases.json. No test contacts psp.cz or its CDN:
// every fetch here is a fake that records what it was asked.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT, json } = require('./load');

const cases = json('psp_cases.json');
const P = CT.Psp;

/** A fake fetch with a fake clock: answers from `responses[id]` (a {status, body}, 'network' or a function). */
function rig(responses) {
  const clock = { now: 1000000 };
  const calls = [];
  const fetch = (...args) => {
    const stream = args[0];
    calls.push({ id: stream.id, url: stream.url, at: clock.now, args: args.length });
    const r = typeof responses[stream.id] === 'function' ? responses[stream.id]() : responses[stream.id];
    if (!r || r === 'network') return Promise.reject(new Error('network error'));
    return Promise.resolve(r);
  };
  const monitor = new CT.PspMonitor(fetch, () => clock.now);
  return { clock, calls, monitor };
}

const LIVE = cases.live.find((c) => c.live && /segments/.test(c.name));
const OFF = cases.live.find((c) => c.status === 404);
const ERR = cases.live.find((c) => c.status >= 500);
const ids = P.streams.map((s) => s.id);

test('catalogue: keys, names and URLs exactly as the fixture', () => {
  assert.deepEqual(P.streams.map((s) => ({ key: s.key, name: s.name, url: s.url })), cases.streams);
  for (const s of P.streams) {
    assert.equal(s.key, 'psp:' + s.id);
    assert.equal(P.byId(s.id), s);
  }
  assert.equal(P.byId('stream6'), null);
  assert.equal(P.CHECK_MS, cases.checkIntervalSeconds * 1000);
  assert.equal(P.ERROR_MS, cases.errorIntervalSeconds * 1000);
  assert.equal(P.STALE_MS, cases.staleLiveSeconds * 1000);
});

test('isLive: golden cases', () => {
  for (const c of cases.live) assert.equal(P.isLive(c.status, c.body), c.live, c.name);
  assert.equal(P.isLive(200, null), false);
  assert.equal(P.isLive(0, ''), false);
});

test('arrange with three sources: golden cases', () => {
  for (const c of cases.arrange) {
    const r = CT.ChannelPrefs.arrange(c.channels, c.prefs);
    assert.deepEqual(r.all, c.all, c.name);
    assert.deepEqual(r.visible, c.visible, c.name);
  }
});

test('sources: golden cases (missing sourcesOff = all on, psp can be switched off)', () => {
  for (const c of cases.sources) {
    const prefs = CT.ChannelPrefs.parse(c.sourcesOff === null ? {} : { sourcesOff: c.sourcesOff });
    const on = ['ct', 'dvb', 'psp'].filter((s) => !CT.ChannelPrefs.isSourceOff(prefs, s));
    assert.deepEqual(on, c.on, c.name);
  }
  let p = CT.ChannelPrefs.setSource(CT.ChannelPrefs.empty(), 'psp', false);
  assert.deepEqual(p.sourcesOff, ['psp']);
  assert.deepEqual(CT.ChannelPrefs.parse(CT.ChannelPrefs.serialise(p)).sourcesOff, ['psp'], 'stored and read back');
  p = CT.ChannelPrefs.setSource(p, 'psp', true);
  assert.deepEqual(p.sourcesOff, []);
});

test('monitor: unknown until checked, streams one after another, each at most once per 60 s', async () => {
  const { clock, calls, monitor } = rig({ stream1: LIVE, stream2: OFF, stream3: OFF, stream4: OFF, stream5: OFF });
  assert.deepEqual(ids.map((id) => monitor.state(id)), ['unknown', 'unknown', 'unknown', 'unknown', 'unknown']);
  const first = monitor.tick(ids);
  assert.ok(first);
  assert.equal(monitor.tick(ids), null, 'nothing new while a check runs');
  await first;
  assert.deepEqual(calls.map((c) => c.id), ['stream1']);
  for (let i = 0; i < 4; i++) await monitor.tick(ids);
  assert.deepEqual(calls.map((c) => c.id), ids, 'each stream once, in catalogue order');
  assert.deepEqual(ids.map((id) => monitor.state(id)), ['live', 'off', 'off', 'off', 'off']);
  for (const c of calls) {
    assert.equal(c.url, P.byId(c.id).url, 'only the playlist URL itself');
    assert.equal(c.args, 1, 'no headers or options passed');
  }
  assert.equal(monitor.tick(ids), null, 'nothing due before 60 s');
  clock.now += 59 * 1000;
  assert.equal(monitor.tick(ids), null);
  clock.now += 1000;
  await monitor.tick(ids);
  assert.equal(calls.length, 6);
  assert.equal(calls[5].id, 'stream1');
});

test('monitor: only the streams it is given (visible ones) are checked', async () => {
  const { calls, monitor } = rig({ stream2: OFF });
  await monitor.tick(['stream2']);
  assert.equal(monitor.tick(['stream2']), null);
  assert.equal(monitor.tick([]), null, 'source off or nothing visible: no request');
  assert.deepEqual(calls.map((c) => c.id), ['stream2']);
});

test('monitor: errors are retried after 120 s; a network error keeps the state, live goes stale after 180 s', async () => {
  let answer = LIVE;
  const { clock, calls, monitor } = rig({ stream1: () => answer });
  await monitor.tick(['stream1']);
  assert.equal(monitor.state('stream1'), 'live');

  answer = 'network';
  clock.now += 60 * 1000;
  await monitor.tick(['stream1']);
  assert.equal(monitor.state('stream1'), 'live', 'network error keeps the last state (60 s old)');
  clock.now += 119 * 1000;
  assert.equal(monitor.tick(['stream1']), null, 'retried after 120 s, not 60 s');
  assert.equal(monitor.state('stream1'), 'live', '179 s since the confirming check');
  clock.now += 2000;
  assert.equal(monitor.state('stream1'), 'unknown', 'over 180 s without a confirming check: no longer live');
  await monitor.tick(['stream1']);
  assert.equal(monitor.state('stream1'), 'unknown');

  answer = LIVE;
  clock.now += 120 * 1000;
  await monitor.tick(['stream1']);
  assert.equal(monitor.state('stream1'), 'live', 'confirmed again');

  answer = ERR;
  clock.now += 60 * 1000;
  await monitor.tick(['stream1']);
  assert.equal(monitor.state('stream1'), 'off', '5xx = not on');
  clock.now += 60 * 1000;
  assert.equal(monitor.tick(['stream1']), null, '5xx retried after 120 s');
  clock.now += 60 * 1000;
  answer = OFF;
  await monitor.tick(['stream1']);
  assert.equal(monitor.state('stream1'), 'off');
  assert.equal(calls.length, 6);
});

test('monitor: a network error never turns off into live, onChange after every check', async () => {
  let answer = OFF;
  const { clock, monitor } = rig({ stream3: () => answer });
  const changed = [];
  monitor.onChange = (id) => changed.push(id);
  await monitor.tick(['stream3']);
  answer = 'network';
  clock.now += 61 * 1000;
  await monitor.tick(['stream3']);
  assert.equal(monitor.state('stream3'), 'off');
  assert.deepEqual(changed, ['stream3', 'stream3']);
});

test('open before playback: reuses a check younger than 60 s, else reads the playlist; force after an error', async () => {
  let answer = OFF;
  const { clock, calls, monitor } = rig({ stream1: () => answer });
  assert.equal(await monitor.open('stream1', false), 'off', 'not checked yet: reads the playlist');
  assert.equal(calls.length, 1);
  assert.equal(await monitor.open('stream1', false), 'off', 'fresh: no request');
  assert.equal(calls.length, 1);
  answer = LIVE;
  clock.now += 60 * 1000;
  assert.equal(await monitor.open('stream1', false), 'live');
  assert.equal(calls.length, 2);
  assert.equal(monitor.tick(['stream1']), null, 'the open check counts for the 60 s limit');
  answer = OFF; // the broadcast just ended
  assert.equal(await monitor.open('stream1', true), 'off', 'force: a 404 now = not on');
  answer = 'network';
  clock.now += 1000;
  assert.equal(await monitor.open('stream1', true), 'unknown', 'network error: let the player try');
});

test('planner: psp plays the published URL, no cookie or gateway; off = the Chamber message', async () => {
  let answer = LIVE;
  const { monitor } = rig({ stream1: () => answer });
  const planner = new CT.Planner({}, {}, () => 0, () => { throw new Error('gateway must not be asked'); }, { monitor });
  const t = await planner.plan({ kind: 'pspLive', channelId: 'stream1', title: 'Schůze PS' });
  assert.equal(t.url, cases.streams[0].url);
  assert.equal(t.kind, 'live');
  assert.equal(t.psp, true);
  assert.equal(t.dvb, null, 'no gateway client = AVPlay sets no cookie');
  assert.equal(t.startPositionMs, null);
  answer = OFF;
  await assert.rejects(planner.plan({ kind: 'pspLive', channelId: 'stream1', title: 'x' }, true),
    (e) => e.userMessage === 'Poslanecká sněmovna teď nevysílá.');
  await assert.rejects(planner.plan({ kind: 'pspLive', channelId: 'stream9', title: 'x' }));
  const demo = new CT.Planner({}, {}, () => 0, null, { monitor: rig({ stream2: LIVE }).monitor, playUrl: (s) => 'demo:' + s.id });
  assert.equal((await demo.plan({ kind: 'pspLive', channelId: 'stream2', title: 'x' })).url, 'demo:stream2');
});

test('guide entry: one cell over the day, look and selection follow the state; no resume', () => {
  const ch = { id: 'stream1', name: 'Schůze PS' };
  const E = CT.Entries;
  const live = E.psp(ch, 'live', true, 0, 100);
  assert.equal(live.title, 'Přímý přenos');
  assert.equal(E.look(live, 50), 'airing');
  assert.deepEqual(E.select(live, 50, { offer: () => { throw new Error('no resume'); } }),
    { type: 'play', request: { kind: 'pspLive', channelId: 'stream1', title: 'Schůze PS' } });
  assert.equal(E.resumeInfo(live), null);
  const off = E.psp(ch, 'off', true, 0, 100);
  assert.equal(off.title, 'Nevysílá se');
  assert.equal(E.look(off, 50), 'dimmed');
  assert.deepEqual(E.select(off, 50, null), { type: 'notice', text: 'Poslanecká sněmovna teď nevysílá.' });
  const unknown = E.psp(ch, 'unknown', true, 0, 100);
  assert.equal(unknown.title, 'Zjišťuji…');
  assert.equal(E.select(unknown, 50, null).type, 'play', 'not checked yet: tries to play');
  const otherDay = E.psp(ch, 'live', false, 0, 100);
  assert.equal(E.look(otherDay, 50), 'dimmed');
  assert.equal(E.select(otherDay, 50, null).type, 'notice', 'another day: live only, nothing to play');
});
