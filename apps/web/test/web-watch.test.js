'use strict';
// Watch reports on the web (spec/dvb-gateway.md "Watch log" → "Apps and web"; spec/web.md T41, T46, T48): a hidden
// tab plays on and is still watching (no stop); the page going away sends the stop with fetch keepalive; always on
// top (T48) is the same player and the same session; the own window (T46) takes playback over: the tab's session
// stops, the popup starts its own — never two at once. The real app shell, Player and web backend.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const { CT, WEB, APP } = require('./load');

global.window = global;
global.location = { protocol: 'http:', hostname: '192.168.1.20', port: '8098', host: '192.168.1.20:8098',
  origin: 'http://192.168.1.20:8098', search: '' };
global.addEventListener = () => {};
global.MediaSource = { isTypeSupported: () => true };

function fakeElement() {
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
}

function fakeVideo() {
  const attrs = {};
  const v = {
    style: {}, paused: true, currentTime: 0, duration: NaN, muted: false, ownerDocument: null,
    addEventListener: () => {},
    canPlayType: () => '',
    play: () => { v.paused = false; return Promise.resolve(); },
    pause: () => { v.paused = true; },
    load: () => {},
    requestPictureInPicture: () => Promise.resolve({}),
    getAttribute: (k) => (k in attrs ? attrs[k] : null),
    removeAttribute: (k) => { delete attrs[k]; }
  };
  Object.defineProperty(v, 'src', { set: (s) => { attrs.src = s; }, get: () => attrs.src || '' });
  return v;
}

function fakeHls() {
  function Hls() { this.handlers = {}; this.audioTracks = []; }
  Hls.isSupported = () => true;
  Hls.Events = { MANIFEST_PARSED: 'hlsManifestParsed', ERROR: 'hlsError' };
  Hls.prototype.on = function (name, f) { this.handlers[name] = f; };
  Hls.prototype.loadSource = function (url) { this.url = url; };
  Hls.prototype.attachMedia = function (media) { this.media = media; };
  Hls.prototype.destroy = function () {};
  return Hls;
}

const page = {
  hidden: false,
  activeElement: { tagName: 'BODY' },
  body: { classList: { toggle: () => {} } },
  byId: {},
  listeners: {},
  reset(video) {
    this.hidden = false;
    this.byId = { html5player: video };
    this.listeners = {};
  },
  getElementById(id) { return this.byId[id] || (this.byId[id] = fakeElement()); },
  createElement: () => fakeElement(),
  createTextNode: (text) => ({ textContent: text }),
  addEventListener(name, f) { (this.listeners[name] = this.listeners[name] || []).push(f); },
  setHidden(hidden) {
    this.hidden = hidden;
    (this.listeners.visibilitychange || []).forEach((f) => f());
  }
};

global.document = page;
page.reset(fakeVideo());
require(path.join(APP, 'js', 'ui', 'platform.js'));
require(path.join(WEB, 'js', 'platform-web.js'));
['keys', 'zap', 'timeline', 'player', 'app'].forEach((name) => require(path.join(APP, 'js', 'ui', name + '.js')));
CT.Demo = { enabled: false };

let sink = null;
global.fetch = (url, init) => {
  if (!sink) throw new Error('unexpected fetch ' + url);
  sink(url, init);
  return Promise.resolve({});
};

const KEY = 'k'.repeat(43);
const ORIGIN = 'http://192.168.1.20:8098';
const LIVE = { kind: 'live', channelId: 'CH_1', title: 'ČT1' };

/** A fake window for pagehide/pageshow and the platform's fetch keepalive. */
function fakeWin() {
  return {
    listeners: {},
    addEventListener(name, f) { (this.listeners[name] = this.listeners[name] || []).push(f); },
    fire(name, e) { (this.listeners[name] || []).forEach((f) => f(e || {})); }
  };
}

/**
 * One page of the web app: the real shell, Player and Reporter with the web platform's final send; `log` = every
 * report of every page in order, as [page name, body, how it went].
 */
