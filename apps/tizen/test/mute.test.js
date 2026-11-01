'use strict';
// spec/apps.md "Mute (T34)": one muted state for the app session, carried to the next programme; on the Samsung the
// TV's audio control while a player is open (AVPlay has no mute), the TV's own mute given back when it closes; the
// yellow key; the Anténa timeline's "Zvuk" choice with the mute item.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

global.window = global; // the ui scripts are browser scripts; a minimal document below stands in for the page

/** Elements by id with what the player and the timeline touch (classList, style, text, children). */
function fakeDocument() {
  const byId = {};
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
    byId,
    getElementById: (id) => byId[id] || (byId[id] = make()),
    createElement: () => make()
  };
}

global.document = fakeDocument();
require('../app/js/ui/platform.js');
require('../app/js/ui/keys.js');
require('../app/js/ui/zap.js');
require('../app/js/ui/timeline.js');
require('../app/js/ui/player.js');
CT.Demo = { enabled: false };

/** tizen.tvaudiocontrol with the TV's mute in `state.muted`; `calls` = every setMute. */
function fakeTvAudio(muted, opts) {
  const o = opts || {};
  const state = { muted: !!muted, calls: [] };
  state.control = {
    isMute: () => state.muted,
    setMute: (m) => {
      if (o.refuse) throw new Error('SecurityError');
      state.calls.push(m);
      state.muted = m;
    }
  };
  return state;
}

/** A recorded output for the Session (as ElementAudio). */
function fakeOutput() {
  const o = { applied: [], released: 0 };
  o.apply = (m) => { o.applied.push(m); return true; };
  o.release = () => { o.released++; };
  return o;
}

// -- the session --------------------------------------------------------------------------------------------------

test('session: the state carries to the next player; release on every close, the state stays', () => {
  const s = new CT.Mute.Session();
  const first = fakeOutput();
  s.attach(first);
  assert.deepEqual(first.applied, [false]);
  assert.equal(s.toggle(), true);
  assert.deepEqual(first.applied, [false, true]);
  s.detach();
  assert.equal(first.released, 1);
  assert.equal(s.muted, true);
  const next = fakeOutput();
  s.attach(next); // the next programme / channel
  assert.deepEqual(next.applied, [true]);
  assert.equal(s.toggle(), false);
  assert.deepEqual(next.applied, [true, false]);
});

test('session: an output that refuses keeps the old state', () => {
  const s = new CT.Mute.Session();
  s.attach({ apply: () => false, release() {} });
  assert.equal(s.toggle(), false);
  assert.equal(s.muted, false);
});

test('session without an output (the ?demo timeline): the state still toggles', () => {
  const s = new CT.Mute.Session();
  s.attach(null);
  assert.equal(s.toggle(), true);
  assert.equal(s.poll(), false);
});

// -- the TV's audio control -------------------------------------------------------------------------------------

test('TV audio: mutes the TV while the player is open and gives the previous state back', () => {
  const tv = fakeTvAudio(false);
  const s = new CT.Mute.Session();
  const out = new CT.Mute.TvAudio(tv.control);
  s.attach(out);
  assert.deepEqual(tv.calls, [], 'not muted: the TV is not touched');
  s.toggle();
  assert.equal(tv.muted, true);
  s.detach();
  assert.equal(tv.muted, false, 'the player closed: sound on the TV again');
  s.attach(out); // the next programme: muted again
  assert.equal(tv.muted, true);
  s.detach();
  assert.deepEqual(tv.calls, [true, false, true, false]);
});

test('TV audio: a TV muted before the app stays muted, and is not unmuted when the app is not muted', () => {
  const tv = fakeTvAudio(true);
  const s = new CT.Mute.Session();
  s.attach(new CT.Mute.TvAudio(tv.control));
  s.detach();
  assert.deepEqual(tv.calls, []);
  assert.equal(tv.muted, true);
  // muted in the app on a muted TV, unmuted in the app: the viewer asked for sound; closing gives the mute back
  const s2 = new CT.Mute.Session();
  s2.muted = true;
  s2.attach(new CT.Mute.TvAudio(tv.control));
  s2.toggle();
  assert.equal(tv.muted, false);
  s2.detach();
  assert.equal(tv.muted, true);
});

test('TV audio: the TV\'s own MUTE key while playing — the app follows it, closing restores the TV', () => {
  const tv = fakeTvAudio(false);
  const s = new CT.Mute.Session();
  s.attach(new CT.Mute.TvAudio(tv.control));
  assert.equal(s.poll(), false);
  tv.muted = true; // the remote's MUTE, handled by the TV itself
  assert.equal(s.poll(), true);
  assert.equal(s.muted, true);
  assert.equal(s.poll(), false, 'reported once');
  s.detach();
  assert.equal(tv.muted, false);
  assert.equal(s.muted, true, 'the next player starts muted');
});

