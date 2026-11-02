'use strict';
// Always on top (spec/web.md "Always on top (T48)"): which Picture-in-Picture this browser gets (Document PiP / video
// PiP / none, secure or not), and the moves of the one <video> element: into the window, back on every way out.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

const P = CT.WebPip;
const tick = () => new Promise((r) => setImmediate(r));

/** A tiny DOM: enough for appendChild/insertBefore moves, classes, attributes and listeners. */
class Node {
  constructor(tag, doc) {
    this.tagName = tag;
    this.ownerDocument = doc || null;
    this.children = [];
    this.parentNode = null;
    this.listeners = {};
    this.attrs = {};
    this.className = '';
    this.textContent = '';
    const cl = new Set();
    this.classes = cl;
    this.classList = {
      toggle: (c, on) => { if (on === undefined) on = !cl.has(c); if (on) cl.add(c); else cl.delete(c); },
      contains: (c) => cl.has(c),
      add: (c) => cl.add(c)
    };
  }
  get nextSibling() {
    if (!this.parentNode) return null;
    const k = this.parentNode.children;
    return k[k.indexOf(this) + 1] || null;
  }
  appendChild(n) { return this.insertBefore(n, null); }
  insertBefore(n, ref) {
    if (n.parentNode) n.parentNode.children.splice(n.parentNode.children.indexOf(n), 1);
    n.parentNode = this;
    n.ownerDocument = this.ownerDocument;
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i < 0) this.children.push(n);
    else this.children.splice(i, 0, n);
    if (n.onMove) n.onMove();
    return n;
  }
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  fire(t, e) { (this.listeners[t] || []).forEach((fn) => fn(e || { type: t })); }
  setAttribute(k, v) { this.attrs[k] = v; }
}

function fakeDoc() {
  const doc = new Node('#document');
  doc.ownerDocument = doc;
  doc.documentElement = new Node('HTML', doc);
  doc.head = new Node('HEAD', doc);
  doc.body = new Node('BODY', doc);
  doc.createElement = (tag) => new Node(tag.toUpperCase(), doc);
  return doc;
}

/** The page: #player holding the <video> before #player-message. `pauseOnMove`: a browser that pauses moved media. */
function page(opts) {
  const o = opts || {};
  const doc = fakeDoc();
  doc.pictureInPictureEnabled = o.pipEnabled === undefined ? true : o.pipEnabled;
  doc.pictureInPictureElement = null;
  doc.exits = 0;
  doc.exitPictureInPicture = () => { doc.exits++; doc.pictureInPictureElement = null; return Promise.resolve(); };
  const player = doc.body.appendChild(new Node('DIV', doc));
  const video = player.appendChild(new Node('VIDEO', doc));
  const message = player.appendChild(new Node('DIV', doc));
  video.paused = false;
  video.plays = 0;
  video.videoWidth = o.videoWidth === undefined ? 640 : o.videoWidth;
  video.videoHeight = o.videoHeight === undefined ? 360 : o.videoHeight;
  video.play = () => { video.paused = false; video.plays++; return Promise.resolve(); };
  if (o.pauseOnMove) video.onMove = () => { video.paused = true; };
  if (o.videoPip !== false) {
    video.requestPictureInPicture = () => {
      video.pipRequests = (video.pipRequests || 0) + 1;
      if (o.videoPipRefused) return Promise.reject(new Error('NotAllowedError'));
      doc.pictureInPictureElement = video;
      return Promise.resolve({});
    };
  }
  return { doc, player, video, message };
}

/** A Document PiP window: its own document; close() fires pagehide as the browser does. */
function pipWindow() {
  const w = { document: fakeDoc(), closed: false, closes: 0, listeners: {} };
  w.addEventListener = (t, fn) => { (w.listeners[t] = w.listeners[t] || []).push(fn); };
  w.close = () => {
    w.closes++;
    if (w.closed) return;
    w.closed = true;
    (w.listeners.pagehide || []).forEach((fn) => fn({ type: 'pagehide' }));
  };
  return w;
}

const CHROME = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36';
const SAFARI = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15';
const FIREFOX = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:135.0) Gecko/20100101 Firefox/135.0';

