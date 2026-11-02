'use strict';
// Watch reports wired into the shared player (spec/dvb-gateway.md "Watch log" → "Apps and web"): start when a channel
// or programme plays, stop when the player closes, the channel changes or the Samsung app goes to the background;
// re-resolving the same stream keeps its session; the status check switches the log; every report goes to the
// gateway, never to ČT or the Chamber. The real app shell (CT.App's methods) and Player on a fake AVPlay.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

global.window = global;

function fakeDocument() {
  const byId = {};
  const listeners = {};
  const make = () => {
    const classes = new Set();
    const el = {
      style: {}, textContent: '', children: [], attrs: {}, className: '',
      classList: {
        add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c),
        toggle: (c, on) => { if (on === undefined ? !classes.has(c) : on) classes.add(c); else classes.delete(c); }
      },
      appendChild: (c) => { el.children.push(c); },
      setAttribute: (k, v) => { el.attrs[k] = v; },
      getAttribute: (k) => (k in el.attrs ? el.attrs[k] : null)
    };
    Object.defineProperty(el, 'innerHTML', { set: () => { el.children = []; }, get: () => '' });
    return el;
  };
  return {
    hidden: false,
    getElementById: (id) => byId[id] || (byId[id] = make()),
    createElement: () => make(),
    createTextNode: (text) => ({ textContent: text }),
    addEventListener: (name, f) => { (listeners[name] = listeners[name] || []).push(f); },
    setHidden(hidden) {
      this.hidden = hidden;
      (listeners.visibilitychange || []).forEach((f) => f());
    }
  };
}

global.document = fakeDocument();
global.addEventListener = () => {};
require('../app/js/ui/platform.js');
require('../app/js/ui/keys.js');
require('../app/js/ui/zap.js');
require('../app/js/ui/timeline.js');
require('../app/js/ui/player.js');
require('../app/js/ui/app.js');
CT.Demo = { enabled: false };

const KEY = 'k'.repeat(43);
const GATEWAY = 'http://192.168.1.20:8098';
const DVB = { config: { cookie: () => 'cztvhub_key=' + 'k'.repeat(43) } };
const CT_HOSTS = /ceskatelevize\.cz|ctcdn|cdn\.example|pspcr|cra\.cz|themoviedb/;

function fakeAvPlay() {
  const p = {
    opened: [], state: 'NONE', listener: null,
    open(url) { p.opened.push(url); p.state = 'IDLE'; },
    setDisplayRect() {}, setDisplayMethod() {}, setStreamingProperty() {}, setBufferingParam() {},
    setListener(l) { p.listener = l; },
    prepareAsync(ok) { p.state = 'READY'; ok(); },
    play() { p.state = 'PLAYING'; }, pause() { p.state = 'PAUSED'; }, stop() { p.state = 'IDLE'; },
    close() { p.state = 'NONE'; },
    getState: () => p.state, getCurrentTime: () => 0, getDuration: () => 0, seekTo() {}
  };
  return p;
}

/** Targets the fake planner hands out, by request kind (CT/Chamber streams on their CDNs, Anténa on the gateway). */
function targetFor(request, n) {
  switch (request.kind) {
    case 'live': return { kind: 'live', url: 'https://cdn.example/ct-' + n + '.m3u8', startPositionMs: null };
    case 'pspLive': return { kind: 'live', url: 'https://pspcr-ott-live.ssl.cdn.cra.cz/x/' + n + '.m3u8', startPositionMs: null, psp: true };
    case 'dvbLive': return { kind: 'live', url: GATEWAY + '/dvb/live/' + request.channelId + '.m3u8', startPositionMs: null, dvb: DVB };
    case 'dvbRecorded': return { kind: 'vod', url: GATEWAY + '/dvb/recording/r.m3u8', startPositionMs: null, dvb: DVB };
    default: return { kind: 'vod', url: 'https://cdn.example/vod-' + n + '.m3u8', startPositionMs: null };
  }
}

