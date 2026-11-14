'use strict';
// spec/apps.md "ČT player bar (T62)" in the real Player on a fake AVPlay: ČT start-over and the archive get the bar's
// controller and keys (▲/▼ = programmes, no zapping, no ▲ audio), ČT and Anténa live, DVR recordings and the Chamber
// keep the plain OSD and its keys; re-resolving after an error continues at the bar's playhead; AVPlay subtitles.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');
const { fakeDocument, withClock, flush } = require('./fakes');

global.window = global;
global.document = fakeDocument();
global.addEventListener = () => {};
require('../app/js/ui/platform.js');
require('../app/js/ui/keys.js');
require('../app/js/ui/zap.js');
require('../app/js/ui/timeline.js');
require('../app/js/ui/ct-bar.js');
require('../app/js/ui/player.js');
CT.Demo = { enabled: false };
CT.LIVE_PREFIX = { live: 'ct:', dvbLive: 'dvb:', pspLive: 'psp:' }; // app.js's

const NOW = Date.parse('2026-11-14T17:00:00Z');
const W = 3 * 3600;
const DVB = { config: { cookie: () => 'cztvhub_key=x' }, breaks: () => Promise.resolve([]) };
const $ = (id) => global.document.getElementById(id);

function fakeAvPlay() {
  const p = {
    opened: [], seeks: [], state: 'NONE', time: 0, duration: W * 1000, listener: null, calls: [],
    tracks: [{ type: 'AUDIO', index: 1, extra_info: '{"language":"cs"}' }],
    open(url) { p.opened.push(url); p.state = 'IDLE'; },
    setDisplayRect() {}, setDisplayMethod() {}, setStreamingProperty() {}, setBufferingParam() {},
    setListener(l) { p.listener = l; },
    prepareAsync(ok) { p.state = 'READY'; ok(); },
    play() { p.state = 'PLAYING'; }, pause() { p.state = 'PAUSED'; }, stop() { p.state = 'IDLE'; },
    close() { p.state = 'NONE'; },
    getState: () => p.state, getCurrentTime: () => p.time, getDuration: () => p.duration,
    seekTo(ms, ok) { p.seeks.push(ms); p.time = ms; if (ok) ok(); },
    getTotalTrackInfo: () => p.tracks,
    getCurrentStreamInfo: () => [{ type: 'AUDIO', index: 1 }],
    setSelectTrack(type, i) { p.calls.push(['select', type, i]); },
    setSilentSubtitle(on) { p.calls.push(['silent', on]); }
  };
  return p;
}

const GUIDE = [
  { idec: 'B1', title: 'Události', start: Date.parse('2026-11-14T15:00:00Z'), end: Date.parse('2026-11-14T16:30:00Z') },
  { idec: 'C1', title: 'Kosmo', start: Date.parse('2026-11-14T16:30:00Z'), end: Date.parse('2026-11-14T17:30:00Z') }
];

function targetFor(request) {
  switch (request.kind) {
    case 'live': return { kind: 'live', url: 'https://cdn.example/live-' + request.channelId + '.m3u8', startPositionMs: null, dvb: null };
    case 'startover': return { kind: 'timeshift', url: 'https://cdn.example/ts.m3u8', startPositionMs: null, dvb: null,
      channelId: request.channelId, fallbackOffsetMs: 3570 * 1000 };
    case 'pspLive': return { kind: 'live', url: 'https://psp.example/1.m3u8', startPositionMs: null, psp: true, dvb: null };
    case 'dvbRecorded': return { kind: 'vod', url: 'http://gw/dvb/recording/r.m3u8', startPositionMs: null, dvb: DVB, recordingId: 'dvr7' };
    default: return { kind: 'vod', url: 'https://cdn.example/vod.m3u8', startPositionMs: null, dvb: null };
  }
}

