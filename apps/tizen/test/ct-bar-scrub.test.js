'use strict';
// spec/apps.md "ČT player bar (T62)": ČT start-over (timeshift) and the archive (VOD) play with the Anténa timeline's
// bar and keys over one window of their own. The real TimelineController with the real CtBarSource on a fake clock,
// fake animation frames and a fake backend: the buffer sliding with now, the clamps (also while moving, the lower one
// sliding), ▲/▼ from the ČT guide, the labels, "Pokračovat", subtitles in the long-OK list.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');
const { fakeDocument, withClock, fakeBackend, flush } = require('./fakes');

global.window = global;
global.document = fakeDocument();
require('../app/js/ui/timeline.js');
require('../app/js/ui/ct-bar.js');

const NOW = Date.parse('2026-11-14T17:00:00Z'); // 18:00 in Prague
const W = 3 * 3600;
const at = (iso) => Date.parse(iso);
const GUIDE = [ // ČT1's guide around the buffer (14:00–17:00 UTC)
  { idec: 'A1', title: 'Ráno', episodeTitle: '', start: at('2026-11-14T13:00:00Z'), end: at('2026-11-14T15:00:00Z'), imageUrl: '' },
  { idec: 'B1', title: 'Události', episodeTitle: 'Díl 5', start: at('2026-11-14T15:00:00Z'), end: at('2026-11-14T16:30:00Z'), imageUrl: 'https://img.example/b.jpg' },
  { idec: 'C1', title: 'Kosmo', episodeTitle: '', start: at('2026-11-14T16:30:00Z'), end: at('2026-11-14T17:30:00Z'), imageUrl: '' },
  { idec: 'D1', title: 'Večerníček', episodeTitle: '', start: at('2026-11-14T17:30:00Z'), end: at('2026-11-14T17:40:00Z'), imageUrl: '' }
];
const $ = (id) => global.document.getElementById(id);
const near = (a, b, what, eps) => assert.ok(Math.abs(a - b) < (eps || 1e-6), (what || '') + ': ' + a + ' ≠ ' + b);

/** The bar over a ČT target: `mode` 'timeshift' | 'vod'; opts.position (s), request, duration, guide, subtitles. */
async function ctBar(clock, mode, opts) {
  global.document = fakeDocument();
  const backend = fakeBackend(clock, opts);
  const saved = [];
  const days = [];
  const request = opts.request || {
    kind: 'startover', channelId: 'CH_1', title: 'Události', start: GUIDE[1].start,
    resumeKey: 'ct:B1', lengthSeconds: 5400, resumeMeta: { title: 'Události (from the guide)' }
  };
  const target = mode === 'timeshift' ? { kind: 'timeshift', channelId: 'CH_1', dvb: null } : { kind: 'vod', dvb: null };
  const player = {
    backend, request, target, plannedAt: clock.now(), skipTo: null, osdTimer: null, mute: { muted: false },
    loadToken: 1, closed: 0,
    setSkip() {}, close() { player.closed++; }, channelName: () => 'ČT1 (player)', isOpen: () => true,
    app: { schedules: { day: (day) => { days.push(day); return opts.guideFails ? Promise.reject(new Error('x')) : Promise.resolve({ CH_1: opts.guide || GUIDE }); } } }
  };
  const source = new CT.CtBarSource(player, mode);
  const ctl = new CT.TimelineController(player, source);
  player.tl = ctl;
  ctl.setWindow(source.window(target));
  await flush();
  ctl.onKeyDown('enter'); // shows the bar; its keyup tells the controller this remote sends keyups
  ctl.onKeyUp('enter');
  return { ctl, backend, player, saved, days, store: { save: (key, progress, length, meta) => saved.push({ key, progress, length, meta }) } };
}

function press(clock, ctl, key, ms) {
  ctl.onKeyDown(key);
  clock.advance(ms);
  ctl.onKeyUp(key);
}
const click = (clock, ctl, key) => press(clock, ctl, key, 100);
/** A long OK: the remote repeats the keydown every 100 ms for 0.9 s, then the keyup. */
function longOk(clock, ctl) {
  ctl.onKeyDown('enter');
  for (let i = 0; i < 9; i++) {
    clock.advance(100);
    ctl.onKeyDown('enter');
  }
  ctl.onKeyUp('enter');
}
/** Seconds of buffer position of a wall clock, for a buffer planned at NOW. */
const pos = (time) => (time - (NOW - W * 1000)) / 1000;
const badge = () => ($('tl-trick').style.display === 'none' ? '' : $('tl-trick').textContent);