/** The app shell around a real Player and a real Reporter; `sent` = every report (parsed), `requests` = raw. */
function samsungApp(opts) {
  const o = opts || {};
  global.document = fakeDocument();
  global.webapis = { avplay: fakeAvPlay() };
  const requests = [];
  const plans = [];
  const app = Object.create(CT.App);
  let n = 0;
  app.planner = {
    plan: (r, force) => {
      plans.push(force);
      n++;
      return o.fail ? Promise.reject(new CT.CtError('nope', 'Nejde to')) : Promise.resolve(targetFor(r, n));
    }
  };
  app.dvbConfig = new CT.DvbConfig(GATEWAY, KEY);
  app.ctState = { onAir: { CH_1: { current: { title: 'Události' } }, CH_2: { current: { title: 'Kosmo' } } } };
  app.dvbState = { channels: [], onAir: {} };
  app.profile = { signedIn: () => false, token: null };
  app.onPlayerClosed = () => {};
  app.dvbChannels = [];
  app.resumeStore = { save() {} };
  app.tab = 'live';
  app.tick = () => {};
  app.zapItems = [
    { key: 'ct:CH_1', source: 'ct', channel: { id: 'CH_1', name: 'ČT1' } },
    { key: 'ct:CH_2', source: 'ct', channel: { id: 'CH_2', name: 'ČT2' } },
    { key: 'psp:stream1', source: 'psp', channel: { id: 'stream1', name: 'Sněmovna' } }
  ];
  app.channelList = () => app.zapItems;
  const realInterval = global.setInterval;
  global.setInterval = () => 0;
  try {
    app.watch = new CT.Watch.Reporter({
      config: () => app.dvbConfig,
      send: (q) => { requests.push(q); return Promise.resolve({ status: 204, body: '' }); },
      profileToken: () => (app.profile.signedIn() ? app.profile.token : null),
      client: () => 'tizen/1.0.0 (412)',
      setInterval: () => 1,
      clearInterval: () => {}
    });
    app.player = new CT.Player(app);
  } finally {
    global.setInterval = realInterval;
  }
  app.watchVisibility(global.document);
  const win = { listeners: {}, addEventListener(name, f) { (this.listeners[name] = this.listeners[name] || []).push(f); },
    fire(name, e) { (this.listeners[name] || []).forEach((f) => f(e || {})); } };
  app.watchPageHide(win);
  const sent = () => requests.map((q) => JSON.parse(q.body));
  return { app, requests, sent, plans, win, avplay: global.webapis.avplay };
}

const tick = () => new Promise((r) => setImmediate(r));
const events = (list) => list.map((b) => b.event);

test('ČT live: start once it plays (title from the guide), stop when the player closes', async () => {
  const { app, sent, requests } = samsungApp();
  app.watch.setEnabled(true);
  app.player.open({ kind: 'live', channelId: 'CH_1', title: 'ČT1' });
  assert.equal(requests.length, 0, 'nothing before the stream is resolved');
  await tick();
  app.player.close();
  const b = sent();
  assert.deepEqual(events(b), ['start', 'stop']);
  assert.deepEqual(b[0], { event: 'start', session: b[0].session, source: 'ct', mode: 'live', channel: 'CH_1', title: 'Události' });
  assert.equal(b[1].session, b[0].session);
});

test('every source and mode: one start each, with what it is', async () => {
  const cases = [
    [{ kind: 'pspLive', channelId: 'stream1', title: 'Sněmovna' }, { source: 'psp', mode: 'live', channel: 'stream1' }],
    [{ kind: 'dvbLive', channelId: 'nova1', title: 'Nova' }, { source: 'dvb', mode: 'live', channel: 'nova1' }],
    [{ kind: 'dvbRecorded', channelId: 'nova1', title: 'Film', recordingId: 'dvr7' },
      { source: 'dvb', mode: 'recording', channel: 'nova1', title: 'Film', recording: 'dvr7' }],
    [{ kind: 'archive', idec: '42', title: 'Kosmo' }, { source: 'ct', mode: 'archive', title: 'Kosmo', idec: '42' }]
  ];
  for (const [request, want] of cases) {
    const { app, sent } = samsungApp();
    app.watch.setEnabled(true);
    app.player.open(request);
    await tick();
    app.player.close();
    const b = sent();
    assert.deepEqual(events(b), ['start', 'stop'], request.kind);
    const start = Object.assign({}, b[0]);
    delete start.event;
    delete start.session;
    assert.deepEqual(start, want, request.kind);
  }
});