function webApp(name, log, fetchCalls) {
  const video = fakeVideo();
  page.reset(video);
  global.Hls = fakeHls();
  const app = Object.create(CT.App);
  app.planner = { plan: () => Promise.resolve({ kind: 'live', url: ORIGIN + '/dvb/ct/live/CH_1.m3u8', startPositionMs: null }) };
  app.dvbConfig = new CT.DvbConfig(ORIGIN, KEY);
  app.ctState = { onAir: { CH_1: { current: { title: 'Události' } } } };
  app.dvbState = { channels: [], onAir: {} };
  app.profile = { signedIn: () => false, token: null };
  app.onPlayerClosed = () => {};
  app.channelList = () => [];
  app.dvbChannels = [];
  app.resumeStore = { save() {} };
  app.tab = 'live';
  app.tick = () => {};
  app.numberEntry = { cancel() {} };
  const send = (q) => { log.push([name, JSON.parse(q.body), 'xhr', q]); return Promise.resolve({ status: 204 }); };
  const realInterval = global.setInterval;
  global.setInterval = () => 0;
  try {
    app.watch = new CT.Watch.Reporter({
      config: () => app.dvbConfig,
      send,
      sendFinal: CT.Platform.watchFinalSend(send, () => app.dvbConfig),
      profileToken: () => null,
      client: () => 'web/1.0.0 (412)',
      setInterval: () => 1,
      clearInterval: () => {}
    });
    app.player = new CT.Player(app);
  } finally {
    global.setInterval = realInterval;
  }
  app.watchVisibility(page);
  const win = fakeWin();
  app.watchPageHide(win);
  // the page's fetch while this page fires its events (never node's real one: no test reaches the network)
  const fire = win.fire.bind(win);
  win.fire = (event, e) => {
    sink = (url, init) => {
      fetchCalls.push([url, init]);
      log.push([name, JSON.parse(init.body), 'keepalive', { url, headers: init.headers }]);
    };
    try {
      fire(event, e);
    } finally {
      sink = null;
    }
  };
  return { app, win, video };
}

const tick = () => new Promise((r) => setImmediate(r));

test('web: a hidden tab plays on and is still watching — no stop when hidden, none when it comes back', async () => {
  const log = [];
  const { app } = webApp('tab', log, []);
  app.watch.setEnabled(true);
  app.player.open(LIVE);
  await tick();
  page.setHidden(true);
  await tick();
  page.setHidden(false);
  await tick();
  assert.deepEqual(log.map((l) => l[1].event), ['start'], 'one session across hiding');
  app.player.close();
  assert.deepEqual(log.map((l) => l[1].event), ['start', 'stop']);
  assert.equal(log[1][1].session, log[0][1].session);
});

test('web: the page going away sends the stop with fetch keepalive and the same headers, to the gateway', async () => {
  const log = [];
  const fetchCalls = [];
  const { app, win } = webApp('tab', log, fetchCalls);
  app.watch.setEnabled(true);
  app.player.open(LIVE);
  await tick();
  win.fire('pagehide');
  assert.deepEqual(log.map((l) => [l[1].event, l[2]]), [['start', 'xhr'], ['stop', 'keepalive']]);
  assert.equal(fetchCalls.length, 1);
  const [url, init] = fetchCalls[0];
  assert.equal(url, ORIGIN + '/dvb/watch');
  assert.equal(init.keepalive, true);
  assert.equal(init.headers.Authorization, 'Bearer ' + KEY);
  assert.equal(init.headers['X-Cztvhub-Client'], 'web/1.0.0 (412)');
});

test('web without fetch keepalive: the stop goes with the ordinary send (no beacon: it cannot carry the bearer)', async () => {
  const log = [];
  const { app, win } = webApp('tab', log, []);
  const savedFetch = global.fetch;
  delete global.fetch;
  try {
    app.watch.setEnabled(true);
    app.player.open(LIVE);
    await tick();
    win.fire('pagehide');
  } finally {
    global.fetch = savedFetch;
  }
  assert.deepEqual(log.map((l) => [l[1].event, l[2]]), [['start', 'xhr'], ['stop', 'xhr']]);
  assert.equal(log[1][3].headers.Authorization, 'Bearer ' + KEY);
});

