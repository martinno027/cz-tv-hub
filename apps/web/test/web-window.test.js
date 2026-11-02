'use strict';
// Own window (spec/web.md "Own window (T46)"): ⧉ opens a popup, the tab stops its player and shows the line,
// ⧈ „Zpět do prohlížeče" / ✕ / „Vrátit sem" hand back, a refused popup says so, phones and tablets get no button.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

const W = CT.WebWindow;

/** BroadcastChannel between fake windows: a message reaches every other channel of the same name, at once. */
function bus() {
  const channels = [];
  function Channel(name) {
    this.name = name;
    this.onmessage = null;
    this.sent = [];
    channels.push(this);
  }
  Channel.prototype.postMessage = function (data) {
    this.sent.push(data.type);
    channels.forEach((c) => { if (c !== this && c.name === this.name && c.onmessage) c.onmessage({ data }); });
  };
  return Channel;
}

/** A window: what window.open made, closed by close(); listeners kept to fire pagehide/beforeunload. */
function fakeWin(opts) {
  const o = opts || {};
  const listeners = {};
  const intervals = [];
  const win = {
    name: o.name || '',
    location: { href: 'http://pi.local:8098/web/?demo' },
    innerWidth: 1440, innerHeight: 810, screenX: 40, screenY: 30,
    navigator: o.navigator || { userAgent: 'Mozilla/5.0 (Macintosh) Chrome/130', platform: 'MacIntel', maxTouchPoints: 0 },
    matchMedia: (q) => ({ matches: q === '(pointer: coarse)' && !!o.coarse }),
    BroadcastChannel: o.channel === undefined ? undefined : o.channel,
    opener: o.opener || null,
    closed: false,
    opened: [],
    focused: 0,
    open(url, name, features) {
      this.opened.push({ url, name, features });
      if (o.blocked) return null;
      const child = o.child ? o.child() : fakeWin({ name, channel: this.BroadcastChannel, opener: this });
      return child;
    },
    close() { this.closed = true; this.fire('pagehide'); },
    focus() { this.focused++; },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    fire(type) { (listeners[type] || []).forEach((fn) => fn({ type })); },
    setInterval(fn) { intervals.push(fn); return intervals.length; },
    clearInterval(i) { intervals[i - 1] = null; },
    tick() { intervals.forEach((fn) => fn && fn()); },
    intervals
  };
  return win;
}

function hooks() {
  const h = { stops: 0, toasts: [], renders: 0 };
  h.api = { stopPlayer() { h.stops++; }, toast(t) { h.toasts.push(t); }, render() { h.renders++; } };
  return h;
}

function button() {
  const classes = new Set();
  const attrs = {};
  return { classes, attrs, textContent: '', classList: { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)) },
    setAttribute: (k, v) => { attrs[k] = v; } };
}

test('button: ⧉ „Samostatné okno" in a tab, ⧈ „Zpět do prohlížeče" in the popup', () => {
  const tab = new W.Controller(fakeWin(), {});
  const popup = new W.Controller(fakeWin({ name: 'televize' }), {});
  assert.equal(tab.popup, false);
  assert.equal(popup.popup, true);
  const a = button();
  const b = button();
  W.render(tab, [a], null);
  W.render(popup, [b], null);
  assert.equal(a.textContent, '⧉ Samostatné okno');
  assert.equal(a.attrs.title, 'Samostatné okno');
  assert.equal(a.attrs['aria-label'], 'Samostatné okno');
  assert.ok(!a.classes.has('hidden'));
  assert.equal(b.textContent, '⧈ Zpět do prohlížeče');
  assert.equal(b.attrs.title, 'Zpět do prohlížeče');
  assert.ok(!b.classes.has('hidden'));
  assert.notEqual(a.textContent, b.textContent);
});

test('⧉: a popup named „televize" at the window\'s place and size; the tab stops its player and shows the line', () => {
  const Channel = bus();
  const win = fakeWin({ channel: Channel });
  const h = hooks();
  const c = new W.Controller(win, h.api).start();
  assert.equal(c.elsewhere, false);
  assert.equal(c.open(), 'opened');
  assert.deepEqual(win.opened, [{ url: 'http://pi.local:8098/web/?demo', name: 'televize',
    features: 'popup,width=1440,height=810,left=40,top=30' }]);
  assert.equal(c.elsewhere, true);
  assert.equal(h.stops, 1);
  const line = button();
  W.render(c, null, line);
  assert.ok(line.classes.has('visible'));
});