/** A Player with the app around it it needs; `plans` = every [request kind, channel, forceRefresh]. */
function samsungPlayer() {
  global.document = fakeDocument();
  global.webapis = { avplay: fakeAvPlay() };
  const plans = [];
  const app = {
    planner: { plan: (r, force) => { plans.push([r.kind, r.channelId, !!force]); return Promise.resolve(targetFor(r)); } },
    schedules: { day: () => Promise.resolve({ CH_1: GUIDE }) },
    zapItems: [{ key: 'ct:CH_1', source: 'ct', channel: { id: 'CH_1', name: 'ČT1' } },
      { key: 'ct:CH_2', source: 'ct', channel: { id: 'CH_2', name: 'ČT2' } }],
    channelList: () => app.zapItems, dvbChannels: [], ctState: null, dvbState: null, resumeStore: { save() {} },
    liveRequest: (item) => ({ kind: 'live', channelId: item.channel.id, title: item.channel.name }),
    airingNow: () => null, pspState: () => 'live', onPlayerClosed: () => {}
  };
  const player = new CT.Player(app);
  return { player, app, plans, av: global.webapis.avplay };
}

test('barMode: ČT start-over and archive get the bar; live, DVR recordings and the Chamber do not', () => {
  const B = CT.Timeline.barMode;
  assert.equal(B({ kind: 'timeline', dvb: DVB }), 'timeline');
  assert.equal(B({ kind: 'timeshift', dvb: null }), 'timeshift');
  assert.equal(B({ kind: 'vod', dvb: null }), 'vod');
  assert.equal(B({ kind: 'vod', dvb: DVB }), null, 'a TVHeadend DVR recording keeps the plain OSD');
  assert.equal(B({ kind: 'live', dvb: null }), null);
  assert.equal(B({ kind: 'live', dvb: DVB }), null);
  assert.equal(B({ kind: 'live', psp: true }), null);
  assert.equal(B(null), null);
});

test('ČT start-over: the bar; ▲ jumps to the programme start (not the audio track), no zapping, no digits', async () => {
  await withClock(NOW, async (clock) => {
    const { player, plans, av } = samsungPlayer();
    player.open({ kind: 'startover', channelId: 'CH_1', title: 'Události', start: GUIDE[0].start });
    await flush();
    assert.ok(player.tl, 'the bar controller');
    assert.equal(player.tl.source.mode, 'timeshift');
    assert.equal($('osd').classList.contains('timeline'), true);
    assert.match($('osd-hint').textContent, /podržet = převíjení/);
    assert.deepEqual(av.seeks, [3570 * 1000], 'opened at the start-over offset');
    av.time = (W - 1800) * 1000; // 16:30 in the buffer = Kosmo's start; 10 min later
    av.time += 600 * 1000;
    ['up'].forEach((k) => { player.onKey(k); player.onKeyUp(k); }); // the bar was up from the start: ▲ acts
    clock.advance(100);
    assert.equal(av.seeks[av.seeks.length - 1], (W - 1800) * 1000, '▲ = start of Kosmo');
    assert.notEqual($('osd-state').textContent, 'Jen jedna zvuková stopa', 'not the audio switch');
    ['chup', 'down', '2'].forEach((k) => { player.onKey(k); player.onKeyUp(k); });
    clock.advance(4000);
    assert.equal(plans.length, 1, 'no zapping: nothing else planned');
    assert.equal(player.request.kind, 'startover');
    player.close();
  });
});

test('ČT archive (VOD): the bar too; ▶ held fast-forwards instead of the old 10 s steps', async () => {
  await withClock(NOW, async (clock) => {
    const { player, av } = samsungPlayer();
    av.duration = 3600 * 1000;
    player.open({ kind: 'archive', idec: '42', title: 'Kosmo' });
    await flush();
    assert.equal(player.tl.source.mode, 'vod');
    av.time = 600 * 1000;
    const before = av.seeks.length;
    player.onKey('right');
    for (let i = 0; i < 10; i++) { clock.advance(100); player.onKey('right'); } // held 1 s, repeating
    player.onKeyUp('right');
    assert.equal(av.seeks.length, before, 'no seek while moving');
    assert.ok(player.tl.trick, 'moving');
    player.onKey('enter');
    assert.equal(av.seeks.length, before + 1, 'one exact seek on the stop');
    assert.ok(av.seeks[before] > 600 * 1000 + 20 * 1000, 'forward, further than a click');
    player.close();
  });
});