/** `docPip`: the browser exposes documentPictureInPicture; `refuse`: requestWindow rejects. */
function fakeWin(opts) {
  const o = opts || {};
  const win = { isSecureContext: o.secure !== false, navigator: { userAgent: o.ua || CHROME }, requests: [], windows: [] };
  if (o.docPip !== false) {
    win.documentPictureInPicture = {
      requestWindow(size) {
        win.requests.push(size);
        if (o.refuse) return Promise.reject(new Error('NotAllowedError'));
        const w = pipWindow();
        win.windows.push(w);
        return o.hold ? new Promise((r) => { win.release = () => r(w); }) : Promise.resolve(w);
      }
    };
  }
  return win;
}

/** A controller on `pg` with recording hooks and a rendered button + line. */
function setup(win, pg, opts) {
  const o = opts || {};
  const h = { open: o.open === undefined ? true : o.open, toasts: [], playPauses: 0, mutes: 0, muted: false, text: '' };
  const button = new Node('DIV');
  const line = new Node('DIV');
  const c = new P.Controller(win, {
    video: () => pg.video,
    playerOpen: () => h.open,
    playPause: () => { h.playPauses++; pg.video.paused = !pg.video.paused; },
    toggleMute: () => { h.mutes++; h.muted = !h.muted; },
    muted: () => h.muted,
    message: () => h.text,
    toast: (t) => h.toasts.push(t),
    render: (ctl) => P.render(ctl, [button], line),
    cssUrl: 'https://cztvhub.example.ts.net:8443/web/css/pip.css'
  });
  c.set('off');
  return { c, h, button, line };
}

test('mode: Document PiP in a secure context, else video PiP, else none (Firefox)', () => {
  const pg = page();
  assert.equal(P.mode(fakeWin(), pg.video), 'document');
  // Chrome over plain http://cztvhub.local:8098: no Document PiP (also if an API object were there)
  assert.equal(P.mode(fakeWin({ secure: false }), pg.video), 'video');
  assert.equal(P.mode(fakeWin({ secure: false, docPip: false }), pg.video), 'video');
  // Safari: only the video's own PiP
  assert.equal(P.mode(fakeWin({ docPip: false, ua: SAFARI }), pg.video), 'video');
  // Firefox: neither API (its own toggle over the video is the browser's)
  assert.equal(P.mode(fakeWin({ docPip: false, ua: FIREFOX }), page({ videoPip: false }).video), 'none');
  // PiP switched off for the document (a policy) or the element
  assert.equal(P.mode(fakeWin({ docPip: false }), page({ pipEnabled: false }).video), 'none');
  const off = page();
  off.video.disablePictureInPicture = true;
  assert.equal(P.mode(fakeWin({ docPip: false }), off.video), 'none');
  assert.notEqual(P.mode(fakeWin(), pg.video), P.mode(fakeWin({ secure: false }), pg.video)); // the two differ
});

test('https hint only where a Chromium 116+ lacks Document PiP because the page is plain HTTP', () => {
  assert.equal(P.httpsWouldHelp(fakeWin({ secure: false })), true);
  assert.equal(P.httpsWouldHelp(fakeWin({ secure: false, ua: CHROME.replace('Chrome/156', 'Chrome/110') })), false);
  assert.equal(P.httpsWouldHelp(fakeWin({ secure: false, ua: CHROME + ' Edg/156.0.0.0' })), true);
  assert.equal(P.httpsWouldHelp(fakeWin({ secure: false, ua: SAFARI })), false);
  assert.equal(P.httpsWouldHelp(fakeWin({ secure: false, ua: FIREFOX })), false);
  assert.equal(P.httpsWouldHelp(fakeWin({ secure: true })), false);
});

test('window size: 480 px wide, height by the video\'s aspect, 16:9 before it is known', () => {
  assert.deepEqual(P.size(640, 360), { width: 480, height: 270 });
  assert.deepEqual(P.size(720, 576), { width: 480, height: 384 });
  assert.deepEqual(P.size(0, 0), { width: 480, height: 270 });
});

test('key P: not while typing, not with a browser shortcut', () => {
  assert.equal(P.isPipKey({ key: 'p' }, 'DIV'), true);
  assert.equal(P.isPipKey({ key: 'P' }, ''), true);
  assert.equal(P.isPipKey({ keyCode: 80 }, ''), true);
  assert.equal(P.isPipKey({ key: 'p' }, 'INPUT'), false);
  assert.equal(P.isPipKey({ key: 'p', metaKey: true }, ''), false);
  assert.equal(P.isPipKey({ key: 'p', ctName: 'enter' }, ''), false);
  assert.equal(P.isPipKey({ key: 'f' }, ''), false);
});

