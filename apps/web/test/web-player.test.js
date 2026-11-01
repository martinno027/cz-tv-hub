'use strict';
// The web player backend (spec/web.md "Player") against a fake <video> and a fake hls.js: the API the shared
// Player/TimelineController use, the key header only for the gateway, the HEVC check and its text.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

const W = CT.WebPlayer;
const ORIGIN = 'http://192.168.1.20:8098';
const KEY = 'k'.repeat(43);
const HLS_TYPE = 'application/vnd.apple.mpegurl';

function fakeVideo(opts) {
  const o = opts || {};
  const listeners = {};
  const attrs = {};
  const v = {
    style: {}, paused: true, currentTime: 0, duration: NaN, played: 0, loads: 0,
    audioTracks: o.audioTracks,
    canPlayType: (type) => (o.canPlay || []).includes(type) ? 'maybe' : '',
    addEventListener: (name, f) => { (listeners[name] = listeners[name] || []).push(f); },
    fire: (name) => (listeners[name] || []).forEach((f) => f()),
    play: () => { v.paused = false; v.played++; return Promise.resolve(); },
    pause: () => { v.paused = true; },
    load: () => { v.loads++; },
    getAttribute: (k) => (k in attrs ? attrs[k] : null),
    removeAttribute: (k) => { delete attrs[k]; },
    onloadedmetadata: null
  };
  Object.defineProperty(v, 'src', { set: (s) => { attrs.src = s; }, get: () => attrs.src || '' });
  return v;
}

/** hls.js's surface the backend uses; `instances` keeps every one made. */
function fakeHls(supported) {
  const instances = [];
  function Hls(config) {
    this.config = config;
    this.handlers = {};
    this.audioTracks = [];
    this.audioTrack = -1;
    this.destroyed = false;
    instances.push(this);
  }
  Hls.isSupported = () => supported !== false;
  Hls.Events = { MANIFEST_PARSED: 'hlsManifestParsed', ERROR: 'hlsError' };
  Hls.prototype.on = function (name, f) { this.handlers[name] = f; };
  Hls.prototype.emit = function (name, data) { this.handlers[name](name, data); };
  Hls.prototype.loadSource = function (url) { this.url = url; };
  Hls.prototype.attachMedia = function (media) { this.media = media; };
  Hls.prototype.destroy = function () { this.destroyed = true; };
  Hls.instances = instances;
  return Hls;
}

function callbacks() {
  const log = [];
  return {
    log,
    cb: {
      onReady: () => log.push('ready'),
      onError: (w) => log.push('error:' + w),
      onEnded: () => log.push('ended'),
      onBuffering: (on) => log.push('buffering:' + on)
    }
  };
}

function backend(video, Hls, key) {
  const c = callbacks();
  const b = new W.WebBackend(c.cb, { video, Hls, key: () => (key === undefined ? KEY : key), origin: ORIGIN });
  return { b, log: c.log };
}

/** A fake XHR for xhrSetup: the headers set on it. */
function xhr() {
  const headers = {};
  return { headers, setRequestHeader: (k, v) => { headers[k] = v; } };
}

test('mode: Safari-like (native HLS with audioTracks) native; Chrome hls.js; native without track API only without MSE', () => {
  assert.equal(W.mode(fakeVideo({ canPlay: [HLS_TYPE], audioTracks: [] }), fakeHls()), 'native');
  assert.equal(W.mode(fakeVideo({}), fakeHls()), 'hls');
  assert.equal(W.mode(fakeVideo({ canPlay: [HLS_TYPE] }), fakeHls()), 'hls'); // native HLS, no track API (new Chrome)
  assert.equal(W.mode(fakeVideo({ canPlay: [HLS_TYPE] }), fakeHls(false)), 'native');
  assert.equal(W.mode(fakeVideo({ canPlay: [HLS_TYPE] }), null), 'native');
  assert.equal(W.mode(fakeVideo({}), fakeHls(false)), 'none');
});