test('ČT live, Anténa DVR recording and the Chamber keep the plain OSD and its keys', async () => {
  await withClock(NOW, async (clock) => {
    const { player, plans } = samsungPlayer();
    player.open({ kind: 'live', channelId: 'CH_1', title: 'ČT1' });
    await flush();
    assert.equal(player.tl, null);
    player.onKey('down'); // zaps
    clock.advance(CT.ChannelNumbers.ZAP_DELAY_MS + 50);
    await flush();
    assert.deepEqual(plans.map((p) => p[1]), ['CH_1', 'CH_2']);
    player.close();
  });
  await withClock(NOW, async () => {
    const { player, av } = samsungPlayer();
    player.open({ kind: 'dvbRecorded', channelId: 'nova1', title: 'Film', recordingId: 'dvr7', fromStart: false });
    await flush();
    assert.equal(player.tl, null);
    av.time = 100 * 1000;
    player.onKey('left');
    assert.equal(av.seeks[av.seeks.length - 1], 90 * 1000, '◀ = 10 s back as before');
    player.onKey('up');
    assert.equal($('osd-state').textContent, 'Jen jedna zvuková stopa', '▲ = the audio track as before');
    player.close();
  });
  await withClock(NOW, async () => {
    const { player } = samsungPlayer();
    player.open({ kind: 'pspLive', channelId: 'stream1', title: 'Sněmovna' });
    await flush();
    assert.equal(player.tl, null);
    player.close();
  });
});

test('re-resolving a start-over after an error: at the bar\'s playhead, a movement in progress ends there', async () => {
  await withClock(NOW, async (clock) => {
    const { player, plans, av } = samsungPlayer();
    player.open({ kind: 'startover', channelId: 'CH_1', title: 'Události', start: GUIDE[0].start });
    await flush();
    av.time = 5000 * 1000;
    player.onKey('rewind'); // moving back at speed 1
    clock.advance(1000);
    const here = player.tl.playheadPosition();
    assert.ok(here < 5000 - 50, 'moved back about a minute');
    player.onError('expired');
    assert.equal(player.tl.trick, null, 'the movement ended');
    await flush();
    assert.deepEqual(plans[1], ['startover', 'CH_1', true]);
    const elapsed = (clock.now() - NOW) / 1000; // the new buffer starts this much later
    const resumed = av.seeks[av.seeks.length - 1] / 1000;
    assert.ok(Math.abs(resumed - (here - elapsed)) < 0.5, resumed + ' vs ' + (here - elapsed));
    player.close();
  });
});

test('AVPlay subtitles: TEXT tracks listed, start off, chosen = setSelectTrack + not silent; the app draws the text', async () => {
  await withClock(NOW, async (clock) => {
    const { player, av } = samsungPlayer();
    av.tracks.push({ type: 'TEXT', index: 3, extra_info: '{"track_lang":"cs"}' });
    av.duration = 3600 * 1000;
    player.open({ kind: 'archive', idec: '42', title: 'Kosmo' });
    await flush();
    assert.deepEqual(av.calls, [['silent', true]], 'off from the start');
    const b = player.backend;
    assert.deepEqual(b.subtitleTracks(), [{ index: 3, language: 'cs', name: '' }]);
    assert.equal(b.currentSubtitle(), -1);
    av.listener.onsubtitlechange(2000, 'not chosen');
    assert.equal($('subtitles').textContent, '', 'nothing drawn while off');
    player.tl.openAudioList();
    assert.deepEqual($('audio-list-items').children.map((c) => c.textContent), ['Titulky: Vypnuto', 'Titulky: Čeština', 'Vypnout zvuk']);
    assert.equal(b.selectSubtitle(3), true);
    assert.deepEqual(av.calls.slice(1), [['select', 'TEXT', 3], ['silent', false]]);
    av.listener.onsubtitlechange(2000, '<i>Dobrý</i> den');
    assert.equal($('subtitles').textContent, 'Dobrý den', 'markup stripped');
    clock.advance(2100);
    assert.equal($('subtitles').textContent, '', 'gone after its duration');
    assert.equal(b.selectSubtitle(-1), true);
    assert.deepEqual(av.calls[av.calls.length - 1], ['silent', true]);
    player.close();
  });
});