test('Document PiP: the same element moves into the window, the page shows its line; ✕ moves it back, playing', async () => {
  const win = fakeWin();
  const pg = page();
  const { c, button, line } = setup(win, pg);
  assert.equal(button.classes.has('hidden'), false);
  assert.equal(button.textContent, '📌 Vždy navrchu');
  assert.equal(c.open(), 'document');
  assert.deepEqual(win.requests, [{ width: 480, height: 270 }]);
  assert.equal(line.classes.has('visible'), true); // already while the window opens
  await tick();
  const w = win.windows[0];
  assert.equal(c.state, 'document');
  assert.equal(pg.video.ownerDocument, w.document); // moved, not a copy
  assert.equal(pg.player.children.indexOf(pg.video), -1);
  assert.equal(c.ui.stage.children[0], pg.video);
  assert.equal(w.document.head.children[0].href, 'https://cztvhub.example.ts.net:8443/web/css/pip.css');
  assert.equal(button.textContent, '📌 Zpět do okna');
  assert.equal(pg.video.paused, false);
  w.close(); // the window's ✕ (pagehide)
  assert.equal(c.state, 'off');
  assert.deepEqual(pg.player.children, [pg.video, pg.message]); // where it was, before the message
  assert.equal(pg.video.ownerDocument, pg.doc);
  assert.equal(pg.video.paused, false);
  assert.equal(line.classes.has('visible'), false);
  assert.equal(button.textContent, '📌 Vždy navrchu');
});

test('a browser that pauses moved media: played again in the window and back in the page', async () => {
  const win = fakeWin();
  const pg = page({ pauseOnMove: true });
  const { c } = setup(win, pg);
  c.open();
  await tick();
  assert.equal(pg.video.paused, false);
  assert.equal(pg.video.plays, 1);
  c.back();
  assert.equal(pg.video.paused, false);
  assert.equal(pg.video.plays, 2);
  // a paused video stays paused through the moves
  const pg2 = page({ pauseOnMove: true });
  pg2.video.paused = true;
  const s2 = setup(fakeWin(), pg2);
  s2.c.open();
  await tick();
  s2.c.back();
  assert.equal(pg2.video.plays, 0);
});

test('„Zpět do okna" in the window: back into the page and the window closed (once)', async () => {
  const win = fakeWin();
  const pg = page();
  const { c } = setup(win, pg);
  c.open();
  await tick();
  const w = win.windows[0];
  c.ui.back.fire('click');
  assert.equal(w.closed, true);
  assert.equal(c.state, 'off');
  assert.equal(pg.video.parentNode, pg.player);
  assert.equal(pg.player.children.filter((n) => n === pg.video).length, 1);
});

test('the window\'s bar and keys: ⏸/▶ and 🔊/🔇 follow the player, Space, M, Esc; the player\'s message shown', async () => {
  const win = fakeWin();
  const pg = page();
  const { c, h } = setup(win, pg);
  c.open();
  await tick();
  const w = win.windows[0];
  assert.equal(c.ui.playPause.textContent, '⏸');
  assert.equal(c.ui.mute.textContent, '🔊');
  c.ui.playPause.fire('click');
  assert.equal(h.playPauses, 1);
  assert.equal(c.ui.playPause.textContent, '▶');
  w.document.fire('keydown', { key: 'm', preventDefault() {} });
  assert.equal(h.mutes, 1);
  assert.equal(c.ui.mute.textContent, '🔇');
  w.document.fire('keydown', { key: ' ', preventDefault() {} });
  assert.equal(h.playPauses, 2);
  w.document.fire('keydown', { key: 'm', metaKey: true, preventDefault() {} }); // the browser's shortcut
  assert.equal(h.mutes, 1);
  h.text = 'Načítání…';
  c.renderWindow();
  assert.equal(c.ui.message.textContent, 'Načítání…');
  assert.equal(c.ui.message.classes.has('visible'), true);
  w.document.fire('keydown', { key: 'Escape', preventDefault() {} });
  assert.equal(c.state, 'off');
  assert.equal(pg.video.parentNode, pg.player);
});

