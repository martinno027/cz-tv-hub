'use strict';
// Watch reports (spec/apps.md "Watch reports (T53)", spec/dvb-gateway.md "Watch log" → "Reports", "Apps and web"):
// start / beat every 60 s / stop to POST /dvb/watch on the gateway only, only while /dvb/status says watchLog true,
// fire-and-forget; what each source and mode reports.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

const W = CT.Watch;
const KEY = 'k'.repeat(43);
const BASE = 'http://192.168.1.20:8098';
const SESSION_RE = /^[A-Za-z0-9_-]{16,64}$/;

/** A fake clock for the beat: `run(ms)` fires the intervals due within `ms`. */
function fakeClock() {
  const timers = new Map();
  let id = 0;
  let now = 0;
  return {
    setInterval(fn, ms) { timers.set(++id, { fn, ms, next: now + ms }); return id; },
    clearInterval(i) { timers.delete(i); },
    run(ms) {
      const end = now + ms;
      for (;;) {
        let due = null;
        for (const [i, t] of timers) if (t.next <= end && (!due || t.next < due[1].next)) due = [i, t];
        if (!due) break;
        now = due[1].next;
        due[1].next += due[1].ms;
        due[1].fn();
      }
      now = end;
    },
    get active() { return timers.size; }
  };
}

/** A reporter with a recording transport; `opts` overrides (e.g. send that fails). */
function reporter(opts) {
  const sent = [];
  const finals = [];
  const clock = fakeClock();
  const state = { config: new CT.DvbConfig(BASE, KEY), token: null };
  const r = new W.Reporter(Object.assign({
    config: () => state.config,
    send: (req) => { sent.push(req); return Promise.resolve({ status: 204, body: '' }); },
    sendFinal: (req) => { finals.push(req); },
    profileToken: () => state.token,
    client: () => 'tizen/1.0.0 (412)',
    setInterval: clock.setInterval,
    clearInterval: clock.clearInterval
  }, opts || {}));
  const bodies = () => sent.map((q) => JSON.parse(q.body));
  return { r, sent, finals, clock, state, bodies };
}

const CT1_LIVE = { source: 'ct', mode: 'live', channel: 'CH_1', title: 'Události' };

// -- describe: what each source and mode reports ------------------------------------------------------------------

const NOW = Date.parse('2026-11-02T18:00:00Z');
const ctx = {
  now: NOW,
  ctOnAir: { CH_1: { current: { title: 'Události' } } },
  dvbOnAir: { nova1: { current: { title: 'Televizní noviny' } } }
};

test('describe: ČT live = the programme airing now from the guide the app shows, not the channel name', () => {
  assert.deepEqual(W.describe({ kind: 'live', channelId: 'CH_1', title: 'ČT1' }, { kind: 'live' }, ctx),
    { source: 'ct', mode: 'live', channel: 'CH_1', title: 'Události' });
  // nothing in the guide: no title (the gateway takes its own schedule)
  assert.deepEqual(W.describe({ kind: 'live', channelId: 'CH_2', title: 'ČT2' }, { kind: 'live' }, ctx),
    { source: 'ct', mode: 'live', channel: 'CH_2' });
});