test('timeshift: the window is now − 3 h … now and slides with now; ticks and title from the ČT guide; "Teď"', async () => {
  await withClock(NOW, async (clock) => {
    const { ctl, days } = await ctBar(clock, 'timeshift', { position: pos(at('2026-11-14T15:01:00Z')) });
    assert.deepEqual(days, ['2026-11-14'], 'the guide of the broadcast day(s) in the buffer');
    assert.equal(ctl.tl.programmes.length, 4);
    assert.equal($('osd-title').textContent, 'Události');
    assert.equal($('osd-subtitle').textContent, 'ČT1 · Díl 5', 'the ČT channel of the target, not the player\'s name');
    assert.equal($('tl-clock').textContent, 'Dnes 16:01:00');
    assert.equal($('tl-start').textContent, '15:00');
    assert.equal($('tl-end').textContent, 'Teď');
    // A starts before the buffer: no tick; B and C start inside it
    assert.equal($('tl-ticks').children.length, 2);
    clock.advance(10 * 60 * 1000);
    ctl.showBar();
    assert.equal($('tl-start').textContent, '15:10', 'the start slid with now');
    assert.equal($('tl-end').textContent, 'Teď');
    assert.equal($('tl-clock').textContent, 'Dnes 16:01:00', 'the playhead keeps its wall clock');
    const span = CT.Timeline.span(ctl.tl.pieces);
    assert.deepEqual([span.start, span.end], [clock.now() - W * 1000, clock.now()]);
  });
});

test('timeshift: a buffer across the broadcast-day boundary asks the guide of both days', async () => {
  const early = Date.parse('2026-11-14T05:30:00Z'); // 6:30 in Prague: the buffer reaches into the day before
  await withClock(early, async (clock) => {
    const { days } = await ctBar(clock, 'timeshift', { position: 600, guide: [] });
    assert.deepEqual(days, ['2026-11-13', '2026-11-14']);
  });
});

test('timeshift: without the guide the title is what was opened', async () => {
  await withClock(NOW, async (clock) => {
    await ctBar(clock, 'timeshift', { position: 3660, guideFails: true });
    assert.equal($('osd-title').textContent, 'Události');
    assert.equal($('osd-subtitle').textContent, 'ČT1');
  });
});

test('timeshift: a click stays 10 s before the live edge', async () => {
  await withClock(NOW, async (clock) => {
    const { ctl, backend } = await ctBar(clock, 'timeshift', { position: W - 15 });
    click(clock, ctl, 'right');
    assert.equal(backend.seeks.length, 1);
    near(backend.seeks[0], pos(clock.now() - 10 * 1000), 'now − 10 s');
    assert.ok(backend.seeks[0] < W - 15 + 10, 'clamped, not +10 s');
  });
});

test('timeshift: rewinding reaches the lower clamp, which slides with now while moving; it stops there and plays', async () => {
  await withClock(NOW, async (clock) => {
    const { ctl, backend } = await ctBar(clock, 'timeshift', { position: 120 });
    ctl.onKeyDown('rewind'); // speed 1 at once: 60 s of buffer per second, the clamp comes 1 s per second closer
    let stopAt = null;
    clock.advance(3000, (now) => { if (stopAt === null && !ctl.trick) stopAt = now; });
    ctl.onKeyUp('rewind');
    assert.ok(stopAt !== null, 'stopped');
    assert.equal(backend.seeks.length, 1);
    const lo = pos(stopAt - W * 1000 + 60 * 1000);
    near(backend.seeks[0], lo, 'the clamp at the moment it stopped', 0.05);
    assert.ok(backend.seeks[0] > 60.5, 'slid: later than the clamp when it started (60 s)');
    assert.equal(backend.playing, true, 'plays');
    assert.equal(clock.pendingFrames(), 0);
  });
});

