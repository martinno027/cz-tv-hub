'use strict';
// spec/web.md "Background (T41)": a hidden tab or a minimised window keeps playing; nothing is closed, paused or
// re-resolved, neither when the page hides nor when it comes back (the Samsung app releases AVPlay instead:
// apps/tizen/test/background.test.js). The real app shell, Player and web backend around a fake <video> / hls.js.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const { CT, WEB, APP } = require('./load');

global.window = global;
global.location = { protocol: 'http:', hostname: '192.168.1.20', port: '8098', host: '192.168.1.20:8098',
  origin: 'http://192.168.1.20:8098', search: '' };
global.addEventListener = () => {};
global.MediaSource = { isTypeSupported: () => true }; // this browser decodes Anténa's H.265

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

/** The <video>: playing or paused, its source, how often it was (re)loaded or paused. */
function fakeVideo() {
  const attrs = {};
  const v = {
    style: {}, paused: true, currentTime: 0, duration: NaN, muted: false, plays: 0, pauses: 0, loads: 0,
    addEventListener: () => {},
    canPlayType: () => '',
    play: () => { v.paused = false; v.plays++; return Promise.resolve(); },
    pause: () => { v.paused = true; v.pauses++; },
    load: () => { v.loads++; },
    getAttribute: (k) => (k in attrs ? attrs[k] : null),
    removeAttribute: (k) => { delete attrs[k]; }
  };
  Object.defineProperty(v, 'src', { set: (s) => { attrs.src = s; }, get: () => attrs.src || '' });
  return v;
}

/** hls.js: `instances` = every one made; MANIFEST_PARSED is emitted by the test. */
function fakeHls() {
  const instances = [];
  function Hls(config) {
    this.config = config;
    this.handlers = {};
    this.audioTracks = [];
    this.destroyed = false;
    instances.push(this);
  }
  Hls.isSupported = () => true;
  Hls.Events = { MANIFEST_PARSED: 'hlsManifestParsed', ERROR: 'hlsError' };
  Hls.prototype.on = function (name, f) { this.handlers[name] = f; };
  Hls.prototype.loadSource = function (url) { this.url = url; };
  Hls.prototype.attachMedia = function (media) { this.media = media; };
  Hls.prototype.destroy = function () { this.destroyed = true; };
  Hls.instances = instances;
  return Hls;
}

/**
 * The page: elements by id, the <video>, `hidden` and its `visibilitychange`. One document for the whole file
 * (platform-web.js keeps the document it was loaded with); `reset` gives each test a fresh page and <video>.
 */
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

const ORIGIN = 'http://192.168.1.20:8098';

/** CT.App's own methods around a real Player on the web backend (CT.Platform.playerBackend); `plans` = planner calls. */
function webApp(target) {
  const video = fakeVideo();
  page.reset(video);
  global.Hls = fakeHls();
  const plans = [];
  const app = Object.create(CT.App);
  app.planner = { plan: (r, force) => { plans.push(force); return Promise.resolve(Object.assign({}, target)); } };
  app.onPlayerClosed = () => {};
  app.channelList = () => [];
  app.dvbChannels = [];
  app.resumeStore = { save() {} };
  app.tab = 'live';
  app.ticks = 0;
  app.tick = () => { app.ticks++; };
  const realInterval = global.setInterval;
  global.setInterval = () => 0;
  try {
    app.player = new CT.Player(app);
  } finally {
    global.setInterval = realInterval;
  }
  app.watchVisibility(page);
  return { app, plans, video, Hls: global.Hls };
}

const tick = () => new Promise((r) => setImmediate(r));

async function playing(target, request) {
  const w = webApp(target);
  w.app.player.open(request);
  await tick();
  assert.equal(w.Hls.instances.length, 1);
  w.Hls.instances[0].handlers[w.Hls.Events.MANIFEST_PARSED]();
  assert.equal(w.video.paused, false);
  return w;
}

test('web: the platform plays on in the background (Samsung does not)', () => {
  assert.equal(CT.Platform.name, 'web');
  assert.equal(CT.Platform.backgroundPlayback, true);
});

for (const [name, target, request] of [
  ['Anténa live', { kind: 'live', url: ORIGIN + '/dvb/live/ch1.m3u8', startPositionMs: null, dvb: {} },
    { kind: 'dvbLive', channelId: 'ch1', title: 'ČT1 HD' }],
  ['ČT live', { kind: 'live', url: 'https://cdn.example/ct1.m3u8', startPositionMs: null },
    { kind: 'live', channelId: 'CH_1', title: 'ČT1' }],
  ['archive', { kind: 'vod', url: 'https://cdn.example/a.m3u8', startPositionMs: null },
    { kind: 'archive', channelId: 'CH_1', title: 'Pořad' }]
]) {
  test('web, ' + name + ': hidden and back keeps the same stream playing, nothing re-resolved', async () => {
    const { app, plans, video, Hls } = await playing(target, request);
    const hls = Hls.instances[0];
    video.currentTime = 1234;
    page.setHidden(true);
    await tick();
    assert.equal(video.paused, false, 'still playing while hidden');
    assert.equal(hls.destroyed, false);
    assert.equal(video.currentTime, 1234);
    page.setHidden(false);
    await tick();
    assert.equal(video.paused, false);
    assert.equal(video.pauses, 0);
    assert.equal(video.plays, 1, 'no second start');
    assert.equal(hls.destroyed, false, 'the same hls.js session');
    assert.equal(Hls.instances.length, 1, 'no new session');
    assert.deepEqual(plans, [false], 'nothing re-resolved on return');
    assert.equal(app.player.isOpen(), true);
    assert.equal(app.ticks, 1, 'the clock catches up on return');
    app.player.close();
  });
}

test('web: the viewer\'s own pause stays a pause across hiding (the tab does not start it again)', async () => {
  const { app, video } = await playing({ kind: 'vod', url: 'https://cdn.example/a.m3u8', startPositionMs: null },
    { kind: 'archive', channelId: 'CH_1', title: 'Pořad' });
  app.player.backend.togglePause();
  assert.equal(video.paused, true);
  page.setHidden(true);
  page.setHidden(false);
  await tick();
  assert.equal(video.paused, true);
  assert.equal(video.plays, 1);
  app.player.close();
});

test('web: the guide still reopens at now on return when no player is open', () => {
  const { app, plans } = webApp({ kind: 'live', url: 'x', startPositionMs: null });
  let shown = 0;
  app.tab = 'guide';
  app.now = 0;
  app.screens = { guide: { show: () => { shown++; } } };
  page.setHidden(true);
  page.setHidden(false);
  assert.equal(shown, 1);
  assert.ok(app.now > 0);
  assert.deepEqual(plans, []);
});