test('describe: ČT startover = anything from the 3 h buffer (airing, ended, Od začátku); archive = VOD with idec; no "timeshift"', () => {
  const airing = { kind: 'programme', channelId: 'CH_24', title: 'Studio 6', idec: '1', start: NOW - 600000, end: NOW + 600000 };
  const ended = { kind: 'programme', channelId: 'CH_24', title: 'Studio 6', idec: '1', start: NOW - 7200000, end: NOW - 3600000 };
  const fromStart = { kind: 'startover', channelId: 'CH_1', title: 'Události', start: NOW - 600000 };
  assert.deepEqual(W.describe(fromStart, { kind: 'timeshift' }, ctx),
    { source: 'ct', mode: 'startover', channel: 'CH_1', title: 'Události' });
  assert.deepEqual(W.describe(airing, { kind: 'timeshift' }, ctx),
    { source: 'ct', mode: 'startover', channel: 'CH_24', title: 'Studio 6' });
  assert.deepEqual(W.describe(ended, { kind: 'timeshift' }, ctx),
    { source: 'ct', mode: 'startover', channel: 'CH_24', title: 'Studio 6' }, 'ended, still in the buffer');
  assert.deepEqual(W.describe(Object.assign({}, ended, { resumeSeconds: 600 }), { kind: 'timeshift' }, ctx),
    { source: 'ct', mode: 'startover', channel: 'CH_24', title: 'Studio 6' }, 'resume within the buffer');
  assert.deepEqual(W.describe(Object.assign({}, ended, { idec: '222411000100101' }), { kind: 'vod' }, ctx),
    { source: 'ct', mode: 'archive', channel: 'CH_24', title: 'Studio 6', idec: '222411000100101' });
  // Archiv without a known channel: no channel (the gateway stores `unknown`), idec and title from the card
  assert.deepEqual(W.describe({ kind: 'archive', idec: '42', title: 'Kosmo', channelName: 'ČT1' }, { kind: 'vod' }, ctx),
    { source: 'ct', mode: 'archive', title: 'Kosmo', idec: '42' });
  // airing now without a start: CT's live stream
  assert.deepEqual(W.describe({ kind: 'archive', idec: '42', title: 'Kosmo' }, { kind: 'live' }, ctx),
    { source: 'ct', mode: 'live', title: 'Kosmo' });
});

test('describe: the Chamber (psp, the stream id, no programme) and the antenna (live, recording, timeline)', () => {
  assert.deepEqual(W.describe({ kind: 'pspLive', channelId: 'stream1', title: 'Sněmovna 1' }, { kind: 'live', psp: true }, ctx),
    { source: 'psp', mode: 'live', channel: 'stream1' });
  assert.deepEqual(W.describe({ kind: 'dvbLive', channelId: 'nova1', title: 'Nova' }, { kind: 'live' }, ctx),
    { source: 'dvb', mode: 'live', channel: 'nova1', title: 'Televizní noviny' });
  assert.deepEqual(W.describe({ kind: 'dvbRecorded', channelId: 'nova1', title: 'Film', recordingId: 'dvr7' }, { kind: 'vod' }, ctx),
    { source: 'dvb', mode: 'recording', channel: 'nova1', title: 'Film', recording: 'dvr7' });
  // Archiv/Pokračovat: the request has no channel id; a timeline window names its channel
  const tl = { kind: 'dvbRecorded', channelId: '', title: 'Zprávy', recordingId: 'bnova1-1-2' };
  assert.deepEqual(W.describe(tl, { kind: 'timeline', timeline: { channel: 'nova1' } }, ctx),
    { source: 'dvb', mode: 'timeline', channel: 'nova1', title: 'Zprávy', recording: 'bnova1-1-2' });
  // a neighbouring window (▲/▼ or seeking out of the window): its own id and programme, not the opened one's
  const next = { id: 'bnova1-2-3', channel: 'nova1', programme: { start: 2, end: 3 },
    programmes: [{ title: 'Zprávy', start: 1, end: 2 }, { title: 'Počasí', start: 2, end: 3 }] };
  assert.deepEqual(W.describe(tl, { kind: 'timeline', timeline: next }, ctx),
    { source: 'dvb', mode: 'timeline', channel: 'nova1', title: 'Počasí', recording: 'bnova1-2-3' });
  assert.deepEqual(W.describe(Object.assign({}, tl, { recordingId: 'dvr7' }), { kind: 'vod' }, ctx),
    { source: 'dvb', mode: 'recording', title: 'Zprávy', recording: 'dvr7' });
});

test('describe: the title is cut to 200 characters; unknown requests and missing targets report nothing', () => {
  const long = 'Ř'.repeat(260);
  const info = W.describe({ kind: 'programme', channelId: 'CH_1', title: long, idec: '1', start: 0, end: 1 }, { kind: 'vod' }, ctx);
  assert.equal(info.title.length, 200);
  assert.ok(Buffer.byteLength(JSON.stringify(Object.assign({ event: 'start', session: 'x'.repeat(64) }, info))) <= 2048);
  assert.equal(W.describe({ kind: 'something' }, { kind: 'live' }, ctx), null);
  assert.equal(W.describe({ kind: 'live', channelId: 'CH_1' }, null, ctx), null);
  assert.equal(W.describe(null, { kind: 'live' }, ctx), null);
});

// -- session ids --------------------------------------------------------------------------------------------------