test('timeshift: fast-forward at 16 stops 10 s before now', async () => {
  await withClock(NOW, async (clock) => {
    const { ctl, backend } = await ctBar(clock, 'timeshift', { position: 3600 });
    for (let i = 0; i < 6; i++) click(clock, ctl, 'forward');
    assert.equal(badge(), '▶▶ 16');
    let stopAt = null;
    clock.advance(10000, (now) => { if (stopAt === null && !ctl.trick) stopAt = now; });
    assert.ok(stopAt !== null);
    assert.equal(backend.seeks.length, 1);
    near(backend.seeks[0], pos(stopAt - 10 * 1000), 'now − 10 s', 0.05);
    assert.equal(backend.playing, true);
  });
});

test('timeshift: the clock label shows the clamped point while moving past it', async () => {
  await withClock(NOW, async (clock) => {
    const { ctl } = await ctBar(clock, 'timeshift', { position: 75 });
    ctl.onKeyDown('rewind');
    clock.advance(16);
    const shown = ctl.playheadTime();
    assert.ok(shown >= clock.now() - W * 1000 + 60 * 1000, 'never before the clamp');
  });
});

test('timeshift: ▲ / ▼ from the ČT guide; a start before the buffer lands on the clamp', async () => {
  await withClock(NOW, async (clock) => {
    const { ctl, backend } = await ctBar(clock, 'timeshift', { position: pos(at('2026-11-14T16:40:00Z')) });
    click(clock, ctl, 'up');
    near(backend.seeks[0], pos(GUIDE[2].start), 'start of Kosmo');
    click(clock, ctl, 'up');
    near(backend.seeks[1], pos(GUIDE[1].start), 'start of Události (the one before)');
    click(clock, ctl, 'down');
    near(backend.seeks[2], pos(GUIDE[2].start), 'the next one');
    click(clock, ctl, 'up');
    click(clock, ctl, 'up');
    click(clock, ctl, 'up'); // Ráno began before the buffer
    near(backend.seeks[backend.seeks.length - 1], pos(clock.now() - W * 1000 + 60 * 1000), 'the lower clamp', 0.2);
  });
});

test('timeshift: no zapping and no digits; Back closes as before', async () => {
  await withClock(NOW, async (clock) => {
    const { ctl, backend, player } = await ctBar(clock, 'timeshift', { position: 3600 });
    ['chup', 'chdown', '1', '5'].forEach((k) => click(clock, ctl, k));
    assert.deepEqual(backend.seeks, []);
    ctl.onKeyDown('back');
    assert.equal(player.closed, 1);
  });
});

test('"Pokračovat" (timeshift): the programme at the playhead under ct:<idec>, from its start; the opened one keeps its row', async () => {
  await withClock(NOW, async (clock) => {
    const { ctl, store, saved, player } = await ctBar(clock, 'timeshift', { position: pos(at('2026-11-14T15:01:00Z')) });
    ctl.saveProgress(store, player.request);
    assert.deepEqual(saved[0], { key: 'ct:B1', progress: 60, length: 5400, meta: { title: 'Události (from the guide)' } });
    player.backend.pos = pos(at('2026-11-14T16:40:00Z')) * 1000;
    ctl.saveProgress(store, player.request);
    assert.equal(saved[1].key, 'ct:C1');
    assert.equal(saved[1].progress, 600);
    assert.deepEqual(saved[1].meta, CT.ResumeRules.meta('Kosmo', '', '', 'ct', GUIDE[2].start, 'CH_1'));
  });
  await withClock(NOW, async (clock) => { // the guide failed: the request's own programme, as before T62
    const { ctl, store, saved, player } = await ctBar(clock, 'timeshift', { position: pos(at('2026-11-14T15:02:00Z')), guideFails: true });
    ctl.saveProgress(store, player.request);
    assert.deepEqual(saved.map((s) => [s.key, s.progress]), [['ct:B1', 120]]);
  });
});

const VOD = {
  kind: 'programme', channelId: 'CH_2', title: 'Film', episodeTitle: '', idec: 'V1', start: at('2026-11-10T18:55:00Z'),
  end: at('2026-11-10T19:55:00Z'), resumeKey: 'ct:V1', lengthSeconds: 3600, resumeMeta: { title: 'Film' }
};