test('the popup announces itself: every other tab stops its player and shows the line (also a reloaded tab asking)', () => {
  const Channel = bus();
  const h = hooks();
  const tab = new W.Controller(fakeWin({ channel: Channel }), h.api).start();
  const popupWin = fakeWin({ name: 'televize', channel: Channel });
  const popup = new W.Controller(popupWin, {}).start();
  assert.equal(tab.elsewhere, true);
  assert.equal(h.stops, 1);
  // a tab loaded later asks and the popup answers
  const h2 = hooks();
  const late = new W.Controller(fakeWin({ channel: Channel }), h2.api).start();
  assert.equal(late.elsewhere, true);
  assert.equal(h2.stops, 1);
  assert.equal(popup.elsewhere, false); // the popup itself never shows the line
});

test('„Zpět do prohlížeče" in the popup: the tab takes over (focused, line gone), the popup closes', () => {
  const Channel = bus();
  const tabWin = fakeWin({ channel: Channel });
  const tab = new W.Controller(tabWin, hooks().api).start();
  let popupWin = null;
  tabWin.open = function (url, name) {
    popupWin = fakeWin({ name, channel: Channel, opener: tabWin });
    return popupWin;
  };
  tab.open();
  const popup = new W.Controller(popupWin, {}).start();
  assert.equal(tab.elsewhere, true);
  assert.equal(popup.toggle(), 'back');
  assert.equal(popupWin.closed, true);
  assert.equal(tabWin.focused, 1);
  assert.equal(tab.elsewhere, false);
});

test('the popup\'s own ✕ (pagehide or beforeunload) hands back too', () => {
  for (const event of ['pagehide', 'beforeunload']) {
    const Channel = bus();
    const tab = new W.Controller(fakeWin({ channel: Channel }), hooks().api).start();
    const popupWin = fakeWin({ name: 'televize', channel: Channel });
    new W.Controller(popupWin, {}).start();
    assert.equal(tab.elsewhere, true, event);
    popupWin.fire(event);
    assert.equal(tab.elsewhere, false, event);
  }
});

test('without BroadcastChannel the tab still gets back when its popup was closed (handle polled)', () => {
  const win = fakeWin(); // no channel
  const c = new W.Controller(win, hooks().api).start();
  let child = null;
  win.open = () => { child = { closed: false, close() { this.closed = true; } }; return child; };
  c.open();
  assert.equal(win.intervals.length, 1);
  win.tick();
  assert.equal(c.elsewhere, true); // still open
  child.closed = true;
  win.tick();
  assert.equal(c.elsewhere, false);
  // „Vrátit sem" there closes the popup by its handle
  c.open();
  assert.equal(c.elsewhere, true);
  c.returnHere();
  assert.equal(child.closed, true);
  assert.equal(c.elsewhere, false);
});

test('„Vrátit sem" in the tab: closes its popup and the line goes; a tab without the handle asks over the channel', () => {
  const Channel = bus();
  const tabWin = fakeWin({ channel: Channel });
  const tab = new W.Controller(tabWin, hooks().api).start();
  let popupWin = null;
  tabWin.open = function (url, name) {
    popupWin = fakeWin({ name, channel: Channel, opener: tabWin });
    return popupWin;
  };
  tab.open();
  new W.Controller(popupWin, {}).start();
  const other = new W.Controller(fakeWin({ channel: Channel }), hooks().api).start();
  assert.equal(other.elsewhere, true);
  tab.returnHere();
  assert.equal(popupWin.closed, true);
  assert.equal(tab.elsewhere, false);
  assert.equal(other.elsewhere, false);

  // the other tab (no handle): the popup closes itself on 'return'
  tab.open();
  new W.Controller(popupWin, {}).start();
  assert.equal(other.elsewhere, true);
  other.returnHere();
  assert.equal(popupWin.closed, true);
  assert.equal(tab.elsewhere, false);
});

