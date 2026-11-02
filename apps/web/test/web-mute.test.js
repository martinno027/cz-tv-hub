'use strict';
// Mute on the web (spec/apps.md "Mute (T34)"): the key M, the 🔊/🔇 button in the player bar before the fullscreen
// button (a click = the player's own toggle), the <video>'s muted carried to the next stream.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

const I = CT.WebInput;

test('M is mute, but a letter while typing or with a browser shortcut', () => {
  const k = (e, tag) => I.keyName(Object.assign({ keyCode: 77 }, e), {}, tag || 'BODY');
  assert.equal(k({ key: 'm' }), 'mute');
  assert.equal(k({ key: 'M' }), 'mute');
  assert.equal(k({ key: undefined }), 'mute'); // only a keyCode
  assert.equal(k({ key: 'm' }, 'INPUT'), null);
  assert.equal(k({ key: 'm' }, 'TEXTAREA'), null);
  assert.equal(k({ key: 'm', ctrlKey: true }), null);
  assert.equal(k({ key: 'm', metaKey: true }), null);
});

/** The elements adapt() touches; children kept per parent. */
function fakeDoc() {
  const element = (id, cls) => {
    const classes = new Set();
    const e = { id: id || '', className: cls || '', children: [], textContent: '', attrs: {},
      classList: { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)), add: (c) => classes.add(c), remove() {},
        contains: (c) => classes.has(c) },
      setAttribute(k, v) { this.attrs[k] = v; } };
    e.appendChild = (c) => { e.children.push(c); c.parent = e; };
    e.insertBefore = (c) => { e.children.unshift(c); c.parent = e; };
    Object.defineProperty(e, 'firstChild', { get: () => e.children[0] || null });
    return e;
  };
  const byId = {};
  ['top', 'osd', 'show'].forEach((id) => { byId[id] = element(id); });
  const doc = {
    documentElement: {},
    body: element('body'), // the own-window line (T46) goes there
    getElementById: (id) => byId[id] || null,
    querySelectorAll: () => [],
    createElement: () => element('')
  };
  return { doc, byId };
}

test('player bar: ‹ Zpět left, then the mute button, always on top (T48), the own-window button (T46) and fullscreen; the player\'s state reaches the button', () => {
  const { doc, byId } = fakeDoc();
  CT.Platform = CT.Platform || {};
  CT.WebPage.adapt(doc);
  const row = byId.osd.children[0];
  assert.equal(row.className, 'web-osd-row');
  const [back, buttons] = row.children;
  assert.equal(back.className, 'web-back-link');
  assert.deepEqual(buttons.children.map((c) => c.className), ['web-mute', 'web-pip hidden', 'web-window web-window-player hidden', 'web-fullscreen web-fullscreen-player']);
  const mute = buttons.children[0];
  assert.equal(mute.attrs.title, 'Vypnout zvuk');
  assert.equal(typeof CT.Platform.onMute, 'function');
  CT.Platform.onMute(true); // what Player.renderMute calls
  assert.equal(mute.textContent, CT.Mute.INDICATOR);
  assert.equal(mute.attrs['aria-label'], 'Zapnout zvuk');
  CT.Platform.onMute(false);
  assert.equal(mute.textContent, CT.Mute.ICON_ON);
});

test('a click on the mute button toggles the player (and keeps the bar up); not without a player', () => {
  const calls = [];
  const player = { open: true, isOpen() { return this.open; }, toggleMute: () => calls.push('toggle'), showOsd: () => calls.push('osd') };
  const doc = { addEventListener() {}, activeElement: null, body: {}, getElementById: () => ({ classList: { contains: () => false } }) };
  const app = { player, screens: { guide: {} } };
  const saved = CT.WebScroll.views;
  CT.WebScroll.views = () => ({});
  let input;
  try {
    input = I.install(doc, app, { history: {}, scale: () => 1 });
  } finally {
    CT.WebScroll.views = saved;
  }
  const target = (cls) => ({ closest: (sel) => (sel === cls ? {} : null), tagName: 'DIV' });
  input.click(target('.web-mute'));
  assert.deepEqual(calls, ['toggle', 'osd']);
  player.open = false;
  calls.length = 0;
  const F = CT.WebFocus;
  const act = F.act;
  F.act = () => calls.push('act');
  try {
    input.click(target('.web-mute'));
  } finally {
    F.act = act;
  }
  assert.ok(!calls.includes('toggle'));
});

test('web backend: the <video> stays muted for the next stream (the same element)', () => {
  const video = { muted: false, style: {}, addEventListener() {}, getAttribute: () => null, removeAttribute() {},
    load() {}, canPlayType: () => 'maybe', audioTracks: [] };
  const backend = new CT.WebPlayer.WebBackend({ onReady() {}, onError() {}, onEnded() {}, onBuffering() {} },
    { video, Hls: null, origin: 'http://gw:8098' });
  const session = new CT.Mute.Session();
  session.attach(backend.audioOutput());
  session.toggle();
  assert.equal(video.muted, true);
  backend.open({ url: 'http://gw:8098/dvb/a.m3u8' }, 0);
  backend.close();
  session.detach();
  session.attach(backend.audioOutput()); // the next programme
  assert.equal(video.muted, true);
  session.toggle();
  assert.equal(video.muted, false);
});