test('session ids: 32 of [A-Za-z0-9_-], new every time (crypto, or given bytes)', () => {
  const ids = new Set();
  for (let i = 0; i < 200; i++) {
    const id = W.sessionId();
    assert.match(id, SESSION_RE);
    ids.add(id);
  }
  assert.equal(ids.size, 200);
  assert.equal(W.sessionId((n) => new Uint8Array(n)), 'A'.repeat(32));
  assert.equal(W.sessionId((n) => new Uint8Array(n).fill(255)), '_'.repeat(32));
});

// -- the reporter -------------------------------------------------------------------------------------------------

test('off (no status said watchLog true): nothing is sent, also no beats', () => {
  const { r, sent, clock } = reporter();
  r.play(CT1_LIVE);
  clock.run(10 * 60 * 1000);
  r.stop();
  assert.equal(sent.length, 0);
  assert.equal(clock.active, 0);
});

test('on: start with every field, beat every 60 s (paused too: the player is open), stop; one session id', () => {
  const { r, sent, clock, bodies } = reporter();
  r.setEnabled(true);
  r.play(CT1_LIVE);
  assert.equal(sent.length, 1);
  clock.run(59 * 1000);
  assert.equal(sent.length, 1, 'no beat before 60 s');
  clock.run(1000);
  assert.equal(sent.length, 2);
  clock.run(2 * 60 * 1000);
  r.stop();
  clock.run(5 * 60 * 1000);
  const b = bodies();
  assert.deepEqual(b.map((x) => x.event), ['start', 'beat', 'beat', 'beat', 'stop']);
  const id = b[0].session;
  assert.match(id, SESSION_RE);
  b.forEach((x) => assert.equal(x.session, id));
  assert.deepEqual(b[0], { event: 'start', session: id, source: 'ct', mode: 'live', channel: 'CH_1', title: 'Události' });
  b.slice(1).forEach((x) => assert.deepEqual(Object.keys(x).sort(), ['event', 'session']), 'beat/stop: only event + session');
  assert.equal(clock.active, 0, 'no timer left after stop');
});

test('the request: POST /dvb/watch on the gateway, bearer key, client header, JSON; the profile token when signed in', () => {
  const { r, sent, state } = reporter();
  r.setEnabled(true);
  r.play(CT1_LIVE);
  let q = sent[0];
  assert.equal(q.method, 'POST');
  assert.equal(q.url, BASE + '/dvb/watch');
  assert.equal(q.headers.Authorization, 'Bearer ' + KEY);
  assert.equal(q.headers['X-Cztvhub-Client'], 'tizen/1.0.0 (412)');
  assert.equal(q.headers['Content-Type'], 'application/json');
  assert.equal(q.headers['X-Cztvhub-Profile'], undefined, 'signed out: no profile header');
  state.token = 'tok-123';
  r.play(CT1_LIVE);
  q = sent[sent.length - 1];
  assert.equal(JSON.parse(q.body).event, 'start');
  assert.equal(q.headers['X-Cztvhub-Profile'], 'tok-123');
});

test('a new start stops the old session first; every start gets a new session id', () => {
  const { r, bodies } = reporter();
  r.setEnabled(true);
  r.play(CT1_LIVE);
  r.play({ source: 'dvb', mode: 'live', channel: 'nova1' });
  r.play({ source: 'psp', mode: 'live', channel: 'stream1' });
  const b = bodies();
  assert.deepEqual(b.map((x) => x.event), ['start', 'stop', 'start', 'stop', 'start']);
  assert.equal(b[1].session, b[0].session);
  assert.equal(b[3].session, b[2].session);
  assert.equal(new Set([b[0].session, b[2].session, b[4].session]).size, 3);
  assert.equal(b[4].source, 'psp');
});

test('switched off while playing: nothing more is sent (no stop, no beats); switched on again: a new start', () => {
  const { r, sent, clock, bodies } = reporter();
  r.setEnabled(true);
  r.play(CT1_LIVE);
  r.setEnabled(false);
  clock.run(5 * 60 * 1000);
  assert.equal(sent.length, 1);
  r.setEnabled(true); // the next status check: still playing → a session of its own
  r.setEnabled(true); // the same answer again changes nothing
  const b = bodies();
  assert.deepEqual(b.map((x) => x.event), ['start', 'start']);
  assert.notEqual(b[0].session, b[1].session);
  r.stop();
  r.setEnabled(false);
  r.setEnabled(true); // nothing plays: nothing starts
  assert.deepEqual(bodies().map((x) => x.event), ['start', 'start', 'stop']);
});