test('HEVC check: MediaSource for hls.js, canPlayType for native; the Czech text', () => {
  const hevcMse = { MediaSource: { isTypeSupported: (t) => t === W.HEVC_TYPE } };
  const noHevc = { MediaSource: { isTypeSupported: () => false } };
  assert.equal(W.hevcSupported(fakeVideo({}), fakeHls(), hevcMse), true);
  assert.equal(W.hevcSupported(fakeVideo({}), fakeHls(), noHevc), false);
  assert.equal(W.hevcSupported(fakeVideo({}), fakeHls(), {}), false);
  assert.equal(W.hevcSupported(fakeVideo({}), fakeHls(), { ManagedMediaSource: hevcMse.MediaSource }), true);
  assert.equal(W.hevcSupported(fakeVideo({ canPlay: [HLS_TYPE, W.HEVC_TYPE], audioTracks: [] }), fakeHls(), noHevc), true);
  assert.equal(W.hevcSupported(fakeVideo({ canPlay: [HLS_TYPE], audioTracks: [] }), fakeHls(), hevcMse), false);
  assert.equal(W.hevcSupported(fakeVideo({}), fakeHls(false), hevcMse), false);
  assert.equal(W.HEVC_TYPE, 'video/mp4; codecs="hvc1.1.6.L120.90"');
  assert.equal(W.HEVC_MESSAGE, 'Tento prohlížeč neumí přehrát vysílání z antény (H.265). Zkuste Safari, nebo Chrome či ' +
    'Edge na počítači s podporou HEVC.');
});

test('hls.js: opens, starts on MANIFEST_PARSED (play + onReady), live starts at the edge', () => {
  const Hls = fakeHls();
  const v = fakeVideo({});
  const { b, log } = backend(v, Hls);
  b.open({ url: ORIGIN + '/dvb/live/ch1.m3u8', kind: 'live' }, 0);
  const h = Hls.instances[0];
  assert.equal(h.url, ORIGIN + '/dvb/live/ch1.m3u8');
  assert.equal(h.media, v);
  assert.equal(h.config.startPosition, -1);
  assert.equal(v.style.display, 'block');
  assert.deepEqual(log, []);
  h.emit(Hls.Events.MANIFEST_PARSED, {});
  h.emit(Hls.Events.MANIFEST_PARSED, {}); // once only
  assert.deepEqual(log, ['ready']);
  assert.equal(v.played, 1);
});

test('hls.js: a timeline and "Od začátku" always seek, also to 0; a resume position in seconds', () => {
  const Hls = fakeHls();
  const { b } = backend(fakeVideo({}), Hls);
  b.open({ url: ORIGIN + '/dvb/timeline/x.m3u8', kind: 'timeline', alwaysSeek: true }, 0);
  assert.equal(Hls.instances[0].config.startPosition, 0);
  b.open({ url: 'https://cdn.example.invalid/vod.m3u8', kind: 'vod' }, 754000);
  assert.equal(Hls.instances[1].config.startPosition, 754);
  assert.equal(Hls.instances[0].destroyed, true);
});