test('TV audio: setMute refused (no privilege on this firmware) — the state does not change', () => {
  const tv = fakeTvAudio(false, { refuse: true });
  const s = new CT.Mute.Session();
  s.attach(new CT.Mute.TvAudio(tv.control));
  assert.equal(s.toggle(), false);
  s.detach();
  assert.equal(tv.muted, false);
});

test('a <video> is muted on the element itself', () => {
  const video = { muted: false };
  const s = new CT.Mute.Session();
  s.attach(new CT.Mute.ElementAudio(video));
  s.toggle();
  assert.equal(video.muted, true);
  s.detach();
  s.attach(new CT.Mute.ElementAudio(video));
  assert.equal(video.muted, true);
});

// -- texts and the timeline's choice ------------------------------------------------------------------------------

test('button texts', () => {
  assert.equal(CT.Mute.label(false), 'Vypnout zvuk');
  assert.equal(CT.Mute.label(true), 'Zapnout zvuk');
  assert.notEqual(CT.Mute.INDICATOR, CT.Mute.ICON_ON);
});

test('the "Zvuk" choice: several tracks + mute last; one track = the note + only the mute item, focused', () => {
  const tracks = [{ index: 1, language: 'cs' }, { index: 2, language: 'en' }];
  const c = CT.Mute.choice(tracks, 2, false);
  assert.equal(c.note, '');
  assert.deepEqual(c.items.map((i) => i.label), ['Čeština', 'Angličtina', 'Vypnout zvuk']);
  assert.deepEqual(c.items.map((i) => i.current), [false, true, false]);
  assert.equal(c.focus, 1);
  const one = CT.Mute.choice([{ index: 1, language: 'cs' }], 1, true);
  assert.equal(one.note, 'Pořad má jen jednu zvukovou stopu');
  assert.deepEqual(one.items.map((i) => [i.kind, i.label]), [['mute', 'Zapnout zvuk']]);
  assert.equal(one.focus, 0);
  assert.equal(CT.Mute.choice([], -1, false).note, CT.Mute.ONE_TRACK);
});

// -- keys ---------------------------------------------------------------------------------------------------------

test('keys: the yellow key is mute and is registered; the TV keeps its own MUTE (not registered, not mapped)', () => {
  CT.Platform.keyName = null;
  assert.equal(CT.Keys.name({ keyCode: 405 }), 'mute');
  assert.equal(CT.Keys.name({ keyCode: 449 }), null);
  assert.equal(CT.Keys.name({ keyCode: 404 }), 'audio');
  const registered = [];
  global.tizen = { tvinputdevice: { registerKey: (k) => registered.push(k) } };
  try {
    CT.Keys.register();
  } finally {
    delete global.tizen;
  }
  assert.ok(registered.includes('ColorF2Yellow'));
  assert.ok(!registered.includes('VolumeMute'));
});

// -- the player ---------------------------------------------------------------------------------------------------

/** webapis.avplay that "plays" at once. */
function fakeAvPlay() {
  let state = 'NONE';
  return {
    opened: [],
    open(url) { this.opened.push(url); state = 'IDLE'; },
    setDisplayRect() {}, setDisplayMethod() {}, setStreamingProperty() {}, setBufferingParam() {}, setListener() {},
    prepareAsync(ok) { state = 'READY'; ok(); },
    play() { state = 'PLAYING'; },
    pause() { state = 'PAUSED'; },
    stop() { state = 'IDLE'; },
    close() { state = 'NONE'; },
    getState: () => state,
    getCurrentTime: () => 0,
    getDuration: () => 0,
    seekTo() {}
  };
}

/** A Player on AVPlay and a fake TV; intervals of the constructor are not started. */
function samsungPlayer(tv) {
  global.document = fakeDocument();
  global.webapis = { avplay: fakeAvPlay() };
  global.tizen = { tvaudiocontrol: tv.control };
  const app = {
    planner: { plan: (r) => Promise.resolve({ kind: 'vod', url: 'https://cdn.example/' + r.title + '.m3u8', startPositionMs: null }) },
    onPlayerClosed() {},
    channelList: () => [],
    resumeStore: { save() {} }
  };
  const realInterval = global.setInterval;
  global.setInterval = () => 0;
  try {
    return new CT.Player(app);
  } finally {
    global.setInterval = realInterval;
  }
}

function programme(title) {
  return { kind: 'archive', title, channelId: 'CH_1', channelName: 'ČT1' };
}

const tick = () => new Promise((r) => setImmediate(r));