test('switched on while nothing plays starts nothing; only `true` is on', () => {
  for (const v of [true, 'true', 1, null, undefined]) {
    const { r, sent } = reporter();
    r.setEnabled(v);
    r.play(CT1_LIVE);
    assert.equal(sent.length, v === true ? 1 : 0, String(v));
  }
});

test('fire-and-forget: a failing or throwing transport never escapes, beats go on', async () => {
  for (const send of [() => Promise.reject(new Error('timeout')), () => { throw new Error('no network'); }, () => undefined]) {
    let calls = 0;
    const { r, clock } = reporter({ send: (q) => { calls++; return send(q); } });
    r.setEnabled(true);
    assert.doesNotThrow(() => r.play(CT1_LIVE));
    clock.run(2 * 60 * 1000);
    assert.doesNotThrow(() => r.stop());
    assert.equal(calls, 4);
  }
  await new Promise((done) => setImmediate(done)); // an unhandled rejection would fail the run here
});

test('play() returns nothing to wait for', () => {
  const { r } = reporter();
  r.setEnabled(true);
  assert.equal(r.play(CT1_LIVE), undefined);
  assert.equal(r.stop(), undefined);
});

test('without a gateway nothing is sent (and nothing breaks)', () => {
  const { r, sent, state, clock } = reporter();
  state.config = null;
  r.setEnabled(true);
  r.play(CT1_LIVE);
  clock.run(3 * 60 * 1000);
  r.stop();
  assert.equal(sent.length, 0);
});

test('the page goes away: the stop goes through the final send; nothing more after it', () => {
  const { r, sent, finals, clock } = reporter();
  r.setEnabled(true);
  r.play(CT1_LIVE);
  r.stop(true);
  clock.run(5 * 60 * 1000);
  assert.equal(sent.length, 1);
  assert.equal(finals.length, 1);
  assert.equal(JSON.parse(finals[0].body).event, 'stop');
  assert.equal(finals[0].url, BASE + '/dvb/watch');
  r.stop(true); // a second pagehide: nothing to stop
  assert.equal(finals.length, 1);
});

// -- the closing page's send (the web) ----------------------------------------------------------------------------

test('finalSend: fetch keepalive with the same headers (no cookie, no beacon: the gateway wants the bearer); else the send', () => {
  const req = { method: 'POST', url: BASE + '/dvb/watch', headers: { Authorization: 'Bearer ' + KEY, 'X-Cztvhub-Client': 'web' },
    body: '{"event":"stop","session":"' + 'a'.repeat(32) + '"}' };
  const seen = [];
  W.finalSend({ fetch: (url, init) => { seen.push(['fetch', url, init]); return Promise.resolve(); }, send: () => seen.push(['send']) })(req);
  assert.equal(seen.length, 1);
  assert.equal(seen[0][0], 'fetch');
  assert.equal(seen[0][1], req.url);
  assert.equal(seen[0][2].keepalive, true);
  assert.equal(seen[0][2].method, 'POST');
  assert.equal(seen[0][2].credentials, 'omit');
  assert.deepEqual(seen[0][2].headers, req.headers);
  assert.equal(seen[0][2].body, req.body);
  const sends = [];
  W.finalSend({ fetch: null, send: (q) => sends.push(q) })(req);
  assert.deepEqual(sends, [req]);
});

// -- /dvb/status ----------------------------------------------------------------------------------------------------

test('parseStatus: watchLog true only when the gateway says so; missing = false', () => {
  const P = CT.DvbParser;
  assert.equal(P.parseStatus(200, '{"enabled": true, "watchLog": true}').watchLog, true);
  assert.equal(P.parseStatus(200, '{"enabled": true, "watchLog": false}').watchLog, false);
  assert.equal(P.parseStatus(200, '{"enabled": true}').watchLog, false);
  assert.equal(P.parseStatus(200, '{"enabled": true, "watchLog": "yes"}').watchLog, false);
  const { response } = require('./load');
  const fixture = response('dvb/dvb_status.json');
  assert.equal(P.parseStatus(fixture.status, fixture.body).watchLog, false, 'the recorded status has no watchLog');
});