test('hls.js: the key header only for the gateway\'s /dvb/ URLs, never for CT\'s CDN or another path', () => {
  const Hls = fakeHls();
  const { b } = backend(fakeVideo({}), Hls);
  b.open({ url: ORIGIN + '/dvb/live/ch1.m3u8', kind: 'live' }, 0);
  const setup = Hls.instances[0].config.xhrSetup;
  const cases = [
    [ORIGIN + '/dvb/live/ch1.m3u8', true],
    [ORIGIN + '/dvb/hls/abc/seg-1.m4s', true],
    ['http://192.168.1.20:8098/dvb/timeline/x/a.m3u8?x=1', true],
    ['https://cdn.example.invalid/dvb/live/x.m3u8', false],
    ['http://192.168.1.20:8099/dvb/live/x.m3u8', false],
    ['http://192.168.1.21:8098/dvb/live/x.m3u8', false],
    [ORIGIN + '/web/app/index.html', false],
    [ORIGIN + '/dvbx/a', false],
    ['https://ctcdn.example.invalid/hls/master.m3u8', false]
  ];
  for (const [url, withKey] of cases) {
    const x = xhr();
    setup(x, url);
    assert.equal(x.headers.Authorization, withKey ? 'Bearer ' + KEY : undefined, url);
  }
  const none = backend(fakeVideo({}), fakeHls(), null);
  none.b.open({ url: ORIGIN + '/dvb/live/ch1.m3u8', kind: 'live' }, 0);
  const x = xhr();
  none.b.hls.config.xhrSetup(x, ORIGIN + '/dvb/live/ch1.m3u8');
  assert.deepEqual(x.headers, {});
  assert.equal(W.sameOrigin('http://h:80', 'http://h'), true);
  assert.equal(W.sameOrigin('https://h', 'http://h'), false);
});

test('hls.js: fatal errors go to onError (the Player re-resolves), others and a closed session do not', () => {
  const Hls = fakeHls();
  const { b, log } = backend(fakeVideo({}), Hls);
  b.open({ url: ORIGIN + '/dvb/live/ch1.m3u8', kind: 'live' }, 0);
  const h = Hls.instances[0];
  h.emit(Hls.Events.ERROR, { fatal: false, type: 'networkError', details: 'fragLoadError' });
  assert.deepEqual(log, []);
  h.emit(Hls.Events.ERROR, { fatal: true, type: 'networkError', details: 'manifestLoadError' });
  assert.deepEqual(log, ['error:networkError: manifestLoadError']);
  b.close();
  h.emit(Hls.Events.ERROR, { fatal: true, type: 'mediaError', details: 'x' });
  h.emit(Hls.Events.MANIFEST_PARSED, {});
  assert.deepEqual(log, ['error:networkError: manifestLoadError']);
  assert.equal(h.destroyed, true);
});

test('video events: buffering, end; ignored once closed', () => {
  const v = fakeVideo({});
  const { b, log } = backend(v, fakeHls());
  b.open({ url: ORIGIN + '/dvb/recording/r.m3u8', kind: 'vod' }, 0);
  v.fire('waiting');
  v.fire('playing');
  v.fire('ended');
  b.close();
  v.fire('ended');
  v.fire('waiting');
  assert.deepEqual(log, ['buffering:true', 'buffering:false', 'ended']);
  assert.equal(v.style.display, 'none');
});

test('native (Safari): src set, seek on loadedmetadata, then play + onReady; errors of a playing source only', () => {
  const v = fakeVideo({ canPlay: [HLS_TYPE], audioTracks: [] });
  const Hls = fakeHls();
  const { b, log } = backend(v, Hls);
  b.open({ url: ORIGIN + '/dvb/recording/r.m3u8', kind: 'vod', alwaysSeek: true }, 0);
  assert.equal(Hls.instances.length, 0);
  assert.equal(v.src, ORIGIN + '/dvb/recording/r.m3u8');
  v.currentTime = 99;
  v.onloadedmetadata();
  assert.equal(v.currentTime, 0);
  assert.deepEqual(log, ['ready']);
  v.fire('error');
  assert.deepEqual(log, ['ready', 'error:video error']);
  b.close();
  assert.equal(v.src, '');
  v.fire('error'); // clearing the source is not an error
  assert.equal(log.length, 2);
  b.open({ url: 'https://cdn.example.invalid/live.m3u8', kind: 'live' }, 0);
  v.currentTime = 5;
  v.onloadedmetadata();
  assert.equal(v.currentTime, 5); // live: no seek
});

test('no HLS at all: onError at once', () => {
  const { b, log } = backend(fakeVideo({}), fakeHls(false));
  b.open({ url: 'x.m3u8', kind: 'live' }, 0);
  assert.deepEqual(log, ['error:no HLS in this browser']);
});

