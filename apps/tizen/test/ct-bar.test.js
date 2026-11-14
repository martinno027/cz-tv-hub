'use strict';
// ČT player bar (spec/apps.md "ČT player bar (T62)") against spec/fixtures/ct_bar_cases.json: the one window of a ČT
// start-over / archive playback, the clamps of seeks and of the moving playhead, the elapsed labels of a VOD without
// a known start.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT, json } = require('./load');

const T = CT.Timeline;
const cases = json('ct_bar_cases.json');
const at = (iso) => (iso === null ? null : Date.parse(iso));

test('golden window: the timeshift buffer slides with now, a VOD spans its programme (or has no wall clock)', () => {
  assert.ok(cases.window.length >= 4);
  for (const c of cases.window) {
    const w = T.ctWindow(c.kind, at(c.now), c.kind === 'vod' ? at(c.programmeStart) : null, c.duration);
    assert.deepEqual(w, { start: at(c.start), end: at(c.end), growing: c.growing }, c.name);
  }
});

test('golden clamp: [buffer start + 60 s, now − 10 s] for timeshift, [0, duration − 10 s] for VOD', () => {
  assert.ok(cases.clamp.length >= 9);
  for (const c of cases.clamp) {
    if (c.kind === 'timeshift') assert.equal(T.ctClamp('timeshift', at(c.now), at(c.target)), at(c.result), c.name);
    else assert.equal(T.ctClamp('vod', 0, c.target, c.duration), c.result, c.name);
  }
});

test('golden labels: a VOD without a start shows the elapsed position and 0:00 … the duration', () => {
  assert.ok(cases.labels.length >= 3);
  for (const c of cases.labels) {
    assert.equal(T.elapsedLabel(c.position), c.clock, c.name);
    const edges = T.elapsedEdges(c.duration);
    assert.deepEqual([edges.start, edges.end], c.edges, c.name);
  }
});

test('a VOD whose duration is not known yet has no upper clamp (the player has not told it)', () => {
  assert.equal(T.ctClamp('vod', 0, 5000, 0), 5000);
  assert.equal(T.ctClamp('vod', 0, -1, 0), 0);
});

test('the long-OK list with subtitles: "Titulky: Vypnuto" + each by its name, between the audio tracks and mute', () => {
  const audio = [{ index: 1, language: 'cs' }, { index: 2, language: 'en' }];
  const subs = [{ index: 7, language: 'cs', name: 'Čeština' }, { index: 8, language: 'en', name: '' }, { index: 9, language: '', name: '' }];
  const c = CT.Mute.choice(audio, 2, false, subs, 8);
  assert.deepEqual(c.items.map((i) => [i.kind, i.label, i.current]), [
    ['track', 'Čeština', false], ['track', 'Angličtina', true],
    ['subtitle', 'Titulky: Vypnuto', false], ['subtitle', 'Titulky: Čeština', false],
    ['subtitle', 'Titulky: Angličtina', true], ['subtitle', 'Titulky 3', false], ['mute', 'Vypnout zvuk', false]
  ]);
  assert.equal(c.focus, 1, 'several audio tracks: the current one');
  const one = CT.Mute.choice([audio[0]], 1, true, subs, 7);
  assert.equal(one.note, CT.Mute.ONE_TRACK);
  assert.equal(one.items[one.focus].label, 'Titulky: Čeština', 'one audio track: the subtitle that is on');
  assert.equal(CT.Mute.choice([audio[0]], 1, true, subs, 42).items[0].current, true, 'an unknown one counts as off');
  const none = CT.Mute.choice([audio[0]], 1, false, [], -1);
  assert.deepEqual(none, CT.Mute.choice([audio[0]], 1, false), 'no subtitles in the stream: no subtitle items');
  assert.notDeepEqual(none.items, one.items);
});

test('AVPlay track info: TEXT tracks are the subtitles, AUDIO stays audio', () => {
  const infos = [{ type: 'VIDEO', index: 0 }, { type: 'AUDIO', index: 1, extra_info: '{"language":"cs"}' },
    { type: 'TEXT', index: 2, extra_info: '{"track_lang":"cs"}' }, { type: 'TEXT', index: 3, extra_info: 'broken' }];
  assert.deepEqual(CT.SubtitleTracks.fromAvPlay(infos), [{ index: 2, language: 'cs', name: '' }, { index: 3, language: '', name: '' }]);
  assert.deepEqual(CT.AudioTracks.fromAvPlay(infos), [{ index: 1, language: 'cs' }]);
});
