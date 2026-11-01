'use strict';
// The app in the background (app.js `watchVisibility`): the Samsung app releases AVPlay when the page is hidden and
// resolves the stream afresh when it comes back (spec/web.md "Background (T41)": the web plays on, apps/web tests).
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

global.window = global; // the ui scripts are browser scripts; a minimal document below stands in for the page

/** Elements by id with what the player touches, plus `hidden` and `visibilitychange` of the page. */
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
    addEventListener: (name, f) => { (listeners[name] = listeners[name] || []).push(f); },
    /** The browser hides or shows the page. */
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

/** webapis.avplay that "plays" at once; `opened` = every URL, `state` = AVPlay's. */
function fakeAvPlay() {
  const p = {
    opened: [],
    state: 'NONE',
    open(url) { p.opened.push(url); p.state = 'IDLE'; },
    setDisplayRect() {}, setDisplayMethod() {}, setStreamingProperty() {}, setBufferingParam() {}, setListener() {},
    prepareAsync(ok) { p.state = 'READY'; ok(); },
    play() { p.state = 'PLAYING'; },
    pause() { p.state = 'PAUSED'; },
    stop() { p.state = 'IDLE'; },
    close() { p.state = 'NONE'; },
    getState: () => p.state,
    getCurrentTime: () => 0,
    getDuration: () => 0,
    seekTo() {}
  };
  return p;
}

/** The app shell (CT.App's own methods) around a real Player on AVPlay; `plans` = every planner call. */
function samsungApp() {
  global.document = fakeDocument();
  global.webapis = { avplay: fakeAvPlay() };
  const plans = [];
  const app = Object.create(CT.App);
  let n = 0;
  app.planner = {
    plan: (r, force) => {
      plans.push(force);
      n++;
      return Promise.resolve({ kind: 'live', url: 'https://cdn.example/ct1-' + n + '.m3u8', startPositionMs: null });
    }
  };
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
  app.watchVisibility(global.document);
  return { app, plans, avplay: global.webapis.avplay };
}

const tick = () => new Promise((r) => setImmediate(r));

test('Samsung: hidden releases AVPlay; back in front resolves afresh and plays again', async (t) => {
  t.after(() => { delete global.webapis; });
  assert.equal(CT.Platform.backgroundPlayback, false);
  const { app, plans, avplay } = samsungApp();
  app.player.open({ kind: 'live', channelId: 'CH_1', title: 'ČT1' });
  await tick();
  assert.equal(avplay.state, 'PLAYING');
  assert.deepEqual(plans, [false]);
  global.document.setHidden(true);
  assert.equal(avplay.state, 'NONE', 'the TV app in the background gives AVPlay back');
  assert.deepEqual(plans, [false], 'nothing resolved while hidden');
  global.document.setHidden(false);
  await tick();
  assert.deepEqual(plans, [false, true], 'back in front: resolved again, past the cache');
  assert.equal(avplay.state, 'PLAYING');
  assert.deepEqual(avplay.opened, ['https://cdn.example/ct1-1.m3u8', 'https://cdn.example/ct1-2.m3u8']);
  assert.equal(app.ticks, 1, 'the clock catches up on return');
  app.player.close();
});

test('Samsung: hidden with no player open does nothing to playback; the guide reopens at now on return', () => {
  const { app, plans, avplay } = samsungApp();
  let shown = 0;
  app.tab = 'guide';
  app.now = 0;
  app.screens = { guide: { show: () => { shown++; } } };
  global.document.setHidden(true);
  global.document.setHidden(false);
  assert.deepEqual(plans, []);
  assert.deepEqual(avplay.opened, []);
  assert.equal(shown, 1);
  assert.ok(app.now > 0);
  assert.equal(app.ticks, 1);
});