test('VOD with a known start: wall-clock labels of the broadcast, complete window, ▲ = its start, ▼ nothing', async () => {
  await withClock(NOW, async (clock) => {
    const { ctl, backend } = await ctBar(clock, 'vod', { position: 754, duration: 3600, request: VOD });
    assert.equal(ctl.tl.complete, true);
    assert.equal($('tl-clock').textContent, 'út 10. 11. 20:07:34');
    assert.deepEqual([$('tl-start').textContent, $('tl-end').textContent], ['19:55', '20:55']);
    assert.equal($('osd-title').textContent, 'Film');
    assert.equal($('tl-ticks').children.length, 0, 'no ticks');
    click(clock, ctl, 'down');
    assert.deepEqual(backend.seeks, [], '▼: nothing after it');
    click(clock, ctl, 'up');
    assert.deepEqual(backend.seeks, [0]);
  });
});

test('VOD: rewinding stops at 0, fast-forward 10 s before the end, a click too', async () => {
  await withClock(NOW, async (clock) => {
    const { ctl, backend } = await ctBar(clock, 'vod', { position: 30, duration: 3600, request: VOD });
    ctl.onKeyDown('rewind');
    clock.advance(1000);
    ctl.onKeyUp('rewind');
    assert.deepEqual(backend.seeks, [0]);
    assert.equal(backend.playing, true);
  });
  await withClock(NOW, async (clock) => {
    const { ctl, backend } = await ctBar(clock, 'vod', { position: 3570, duration: 3600, request: VOD });
    ctl.onKeyDown('forward');
    clock.advance(1000);
    assert.deepEqual(backend.seeks, [3590]);
    click(clock, ctl, 'right');
    assert.deepEqual(backend.seeks, [3590, 3590]);
  });
});

test('VOD without a known start: the elapsed position and 0:00 … the duration, which comes from the player', async () => {
  await withClock(NOW, async (clock) => {
    const request = { kind: 'archive', idec: 'X9', title: 'Pořad', resumeKey: 'ct:X9', lengthSeconds: 0 };
    const { ctl, backend } = await ctBar(clock, 'vod', { position: 754, duration: 0, request });
    assert.equal(ctl.tl.complete, false, 'no length known yet');
    assert.equal($('tl-clock').textContent, '0:12:34');
    backend.durationS = 3600;
    clock.advance(600); // the tick asks the player
    ctl.showBar();
    assert.equal(ctl.tl.complete, true);
    assert.deepEqual([$('tl-clock').textContent, $('tl-start').textContent, $('tl-end').textContent], ['0:12:34', '0:00', '1:00']);
    click(clock, ctl, 'right');
    assert.deepEqual(backend.seeks, [764]);
  });
});

test('"Pokračovat" (VOD): the request\'s key at the position; the end of the stream closes the player', async () => {
  await withClock(NOW, async (clock) => {
    const { ctl, store, saved, player } = await ctBar(clock, 'vod', { position: 754, duration: 3600, request: VOD });
    ctl.saveProgress(store, player.request);
    assert.deepEqual(saved, [{ key: 'ct:V1', progress: 754, length: 3600, meta: { title: 'Film' } }]);
    assert.equal(ctl.continueAt(), null, 'no window to continue in: the player\'s position');
    ctl.onEnded();
    assert.equal(player.closed, 1);
  });
});

test('long OK: subtitles after the audio tracks ("Titulky: Vypnuto" + each), the mute item last; OK picks one', async () => {
  await withClock(NOW, async (clock) => {
    const subtitles = [{ index: 4, language: 'cs', name: 'Čeština' }, { index: 5, language: 'cs', name: '' }];
    const { ctl, backend } = await ctBar(clock, 'timeshift', { position: 3600, subtitles });
    longOk(clock, ctl);
    assert.deepEqual($('audio-list-items').children.map((c) => c.textContent),
      ['Titulky: Vypnuto', 'Titulky: Čeština', 'Titulky: Čeština', 'Vypnout zvuk']);
    assert.equal(ctl.audioList.focus, 0, 'one audio track: the focus on the subtitle that is on (off)');
    ctl.onKeyDown('down');
    ctl.onKeyDown('enter');
    assert.equal(backend.shownSubtitle, 4);
    assert.equal($('osd-state').textContent, 'Titulky: Čeština');
    longOk(clock, ctl);
    assert.equal(ctl.audioList.focus, 1, 'the one that is on now');
    ctl.onKeyDown('up');
    ctl.onKeyDown('enter');
    assert.equal(backend.shownSubtitle, -1, 'off');
  });
});