test('never to ČT or the Chamber: every report goes to the gateway, the streams themselves to their CDNs', async () => {
  const { app, requests, avplay } = samsungApp();
  app.watch.setEnabled(true);
  for (const r of [{ kind: 'live', channelId: 'CH_1', title: 'ČT1' }, { kind: 'pspLive', channelId: 'stream1', title: 'S' },
    { kind: 'archive', idec: '42', title: 'Kosmo' }]) {
    app.player.open(r);
    await tick();
    app.player.close();
  }
  assert.equal(requests.length, 6);
  requests.forEach((q) => {
    assert.equal(q.url, GATEWAY + '/dvb/watch');
    assert.ok(!CT_HOSTS.test(q.url), q.url);
  });
  assert.ok(avplay.opened.every((u) => CT_HOSTS.test(u)), 'the test streams are on the CDNs');
});

test('zapping: the old channel stops at once, the new one starts when it plays (a new session)', async () => {
  const { app, sent } = samsungApp();
  app.watch.setEnabled(true);
  app.player.open({ kind: 'live', channelId: 'CH_1', title: 'ČT1' });
  await tick();
  app.player.switchTo(1, false);
  assert.deepEqual(events(sent()), ['start', 'stop']);
  await tick();
  const b = sent();
  assert.deepEqual(events(b), ['start', 'stop', 'start']);
  assert.equal(b[2].channel, 'CH_2');
  assert.equal(b[2].title, 'Kosmo');
  assert.notEqual(b[2].session, b[0].session);
  app.player.close();
  assert.deepEqual(events(sent()), ['start', 'stop', 'start', 'stop']);
});

test('Samsung in the background: stop when hidden, a new session back in front', async (t) => {
  t.after(() => { delete global.webapis; });
  const { app, sent } = samsungApp();
  app.watch.setEnabled(true);
  app.player.open({ kind: 'live', channelId: 'CH_1', title: 'ČT1' });
  await tick();
  global.document.setHidden(true);
  assert.deepEqual(events(sent()), ['start', 'stop']);
  global.document.setHidden(false);
  await tick();
  const b = sent();
  assert.deepEqual(events(b), ['start', 'stop', 'start']);
  assert.notEqual(b[2].session, b[0].session);
  app.player.close();
});

test('a playback error re-resolved once keeps the session; failing again ends it; a failed plan starts nothing', async () => {
  const { app, sent, plans } = samsungApp();
  app.watch.setEnabled(true);
  app.player.open({ kind: 'live', channelId: 'CH_1', title: 'ČT1' });
  await tick();
  app.player.onError('expired');
  await tick();
  assert.deepEqual(plans, [false, true], 're-resolved');
  assert.deepEqual(events(sent()), ['start'], 'the same session');
  app.player.onError('again');
  assert.deepEqual(events(sent()), ['start', 'stop'], 'the error stays on screen: nothing plays');
  app.player.close();
  assert.deepEqual(events(sent()), ['start', 'stop']);

  const failing = samsungApp({ fail: true });
  failing.app.watch.setEnabled(true);
  failing.app.player.open({ kind: 'live', channelId: 'CH_1', title: 'ČT1' });
  await tick();
  failing.app.player.close();
  assert.deepEqual(failing.sent(), []);
});