test('the player closing (end, the T46 popup taking over) brings the picture back and closes the window', async () => {
  const win = fakeWin();
  const pg = page();
  const { c } = setup(win, pg);
  c.open();
  await tick();
  c.playerClosed();
  assert.equal(win.windows[0].closed, true);
  assert.equal(pg.video.parentNode, pg.player);
  c.playerClosed(); // nothing on top: nothing to do
  assert.equal(c.state, 'off');
});

test('the player closing while the window opens: the window is closed at once, the element never moves', async () => {
  const win = fakeWin({ hold: true });
  const pg = page();
  const { c, h, line } = setup(win, pg);
  c.open();
  h.open = false;
  c.playerClosed();
  assert.equal(line.classes.has('visible'), false);
  win.release();
  await tick();
  assert.equal(win.windows[0].closed, true);
  assert.equal(pg.video.parentNode, pg.player);
  assert.equal(c.state, 'off');
});

test('„Vrátit sem" while the window opens (the player plays on): the window that comes is closed, nothing moves', async () => {
  const win = fakeWin({ hold: true });
  const pg = page();
  const { c, line } = setup(win, pg);
  c.open();
  assert.equal(c.away(), true);
  c.back();
  assert.equal(line.classes.has('visible'), false);
  win.release();
  await tick();
  assert.equal(win.windows[0].closed, true);
  assert.equal(pg.video.parentNode, pg.player);
  assert.equal(c.state, 'off');
});

test('nothing playing: no window asked for; a refused window says so and the picture stays', async () => {
  const pg = page();
  const idle = fakeWin();
  assert.equal(setup(idle, pg, { open: false }).c.open(), null);
  assert.deepEqual(idle.requests, []);
  const win = fakeWin({ refuse: true });
  const { c, h, line } = setup(win, pg);
  assert.equal(c.open(), 'document');
  await tick();
  assert.equal(c.state, 'off');
  assert.deepEqual(h.toasts, [P.FAILED]);
  assert.equal(pg.video.parentNode, pg.player);
  assert.equal(line.classes.has('visible'), false);
});

test('video PiP (plain HTTP in Chrome): the browser\'s picture window, the page keeps its player; the https hint once', async () => {
  const win = fakeWin({ secure: false });
  const pg = page();
  const { c, h, button, line } = setup(win, pg);
  assert.equal(c.open(), 'video');
  await tick();
  assert.equal(pg.video.pipRequests, 1);
  assert.deepEqual(win.requests, []); // no Document PiP over http
  assert.equal(c.state, 'video');
  assert.equal(pg.video.parentNode, pg.player); // never moved
  assert.equal(line.classes.has('visible'), false);
  assert.equal(button.textContent, '📌 Zpět do okna');
  assert.deepEqual(h.toasts, [P.HTTPS_HINT]);
  c.toggle(); // „Zpět do okna"
  assert.equal(pg.doc.exits, 1);
  assert.equal(c.state, 'off');
  c.open();
  await tick();
  pg.video.fire('leavepictureinpicture'); // the browser's own ✕
  assert.equal(c.state, 'off');
  assert.deepEqual(h.toasts, [P.HTTPS_HINT]); // once
});

test('video PiP in Safari: no https hint (Safari has no Document PiP anywhere); refused says so', async () => {
  const pg = page();
  const { c, h } = setup(fakeWin({ docPip: false, secure: false, ua: SAFARI }), pg);
  c.open();
  await tick();
  assert.equal(c.state, 'video');
  assert.deepEqual(h.toasts, []);
  const refused = setup(fakeWin({ docPip: false, ua: SAFARI }), page({ videoPipRefused: true }));
  refused.c.open();
  await tick();
  assert.equal(refused.c.state, 'off');
  assert.deepEqual(refused.h.toasts, [P.FAILED]);
});

test('Firefox (neither API): the button is hidden and does nothing', () => {
  const pg = page({ videoPip: false });
  const { c, button } = setup(fakeWin({ docPip: false, ua: FIREFOX }), pg);
  assert.equal(button.classes.has('hidden'), true);
  assert.equal(c.open(), null);
  assert.equal(c.state, 'off');
});