test('a refused popup: the message, no line, the player keeps playing', () => {
  const h = hooks();
  const win = fakeWin({ blocked: true });
  const c = new W.Controller(win, h.api).start();
  assert.equal(c.open(), 'blocked');
  assert.deepEqual(h.toasts, ['Prohlížeč samostatné okno zablokoval – povolte vyskakovací okna pro tuto stránku.']);
  assert.equal(c.elsewhere, false);
  assert.equal(h.stops, 0);
  // window.open throwing (some blockers) is the same
  const h2 = hooks();
  const w2 = fakeWin();
  w2.open = () => { throw new Error('blocked'); };
  assert.equal(new W.Controller(w2, h2.api).open(), 'blocked');
  assert.equal(h2.toasts.length, 1);
});

test('phones and tablets: no button, nothing opens (coarse pointer, iPhone, iPad, iPadOS as a Mac)', () => {
  const touch = [
    fakeWin({ coarse: true }),
    fakeWin({ navigator: { userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)', platform: 'iPhone', maxTouchPoints: 5 } }),
    fakeWin({ navigator: { userAgent: 'Mozilla/5.0 (iPad; CPU OS 16_0)', platform: 'iPad', maxTouchPoints: 5 } }),
    fakeWin({ navigator: { userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605', platform: 'MacIntel', maxTouchPoints: 5 } })
  ];
  for (const win of touch) {
    assert.equal(W.touchDevice(win), true, win.navigator.userAgent);
    const c = new W.Controller(win, hooks().api);
    const b = button();
    W.render(c, [b], null);
    assert.ok(b.classes.has('hidden'), win.navigator.userAgent);
    assert.equal(c.open(), null);
    assert.equal(win.opened.length, 0);
  }
  const desk = fakeWin();
  assert.equal(W.touchDevice(desk), false);
  const b = button();
  W.render(new W.Controller(desk, {}), [b], null);
  assert.ok(!b.classes.has('hidden'));
});

test('the popup whose tab was closed meanwhile: „Zpět do prohlížeče" opens the page in a tab, then closes', () => {
  const popupWin = fakeWin({ name: 'televize' });
  const c = new W.Controller(popupWin, {});
  c.back();
  assert.deepEqual(popupWin.opened.map((o) => o.name), ['_blank']);
  assert.equal(popupWin.closed, true);
});

test('page glue: while the line is up a click only reaches „Vrátit sem"; ⧉ goes to the controller', () => {
  const calls = [];
  const own = { elsewhere: false, toggle() { calls.push('toggle'); }, returnHere() { calls.push('return'); } };
  const handlers = {};
  const doc = {
    activeElement: null,
    getElementById: () => ({ classList: { contains: () => false } }),
    addEventListener(type, fn) { handlers[type] = fn; },
    dispatchEvent() {}
  };
  const app = { screens: { guide: {} }, player: { isOpen: () => false } };
  const savedViews = CT.WebScroll.views;
  CT.WebScroll.views = () => ({});
  try {
    CT.WebInput.install(doc, app, { history: { back() { calls.push('history'); } }, window: own, scale: () => 1 });
  } finally {
    CT.WebScroll.views = savedViews;
  }
  const target = (selector) => ({ closest: (s) => (s === selector ? {} : null) });
  const tap = (t) => {
    handlers.pointerdown({ button: 0, clientX: 5, clientY: 5, pointerType: 'mouse', target: t, preventDefault() {} });
    handlers.pointerup({ button: 0, clientX: 5, clientY: 5, pointerType: 'mouse', target: t, preventDefault() {} });
  };
  tap(target('.web-window'));
  assert.deepEqual(calls, ['toggle']);
  own.elsewhere = true;
  tap(target('.web-back-link')); // under the line nothing else acts
  tap(target('.web-window'));
  assert.deepEqual(calls, ['toggle']);
  tap(target('.web-elsewhere-return'));
  assert.deepEqual(calls, ['toggle', 'return']);
});

test('page glue: under the line the keyboard only knows Enter/Space („Vrátit sem"); browser shortcuts pass', () => {
  const Channel = bus();
  const keydown = [];
  const els = { 'web-elsewhere': button() };
  const doc = {
    querySelectorAll: () => [button()],
    getElementById: (id) => els[id] || null,
    addEventListener(type, fn) { if (type === 'keydown') keydown.push(fn); }
  };
  const app = { player: { open: true, isOpen() { return this.open; }, close() { this.open = false; } }, toast() {} };
  const win = fakeWin({ channel: Channel });
  const own = CT.WebPage.ownWindow(doc, win, app);
  const key = (k, extra) => {
    const e = Object.assign({ key: k, prevented: false, stopped: false,
      preventDefault() { this.prevented = true; }, stopImmediatePropagation() { this.stopped = true; } }, extra);
    keydown.forEach((fn) => fn(e));
    return e;
  };
  assert.equal(key('ArrowDown').stopped, false); // no popup: the app's keys
  own.open();
  assert.equal(app.player.open, false); // the player stopped
  assert.ok(els['web-elsewhere'].classes.has('visible'));
  const arrow = key('ArrowDown');
  assert.ok(arrow.stopped && arrow.prevented);
  assert.equal(own.elsewhere, true);
  assert.equal(key('r', { metaKey: true }).stopped, false); // Cmd+R stays the browser's
  key('Enter');
  assert.equal(own.elsewhere, false);
  assert.ok(!els['web-elsewhere'].classes.has('visible'));
});

test('opened from the player: the popup starts what played here, once; only the tab that opened it hands it over', () => {
  const Channel = bus();
  const tabWin = fakeWin({ channel: Channel });
  const what = { request: { kind: 'dvbRecorded', channelId: 'nova1', title: 'Zprávy', recordingId: 'r1' }, resumeMs: 754000, timelineAt: null };
  const order = [];
  const tab = new W.Controller(tabWin, { playing: () => { order.push('playing'); return what; }, stopPlayer: () => order.push('stop') }).start();
  const other = new W.Controller(fakeWin({ channel: Channel }), { playing: () => ({ request: { kind: 'live' } }) }).start();
  let popupWin = null;
  tabWin.open = function (url, name) { popupWin = fakeWin({ name, channel: Channel, opener: tabWin }); return popupWin; };
  tab.open();
  assert.deepEqual(order, ['playing', 'stop']); // taken before the player stops
  const played = [];
  new W.Controller(popupWin, { play: (w) => played.push(w) }).start();
  assert.deepEqual(played, [what]); // the other tab (also told) never hands anything over
  assert.equal(other.elsewhere, true);
  // the popup reloaded: announces itself again, nothing is started twice
  const again = [];
  new W.Controller(popupWin, { play: (w) => again.push(w) }).start();
  assert.deepEqual(again, []);
});

test('opened from the tab bar (nothing plays): the popup starts nothing', () => {
  const Channel = bus();
  const tabWin = fakeWin({ channel: Channel });
  const tab = new W.Controller(tabWin, { playing: () => null }).start();
  let popupWin = null;
  tabWin.open = function (url, name) { popupWin = fakeWin({ name, channel: Channel, opener: tabWin }); return popupWin; };
  tab.open();
  const played = [];
  new W.Controller(popupWin, { play: (w) => played.push(w) }).start();
  assert.deepEqual(played, []);
});

test('playing(): the request, and once the stream is there the position as player.js keeps it', () => {
  const P = CT.WebPage.playing;
  assert.equal(P(null), null);
  assert.equal(P({ isOpen: () => false, request: { kind: 'live' } }), null);
  const live = { kind: 'live', channelId: 'ct1', title: 'ČT1' };
  // still loading: no position (it would be 0 and lose a „Pokračovat" start)
  assert.deepEqual(P({ isOpen: () => true, request: live, target: null, resumePosition: () => 0 }),
    { request: live, resumeMs: null, timelineAt: null });
  const vod = { kind: 'archive', idec: '1', title: 'X' };
  assert.deepEqual(P({ isOpen: () => true, request: vod, target: {}, tl: null, resumePosition: () => 123000 }),
    { request: vod, resumeMs: 123000, timelineAt: null });
  const at = { id: 'w1', time: 1767000000000 };
  assert.deepEqual(P({ isOpen: () => true, request: vod, target: {}, tl: { continueAt: () => at }, resumePosition: () => null }),
    { request: vod, resumeMs: null, timelineAt: at });
  assert.deepEqual(P({ isOpen: () => true, request: vod, target: {}, tl: null, resumePosition: () => { throw new Error('x'); } }),
    { request: vod, resumeMs: null, timelineAt: null });
});

/** A fake app whose player.open loads like player.js (load(false, null)) and records what was planned. */
function fakeApp(timelineFails) {
  const calls = [];
  const planner = {
    plan(request, force) { calls.push(['plan', request.kind, force]); return Promise.resolve({ kind: 'vod' }); },
    timelineWindow(id, title, time) {
      calls.push(['window', id, time]);
      return timelineFails ? Promise.reject(new Error('gone')) : Promise.resolve({ kind: 'timeline' });
    }
  };
  const player = {
    timelineAt: null,
    open(request) { this.request = request; this.timelineAt = null; this.load(false, null); },
    load(force, resumeMs) {
      const at = this.timelineAt;
      calls.push(['load', resumeMs, at ? at.id : null]);
      this.planned = at ? planner.timelineWindow(at.id, this.request.title, at.time) : planner.plan(this.request, force);
    }
  };
  const app = { player, planner, play(r) { calls.push(['play', r.kind]); player.open(r); } };
  return { app, calls };
}

test('playHere: live as live, a recording at its position, a timeline at its point — one load each', async () => {
  let f = fakeApp();
  CT.WebPage.playHere(f.app, { request: { kind: 'live', channelId: 'ct1' }, resumeMs: null, timelineAt: null });
  assert.deepEqual(f.calls, [['play', 'live'], ['load', null, null], ['plan', 'live', false]]);

  f = fakeApp();
  CT.WebPage.playHere(f.app, { request: { kind: 'archive', idec: '1' }, resumeMs: 754000, timelineAt: null });
  assert.deepEqual(f.calls, [['play', 'archive'], ['load', 754000, null], ['plan', 'archive', false]]);
  const original = f.app.player.load;
  assert.equal(original.length, 2); // the one-off is gone, the player's own load is back
  f.app.player.open({ kind: 'live' }); // the next open loads from the start again
  assert.deepEqual(f.calls.slice(-2), [['load', null, null], ['plan', 'live', false]]);

  f = fakeApp();
  const at = { id: 'w1', time: 1767000000000 };
  CT.WebPage.playHere(f.app, { request: { kind: 'dvbRecorded', title: 'Zprávy' }, resumeMs: null, timelineAt: at });
  assert.deepEqual(f.calls, [['play', 'dvbRecorded'], ['load', null, 'w1'], ['window', 'w1', 1767000000000]]);
  assert.deepEqual(await f.app.player.planned, { kind: 'timeline' });
  assert.equal(f.app.planner.timelineWindow.length, 3); // the planner's own again
});

test('playHere: a timeline window the planner cannot open any more → the request from its own start', async () => {
  const f = fakeApp(true);
  CT.WebPage.playHere(f.app, { request: { kind: 'dvbRecorded', title: 'Zprávy' }, resumeMs: null, timelineAt: { id: 'w9', time: 1 } });
  assert.deepEqual(await f.app.player.planned, { kind: 'vod' });
  assert.deepEqual(f.calls, [['play', 'dvbRecorded'], ['load', null, 'w9'], ['window', 'w9', 1], ['plan', 'dvbRecorded', false]]);
  assert.equal(f.app.planner.timelineWindow.length, 3);
});

test('playHere on a player whose load is on its prototype (player.js): the one-off goes, the prototype\'s stays', () => {
  const seen = [];
  function Player() { this.timelineAt = null; }
  Player.prototype.open = function (r) { this.request = r; this.timelineAt = null; this.load(false, null); };
  Player.prototype.load = function (force, resumeMs) { seen.push(resumeMs); };
  const player = new Player();
  const app = { player, planner: {}, play(r) { player.open(r); } };
  CT.WebPage.playHere(app, { request: { kind: 'archive' }, resumeMs: 5000, timelineAt: null });
  assert.deepEqual(seen, [5000]);
  assert.ok(!Object.prototype.hasOwnProperty.call(player, 'load'));
  player.open({ kind: 'archive' });
  assert.deepEqual(seen, [5000, null]);
});

test('the popup starts a handed-over stream only once (a second „play" from another tab is ignored)', () => {
  const played = [];
  const popup = new W.Controller(fakeWin({ name: 'televize' }), { play: (w) => played.push(w) });
  popup.onMessage({ type: 'play', play: { request: { kind: 'live' } } });
  popup.onMessage({ type: 'play', play: { request: { kind: 'archive' } } });
  assert.deepEqual(played.map((w) => w.request.kind), ['live']);
});