test('the page goes away (pagehide): the stop goes out as the last send; back from the cache: a new start', async () => {
  const { app, sent, win } = samsungApp();
  app.watch.setEnabled(true);
  app.player.open({ kind: 'live', channelId: 'CH_1', title: 'ČT1' });
  await tick();
  win.fire('pagehide');
  assert.deepEqual(events(sent()), ['start', 'stop']);
  win.fire('pageshow', { persisted: false });
  assert.deepEqual(events(sent()), ['start', 'stop']);
  win.fire('pageshow', { persisted: true });
  assert.deepEqual(events(sent()), ['start', 'stop', 'start']);
  app.player.close();
});

test('the status check switches the log: watchLog true reports, false stops, no answer keeps the last answer', async () => {
  const { app, sent } = samsungApp();
  let answer = Promise.resolve({ watchLog: true, thumbnails: false });
  app.dvbRepo = { client: { status: () => answer } };
  app.dvbThumbs = false;
  app.player.open({ kind: 'live', channelId: 'CH_1', title: 'ČT1' });
  await tick();
  assert.deepEqual(sent(), [], 'off until a status check says so');
  app.checkThumbs();
  await tick();
  assert.deepEqual(events(sent()), ['start'], 'switched on while playing: the playing channel starts');
  answer = Promise.reject(new Error('unreachable'));
  app.checkThumbs();
  await tick();
  app.player.close();
  assert.deepEqual(events(sent()), ['start', 'stop'], 'a failed check keeps the log on: the session stops normally');
  answer = Promise.resolve({ watchLog: false, thumbnails: false });
  app.checkThumbs();
  await tick();
  app.player.open({ kind: 'live', channelId: 'CH_1', title: 'ČT1' });
  await tick();
  app.player.close();
  assert.deepEqual(events(sent()), ['start', 'stop'], 'off: nothing more is sent');
});

test('a neighbouring timeline window is a new session (stop, then start with the window\'s own programme)', async () => {
  const { app, sent } = samsungApp();
  app.watch.setEnabled(true);
  const tl = (id, start, title) => ({ id, channel: 'nova1', playlist: '/dvb/timeline/' + id + '.m3u8',
    programme: { start, end: start + 1 }, pieces: [{ position: 0, time: start, duration: 10 }],
    programmes: [{ id, title, subtitle: '', start, end: start + 1 }] });
  const target = (id, start, title) => ({ kind: 'timeline', url: GATEWAY + '/dvb/timeline/' + id + '.m3u8',
    startPositionMs: 0, alwaysSeek: true, dvb: DVB, timeline: tl(id, start, title) });
  app.planner.plan = () => Promise.resolve(target('bnova1-1-2', 1000, 'Zprávy'));
  app.player.open({ kind: 'dvbRecorded', channelId: '', title: 'Zprávy', recordingId: 'bnova1-1-2' });
  await tick();
  app.player.switchTarget(target('bnova1-2-3', 2000, 'Počasí'));
  app.player.close();
  const b = sent();
  assert.deepEqual(events(b), ['start', 'stop', 'start', 'stop']);
  assert.equal(b[0].recording, 'bnova1-1-2');
  assert.equal(b[0].title, 'Zprávy');
  assert.equal(b[2].recording, 'bnova1-2-3');
  assert.equal(b[2].title, 'Počasí');
  assert.equal(b[2].channel, 'nova1');
  assert.notEqual(b[2].session, b[0].session);
});

test('signed in: the profile session token rides along', async () => {
  const { app, requests } = samsungApp();
  app.profile = { signedIn: () => true, token: 'tok-1' };
  app.watch.setEnabled(true);
  app.player.open({ kind: 'live', channelId: 'CH_1', title: 'ČT1' });
  await tick();
  app.player.close();
  requests.forEach((q) => {
    assert.equal(q.headers['X-Cztvhub-Profile'], 'tok-1');
    assert.equal(q.headers.Authorization, 'Bearer ' + KEY);
    assert.equal(q.headers['X-Cztvhub-Client'], 'tizen/1.0.0 (412)');
  });
});