test('position, duration, pause; seek keeps 10 s before a growing end (as AVPlay)', () => {
  const v = fakeVideo({});
  const { b } = backend(v, fakeHls());
  b.open({ url: 'x.m3u8', kind: 'timeline' }, 0);
  assert.equal(b.paused(), false); // loading: not "Pozastaveno" (AVPlay is not PAUSED yet either)
  b.hls.emit('hlsManifestParsed', {});
  v.paused = true;
  assert.equal(b.duration(), 0); // NaN
  v.duration = Infinity;
  assert.equal(b.duration(), 0);
  v.duration = 600;
  v.currentTime = 12.5;
  assert.equal(b.position(), 12500);
  assert.equal(b.duration(), 600000);
  b.seekTo(10 * 60 * 1000);
  assert.equal(v.currentTime, 590);
  b.seekTo(-5000);
  assert.equal(v.currentTime, 0);
  v.currentTime = 100;
  b.seekBy(-60000);
  assert.equal(v.currentTime, 40);
  assert.equal(b.paused(), true);
  b.togglePause();
  assert.equal(b.paused(), false);
  b.togglePause();
  assert.equal(b.paused(), true);
  b.close();
  assert.equal(b.paused(), false); // closed after an error: nothing is paused
});

test('audio tracks: hls.js renditions (LANGUAGE) and the browser\'s own audioTracks; labels via the shared rule', () => {
  const Hls = fakeHls();
  const { b } = backend(fakeVideo({}), Hls);
  b.open({ url: ORIGIN + '/dvb/live/ch1.m3u8', kind: 'live' }, 0);
  const h = Hls.instances[0];
  h.audioTracks = [{ lang: 'ces', name: 'a' }, { lang: 'eng', name: 'b' }, { name: 'c' }];
  h.audioTrack = 0;
  const tracks = b.audioTracks();
  assert.deepEqual(tracks, [{ index: 0, language: 'ces' }, { index: 1, language: 'eng' }, { index: 2, language: '' }]);
  assert.equal(b.currentAudio(), 0);
  const next = CT.AudioTracks.next(tracks, b.currentAudio());
  assert.equal(b.selectAudio(tracks[next].index), true);
  assert.equal(h.audioTrack, 1);
  assert.equal(CT.AudioTracks.osd(tracks, next), 'Zvuk: Angličtina (2/3)');
  assert.equal(b.selectAudio(7), false);

  const list = [{ language: 'cs', enabled: true }, { language: 'de', enabled: false }];
  const native = backend(fakeVideo({ canPlay: [HLS_TYPE], audioTracks: list }), fakeHls());
  native.b.open({ url: ORIGIN + '/dvb/live/ch1.m3u8', kind: 'live' }, 0);
  assert.deepEqual(native.b.audioTracks(), [{ index: 0, language: 'cs' }, { index: 1, language: 'de' }]);
  assert.equal(native.b.currentAudio(), 0);
  assert.equal(native.b.selectAudio(1), true);
  assert.deepEqual(list.map((t) => t.enabled), [false, true]);
});

test('hls.js: never X-Cztvhub-Client (T37) — not to the gateway\'s media, not to a CDN (no custom header, no CORS preflight)', () => {
  const Hls = fakeHls();
  const { b } = backend(fakeVideo({}), Hls);
  b.open({ url: 'https://ctcdn.example.invalid/hls/master.m3u8', kind: 'vod' }, 0);
  const setup = Hls.instances[0].config.xhrSetup;
  const gateway = xhr();
  setup(gateway, ORIGIN + '/dvb/live/ch1.m3u8');
  assert.deepEqual(Object.keys(gateway.headers), ['Authorization']);
  for (const url of ['https://ctcdn.example.invalid/hls/master.m3u8', 'https://psp.example.invalid/live/index.m3u8']) {
    const x = xhr();
    setup(x, url);
    assert.deepEqual(x.headers, {}, url); // a CDN request stays a "simple" one
  }
});