test('always on top (T48): the same player, the same session — nothing new while the picture is on top', async () => {
  const log = [];
  const { app, video } = webApp('tab', log, []);
  video.ownerDocument = { pictureInPictureEnabled: true, pictureInPictureElement: null, exitPictureInPicture: () => Promise.resolve() };
  const pip = new CT.WebPip.Controller({ isSecureContext: false }, {
    video: () => video, playerOpen: () => app.player.isOpen(), render: () => {}
  });
  app.watch.setEnabled(true);
  app.player.open(LIVE);
  await tick();
  assert.equal(pip.open(), 'video');
  await tick();
  assert.equal(pip.state, 'video');
  pip.back();
  await tick();
  assert.equal(pip.open(), 'video');
  await tick();
  assert.deepEqual(log.map((l) => l[1].event), ['start']);
  app.player.close();
  pip.playerClosed();
  assert.deepEqual(log.map((l) => l[1].event), ['start', 'stop']);
});

/** BroadcastChannel between the fake windows of the T46 test. */
function bus() {
  const channels = [];
  function Channel(name) { this.name = name; this.onmessage = null; channels.push(this); }
  Channel.prototype.postMessage = function (data) {
    channels.forEach((c) => { if (c !== this && c.name === this.name && c.onmessage) c.onmessage({ data }); });
  };
  return Channel;
}

function windowOf(name, Channel, opener) {
  return {
    name, opener: opener || null, closed: false, location: { href: ORIGIN + '/web/' },
    innerWidth: 1280, innerHeight: 720, screenX: 0, screenY: 0,
    navigator: { userAgent: 'Mozilla/5.0 (Macintosh) Chrome/130', platform: 'MacIntel', maxTouchPoints: 0 },
    matchMedia: () => ({ matches: false }),
    BroadcastChannel: Channel,
    open: null,
    close() { this.closed = true; },
    focus() {},
    addEventListener() {},
    setInterval: () => 1,
    clearInterval: () => {}
  };
}

test('own window (T46): the popup takes over — the tab\'s session stops before the popup\'s starts, never two at once', async () => {
  const log = [];
  const tab = webApp('tab', log, []);
  const popup = webApp('popup', log, []);
  tab.app.watch.setEnabled(true);
  popup.app.watch.setEnabled(true);
  const Channel = bus();
  const tabWin = windowOf('', Channel);
  let popupWin = null;
  tabWin.open = (url, name) => { popupWin = windowOf(name, Channel, tabWin); return popupWin; };
  const own = new CT.WebWindow.Controller(tabWin, {
    stopPlayer: () => { if (tab.app.player.isOpen()) tab.app.player.close(); },
    playing: () => CT.WebPage.playing(tab.app.player),
    render: () => {}
  }).start();
  tab.app.player.open(LIVE);
  await tick();
  assert.equal(own.open(), 'opened');
  assert.deepEqual(log.map((l) => l[0] + ' ' + l[1].event), ['tab start', 'tab stop'], 'the tab stopped at once');
  new CT.WebWindow.Controller(popupWin, { play: (what) => CT.WebPage.playHere(popup.app, what), render: () => {} }).start();
  await tick();
  assert.equal(popup.app.player.isOpen(), true, 'the popup plays what the tab played');
  assert.equal(tab.app.player.isOpen(), false);
  const order = log.map((l) => l[0] + ' ' + l[1].event);
  assert.deepEqual(order, ['tab start', 'tab stop', 'popup start']);
  assert.notEqual(log[2][1].session, log[0][1].session, 'the popup\'s own session');
  assert.equal(log[2][1].channel, 'CH_1');
  // the popup closes (✕ or „Zpět do prohlížeče"): its page goes away, its session stops
  popup.win.fire('pagehide');
  assert.deepEqual(log.map((l) => l[0] + ' ' + l[1].event), ['tab start', 'tab stop', 'popup start', 'popup stop']);
  // at no point two sessions in progress
  let open = 0;
  for (const [, body] of log) {
    open += body.event === 'start' ? 1 : body.event === 'stop' ? -1 : 0;
    assert.ok(open <= 1, 'two sessions at once');
  }
});

test('web: every report goes to the page\'s own gateway, never to ČT or the Chamber', async () => {
  const log = [];
  const { app, win } = webApp('tab', log, []);
  app.watch.setEnabled(true);
  for (const r of [LIVE, { kind: 'pspLive', channelId: 'stream1', title: 'S' }]) {
    app.player.open(r);
    await tick();
    app.player.close();
  }
  app.player.open(LIVE);
  await tick();
  win.fire('pagehide');
  assert.equal(log.length, 6);
  log.forEach((l) => assert.equal(l[3].url, ORIGIN + '/dvb/watch'));
});