test('the Anténa timeline gets the same subtitle items', async () => {
  await withClock(NOW, async (clock) => {
    global.document = fakeDocument();
    const backend = fakeBackend(clock, { position: 0, subtitles: [{ index: 2, language: 'cs', name: 'DVB titulky' }],
      audio: [{ index: 1, language: 'cs' }, { index: 2, language: 'en' }] });
    const ctl = new CT.TimelineController({ backend, mute: { muted: false } });
    ctl.openAudioList();
    assert.deepEqual($('audio-list-items').children.map((c) => c.textContent),
      ['Čeština', 'Angličtina', 'Titulky: Vypnuto', 'Titulky: DVB titulky', 'Vypnout zvuk']);
    assert.equal(ctl.audioList.focus, 0, 'several audio tracks: the focus on the current one, as before');
    ctl.stop();
  });
});

test('▼ to the programme after the one airing now (in the future) lands 10 s before the live edge', async () => {
  await withClock(NOW, async (clock) => {
    const { ctl, backend } = await ctBar(clock, 'timeshift', { position: pos(at('2026-11-14T16:45:00Z')) });
    click(clock, ctl, 'down');
    assert.equal(backend.seeks.length, 1);
    near(backend.seeks[0], pos(clock.now() - 10 * 1000), 'now − 10 s, not Večerníček\'s start', 0.2);
  });
});

test('"Pokračovat": the 30 s before the opened programme count as it (progress 0), the previous one is left alone', async () => {
  await withClock(NOW, async (clock) => {
    const { ctl, store, saved, player } = await ctBar(clock, 'timeshift', { position: pos(GUIDE[1].start - 20 * 1000) });
    ctl.saveProgress(store, player.request);
    assert.deepEqual(saved.map((x) => [x.key, x.progress]), [['ct:B1', 0]], 'Události at 0, not Ráno near its end');
    player.backend.pos = pos(GUIDE[1].start - 40 * 1000) * 1000; // before the margin: Ráno itself
    ctl.saveProgress(store, player.request);
    assert.deepEqual(saved[1].key, 'ct:A1');
  });
  await withClock(NOW, async (clock) => { // without the guide: the request's own key at 0
    const { ctl, store, saved, player } = await ctBar(clock, 'timeshift', { position: pos(GUIDE[1].start - 20 * 1000), guideFails: true });
    ctl.saveProgress(store, player.request);
    assert.deepEqual(saved.map((x) => [x.key, x.progress]), [['ct:B1', 0]]);
  });
});

test('a paused playhead the buffer slid past plays again from the lower clamp', async () => {
  await withClock(NOW, async (clock) => {
    const { ctl, backend } = await ctBar(clock, 'timeshift', { position: 70 });
    click(clock, ctl, 'playpause');
    assert.equal(backend.playing, false);
    clock.advance(30 * 1000); // the clamp is now at 90 s
    click(clock, ctl, 'playpause');
    assert.equal(backend.seeks.length, 1);
    near(backend.seeks[0], pos(clock.now() - W * 1000 + 60 * 1000), 'the lower clamp', 0.2);
    assert.equal(backend.playing, true);
  });
  await withClock(NOW, async (clock) => { // still inside: just plays on, no seek
    const { ctl, backend } = await ctBar(clock, 'timeshift', { position: 3600 });
    click(clock, ctl, 'playpause');
    clock.advance(30 * 1000);
    click(clock, ctl, 'playpause');
    assert.deepEqual(backend.seeks, []);
    assert.equal(backend.playing, true);
  });
});

test('the list is titled "Zvuk a titulky" with subtitles in the stream, else "Zvuk"', async () => {
  await withClock(NOW, async (clock) => {
    const { ctl } = await ctBar(clock, 'timeshift', { position: 3600, subtitles: [{ index: 4, language: 'cs', name: '' }] });
    ctl.openAudioList();
    assert.equal($('audio-list-title').textContent, 'Zvuk a titulky');
    ctl.closeAudioList();
  });
  await withClock(NOW, async (clock) => {
    const { ctl } = await ctBar(clock, 'timeshift', { position: 3600 });
    ctl.openAudioList();
    assert.equal($('audio-list-title').textContent, 'Zvuk');
  });
});