test('player: muted with the yellow key, the next programme opens muted; the TV gets its sound back on close', async (t) => {
  t.after(() => { delete global.webapis; delete global.tizen; });
  const tv = fakeTvAudio(false);
  const player = samsungPlayer(tv);
  const $ = (id) => global.document.getElementById(id);
  player.open(programme('a'));
  await tick();
  assert.equal($('mute-indicator').classList.contains('visible'), false);
  assert.equal($('osd-mute-label').textContent, 'Vypnout zvuk');
  player.onKey('mute');
  assert.equal(tv.muted, true);
  assert.equal($('mute-indicator').classList.contains('visible'), true);
  assert.equal($('mute-indicator').textContent, CT.Mute.INDICATOR);
  assert.equal($('osd-mute-label').textContent, 'Zapnout zvuk');
  player.close();
  assert.equal(tv.muted, false, 'closing the player gives the TV its sound back');
  player.open(programme('b'));
  await tick();
  assert.equal(global.webapis.avplay.opened.length, 2);
  assert.equal(tv.muted, true, 'the next programme is muted too');
  assert.equal($('mute-indicator').classList.contains('visible'), true);
  player.suspend(); // the app goes to the background: the TV is not left muted
  assert.equal(tv.muted, false);
  player.resume();
  assert.equal(tv.muted, true);
  player.onKey('mute');
  assert.equal(tv.muted, false);
  assert.equal($('mute-indicator').classList.contains('visible'), false);
  player.close();
  assert.deepEqual(tv.calls, [true, false, true, false, true, false]);
});

test('player: the TV\'s own MUTE while playing shows the corner icon (polled every second)', async (t) => {
  t.after(() => { delete global.webapis; delete global.tizen; });
  const tv = fakeTvAudio(false);
  const player = samsungPlayer(tv);
  player.open(programme('a'));
  await tick();
  tv.muted = true;
  player.pollMute();
  assert.equal(global.document.getElementById('mute-indicator').classList.contains('visible'), true);
  player.close();
  assert.equal(tv.muted, false);
});

// -- the timeline's "Zvuk" choice -------------------------------------------------------------------------------

function timeline(tracks, muted) {
  global.document = fakeDocument();
  const toggles = [];
  const player = {
    mute: { muted: !!muted },
    toggleMute() { toggles.push(1); this.mute.muted = !this.mute.muted; },
    backend: { audioTracks: () => tracks, currentAudio: () => (tracks[0] ? tracks[0].index : -1), selectAudio: () => true }
  };
  const realInterval = global.setInterval;
  global.setInterval = () => 0;
  let tl;
  try {
    tl = new CT.TimelineController(player);
  } finally {
    global.setInterval = realInterval;
  }
  return { tl, player, toggles, $: (id) => global.document.getElementById(id) };
}

test('timeline: one audio track opens the choice with the note and the mute item; OK mutes', () => {
  const { tl, toggles, $ } = timeline([{ index: 1, language: 'cs' }]);
  tl.openAudioList();
  assert.equal($('audio-list').classList.contains('visible'), true);
  assert.equal($('audio-list-note').textContent, 'Pořad má jen jednu zvukovou stopu');
  assert.deepEqual($('audio-list-items').children.map((c) => c.textContent), ['Vypnout zvuk']);
  tl.onAudioListKey('enter');
  assert.equal(toggles.length, 1);
  assert.equal(tl.audioList, null);
});

test('timeline: with several tracks ▼ past the tracks reaches the mute item; a track is not mute', () => {
  const { tl, toggles, $ } = timeline([{ index: 1, language: 'cs' }, { index: 2, language: 'en' }], true);
  tl.openAudioList();
  assert.equal($('audio-list-note').style.display, 'none');
  assert.deepEqual($('audio-list-items').children.map((c) => c.textContent), ['Čeština', 'Angličtina', 'Zapnout zvuk']);
  tl.onAudioListKey('down');
  tl.onAudioListKey('down');
  tl.onAudioListKey('down'); // stays on the last item
  assert.equal(tl.audioList.focus, 2);
  tl.onAudioListKey('enter');
  assert.equal(toggles.length, 1);
  tl.openAudioList();
  tl.onAudioListKey('enter'); // the current track
  assert.equal(toggles.length, 1);
});

test('timeline: the audio key opens the "Zvuk" choice, the yellow key mutes at once', () => {
  const { tl, toggles, $ } = timeline([{ index: 1, language: 'cs' }]);
  tl.tl = { pieces: [], programmes: [] };
  tl.showBar = () => {};
  tl.onKeyDown('audio');
  assert.equal($('audio-list').classList.contains('visible'), true);
  tl.closeAudioList();
  tl.onKeyDown('mute');
  assert.equal(toggles.length, 1);
});

test('config.xml asks for the TV audio privilege tvaudiocontrol.setMute needs', () => {
  const xml = require('fs').readFileSync(require('path').join(__dirname, '..', 'app', 'config.xml'), 'utf8');
  assert.match(xml, /<tizen:privilege name="http:\/\/tizen\.org\/privilege\/tv\.audio"\/>/);
});
