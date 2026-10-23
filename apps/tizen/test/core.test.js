'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT, response, json } = require('./load');

const t = (iso) => Date.parse(iso);
const NBSP = ' ';

function live(name) {
  const r = response(name);
  return CT.CtParser.parseLive('CH_1', r.status, r.body);
}

function vod(name) {
  const r = response(name);
  return CT.CtParser.parseVod('226411010101016', r.status, r.body);
}

function failure(fn) {
  try {
    fn();
  } catch (e) {
    assert.ok(e instanceof CT.CtError, String(e));
    return e;
  }
  assert.fail('expected CtError');
}

test('live returns main and timeshift', () => {
  const s = live('live_CH_1.json');
  assert.match(s.main, /contentType=live/);
  assert.match(s.timeshift, /contentType=timeshift/);
});

test('live blocked, missing main and garbage', () => {
  assert.equal(failure(() => live('live_blocked.json')).userMessage, CT.Messages.BLOCKED);
  assert.equal(failure(() => CT.CtParser.parseLive('CH_1', 200, '{"streamUrls":{"main":null}}')).userMessage,
    CT.Messages.GENERIC);
  assert.equal(CT.CtParser.parseLive('CH_1', 200, '{"streamUrls":{"main":"https://x"}}').timeshift, null);
  for (const body of ['<html>', '[1,2]', '{"streamUrls":"x"}']) failure(() => CT.CtParser.parseLive('CH_1', 200, body));
});

test('vod urls, still live and CT error texts', () => {
  assert.match(vod('vod_ok.json'), /contentType=vod/);
  assert.equal(CT.CtParser.parseVod('1', 200, '{"streams":[{"url":""},{"url":null},{"url":"https://b"}]}'), 'https://b');
  const still = failure(() => vod('vod_still_live.json'));
  assert.ok(still instanceof CT.StillLiveError);
  assert.equal(still.channelId, 'CH_24');
  const licence = failure(() => vod('vod_no_licence.json'));
  assert.equal(licence.userMessage, 'ČT nemá práva pro' + NBSP + 'internet');
  assert.equal(licence.status, 'MISSING_LICENCE_NOW');
  const drm = failure(() => vod('vod_drm.json'));
  assert.equal(drm.userMessage, 'Video vyžaduje prohlížeč s' + NBSP + 'ochranou DRM\n' + CT.Messages.DRM_HINT);
  assert.equal(drm.status, 'UNSUPPORTED_DRM');
  assert.equal(failure(() => vod('vod_not_found.json')).userMessage, 'Video není k' + NBSP + 'dispozici');
  assert.equal(failure(() => CT.CtParser.parseVod('1', 500, 'Bad gateway')).userMessage, CT.Messages.VIDEO_UNAVAILABLE);
  assert.equal(failure(() => CT.CtParser.parseVod('1', 200, '{}')).userMessage, CT.Messages.VIDEO_UNAVAILABLE);
});

test('schedule parses all channels and fields', () => {
  const r = response('schedule_ct1_ct24.json');
  const plan = CT.CtParser.parseSchedule(r.status, r.body);
  assert.deepEqual(Object.keys(plan).sort(), ['ct1', 'ct24']);
  assert.equal(plan.ct1.length, 6);
  assert.equal(plan.ct24.length, 6);
  const second = plan.ct1[1];
  assert.equal(second.idec, '224381492050002');
  assert.equal(second.title, 'Testovací pořad 3');
  assert.equal(second.episodeTitle, 'Testovací pořad 4');
  assert.equal(second.genre, 'Seriály');
  assert.equal(second.start, t('2026-10-16T07:00:00Z'));
  assert.equal(second.end, t('2026-10-16T07:51:00Z'));
  assert.equal(second.isPlayableNow, false);
  assert.match(second.imageUrl, /^https:\/\//);
});

test('schedule skips malformed entries and fails on errors', () => {
  const body = `{"data":{"TVProgramDailyChannelsPlanV2":[{"channel":"ct1","program":[
    {"idec":"1","title":"ok","start":"2026-10-16T07:00:00.000Z","end":"2026-10-16T08:00:00.000Z"},
    {"idec":"2","title":"no end","start":"2026-10-16T08:00:00.000Z"},
    {"idec":"3","start":"yesterday","end":"2026-10-16T08:00:00.000Z"},
    "garbage", null,
    {"title":"no idec","start":"2026-10-16T08:00:00.000Z","end":"2026-10-16T09:00:00.000Z"}
  ]},{"program":[]},{"channel":"ct2","program":null}]}}`;
  const plan = CT.CtParser.parseSchedule(200, body);
  assert.deepEqual(plan.ct1.map((p) => p.idec), ['1']);
  assert.deepEqual(plan.ct2, []);
  assert.equal(plan.ct1[0].genre, '');
  const r = response('graphql_error.json');
  assert.equal(failure(() => CT.CtParser.parseSchedule(r.status, r.body)).userMessage, CT.Messages.SCHEDULE_FAILED);
  assert.equal(failure(() => CT.CtParser.parseSchedule(502, 'oops')).userMessage, CT.Messages.SCHEDULE_FAILED);
});

test('decisions: golden cases', () => {
  const root = json('decisions.json');
  const now = t(root.now);
  assert.equal(CT.Decisions.TIMESHIFT_WINDOW_MS, root.timeshiftWindowSeconds * 1000);
  assert.equal(CT.Decisions.START_MARGIN_MS, root.startMarginSeconds * 1000);
  assert.ok(root.programmes.length >= 7);
  for (const c of root.programmes) {
    const d = CT.Decisions.decide(t(c.start), t(c.end), c.isPlayableNow, now);
    assert.equal(d.action, c.expect.action, c.name);
    if ('offsetSeconds' in c.expect) assert.equal(d.offsetSeconds, c.expect.offsetSeconds, c.name);
  }
});

test('decisions: boundaries', () => {
  const start = t('2026-10-17T10:00:00Z');
  const end = t('2026-10-17T11:00:00Z');
  const H = 3600 * 1000;
  assert.equal(CT.Decisions.decide(start, end, false, start).action, 'startover');
  assert.equal(CT.Decisions.decide(start, end, false, start + 3 * H).action, 'vod_unavailable');
  assert.equal(CT.Decisions.decide(start, end, false, start + 3 * H - 1000).action, 'startover');
  assert.equal(CT.Decisions.decide(start, end, true, end).action, 'startover');
  assert.equal(CT.Decisions.decide(start, end, true, start + 3 * H).action, 'vod');
});

test('days: labels, range and Prague time', () => {
  const today = '2026-10-17';
  assert.equal(CT.Days.label(today, today), 'Dnes');
  assert.equal(CT.Days.label('2026-10-16', today), 'Včera');
  assert.equal(CT.Days.label('2026-10-12', today), 'po 12. 10.');
  assert.equal(CT.Days.label('2026-10-18', today), 'Zítra');
  assert.equal(CT.Days.label('2026-10-19', today), 'po 19. 10.');
  assert.equal(CT.Days.label('2026-01-01', today), 'čt 1. 1.');
  const days = CT.Days.guideDays(today);
  assert.equal(days.length, 10);
  assert.equal(days[0], '2026-10-10');
  assert.equal(days[9], '2026-10-19');
  assert.equal(CT.Days.today(t('2026-10-17T22:30:00Z')), '2026-10-18');
  assert.equal(CT.Days.time(t('2026-10-17T05:00:00Z')), '07:00');
  assert.equal(CT.Days.timeRange(t('2026-10-16T03:59:00Z'), t('2026-10-16T07:00:00Z')), '05:59–09:00');
  assert.equal(CT.Days.graphqlDate('2026-10-07'), '10.07.2026');
});

test('days: DST switches (last Sundays of March and October)', () => {
  assert.equal(CT.Days.time(t('2026-03-29T00:59:00Z')), '01:59');
  assert.equal(CT.Days.time(t('2026-03-29T01:00:00Z')), '03:00');
  assert.equal(CT.Days.time(t('2026-10-25T00:59:00Z')), '02:59');
  assert.equal(CT.Days.time(t('2026-10-25T01:00:00Z')), '02:00');
  assert.equal(CT.Days.time(t('2026-01-15T12:00:00Z')), '13:00');
  assert.equal(CT.Days.atMinute('2026-10-17', 20 * 60), t('2026-10-17T18:00:00Z'));
  assert.equal(CT.Days.atMinute('2026-12-01', 0), t('2026-11-30T23:00:00Z'));
  assert.equal(CT.Days.minuteOfDay(t('2026-10-17T18:30:00Z')), 20 * 60 + 30);
});

const at = (hm) => t(`2026-10-17T${hm}:00Z`);
const p = (id, from, to) => ({ idec: id, start: at(from), end: at(to) });

function assertContiguous(slots, axisStart) {
  assert.equal(slots[0].start, axisStart);
  for (let i = 1; i < slots.length; i++) assert.equal(slots[i - 1].end, slots[i].start, 'no overlap, no hole');
  slots.forEach((s) => assert.ok(s.end > s.start));
}

test('layout: fillers, overlaps, minimum width, tiny gaps, axis', () => {
  const L = CT.GuideLayout;
  const a0 = at('20:00');
  const a1 = at('23:00');
  assert.deepEqual(L.row([], a0, a1), [{ start: a0, end: a1, item: null }]);

  let slots = L.row([p('a', '20:30', '21:00'), p('b', '21:30', '22:00')], a0, a1);
  assertContiguous(slots, a0);
  assert.deepEqual(slots.map((s) => s.item && s.item.idec), [null, 'a', null, 'b', null]);
  assert.equal(slots[1].start, at('20:30'));
  assert.equal(slots[1].end, at('21:00'));

  slots = L.row([p('weather', '21:00', '21:08'), p('news', '21:05', '22:00')], a0, a1);
  assertContiguous(slots, a0);
  const news = slots.find((s) => s.item && s.item.idec === 'news');
  assert.equal(news.start, at('21:08'));
  assert.equal(news.end, at('22:00'));

  slots = L.row([p('short', '21:00', '21:01'), p('next', '21:01', '21:30')], a0, a1);
  assertContiguous(slots, a0);
  const short = slots.find((s) => s.item && s.item.idec === 'short');
  assert.equal(short.end - short.start, L.MIN_SLOT_MS);
  assert.equal(short.end, slots.find((s) => s.item && s.item.idec === 'next').start);

  slots = L.row([p('a', '20:00', '21:00'), p('b', '21:00', '22:00')], a0, at('22:00'));
  assert.deepEqual(slots.map((s) => s.item.idec), ['a', 'b']);
  slots = L.row([{ idec: 'a', start: at('20:00'), end: at('21:00') - 20000 }, p('b', '21:00', '22:00')], a0, at('22:00'));
  assertContiguous(slots, a0);
  assert.ok(slots.every((s) => s.item));

  // clipped to the axis (the broadcast day's edges); items outside it are left out
  slots = L.row([p('night', '19:00', '20:30'), p('late', '21:30', '22:45'), p('next', '22:50', '22:59')], a0, at('22:00'));
  assertContiguous(slots, a0);
  assert.deepEqual(slots.map((s) => s.item && s.item.idec), ['night', null, 'late']);
  assert.equal(slots[slots.length - 1].end, at('22:00'));
  assert.deepEqual(L.ticks(at('20:00'), at('21:10')), [at('20:00'), at('20:30'), at('21:00')]);
});

test('broadcast day bounds: 05:00 to 05:00 Prague, DST-aware', () => {
  assert.deepEqual(CT.Days.bounds('2026-10-17'), [Date.parse('2026-10-17T03:00:00Z'), Date.parse('2026-10-18T03:00:00Z')]);
  const autumn = CT.Days.bounds('2026-10-24'); // clocks go back on 25 Oct: 25 h
  assert.deepEqual(autumn, [Date.parse('2026-10-24T03:00:00Z'), Date.parse('2026-10-25T04:00:00Z')]);
  const spring = CT.Days.bounds('2026-03-28'); // clocks go forward on 29 Mar: 23 h
  assert.equal((spring[1] - spring[0]) / 3600000, 23);
  assert.equal(CT.Days.bounds('2026-01-10')[0], Date.parse('2026-01-10T04:00:00Z'));
});

function fixtureApi(routes, seen) {
  return new CT.CtApi((req) => {
    if (seen) seen.push(req);
    const hit = routes.find(([part]) => req.url.includes(part));
    if (!hit) return Promise.reject(new Error('no route ' + req.url));
    return Promise.resolve(typeof hit[1] === 'string' ? response(hit[1]) : hit[1]);
  });
}

test('api: requests and network failure', async () => {
  const seen = [];
  const api = fixtureApi([['channel/CH_1', 'live_CH_1.json'], ['graphql', 'schedule_ct1_ct24.json']], seen);
  const streams = await api.liveStreams('CH_1');
  assert.match(streams.main, /contentType=live/);
  assert.match(seen[0].url, /canPlayDrm=false/);
  assert.equal(seen[0].method, 'GET');
  await api.schedules(['ct1', 'ct24'], '2026-10-16');
  const body = JSON.parse(seen[1].body);
  assert.equal(seen[1].method, 'POST');
  assert.deepEqual(body.variables, { channels: ['ct1', 'ct24'], date: '10.16.2026' });
  assert.match(body.query, /TVProgramDailyChannelsPlanV2/);
  await assert.rejects(api.vodUrl('1'), (e) => e instanceof CT.CtError && e.userMessage === CT.Messages.GENERIC);
});

test('schedule repository: caching, onAir from yesterday, failed day not cached', async () => {
  let now = t('2026-10-16T05:00:00Z');
  let calls = 0;
  let fail = false;
  const api = new CT.CtApi(() => {
    calls++;
    return fail ? Promise.reject(new Error('down')) : Promise.resolve(response('schedule_ct1_ct24.json'));
  });
  const repo = new CT.ScheduleRepository(api, () => now);
  const plan = await repo.day('2026-10-16');
  assert.equal(plan.CH_1.length, 6);
  assert.deepEqual(plan.CH_2, []);
  await repo.day('2026-10-16');
  assert.equal(calls, 1);
  now += 2 * 3600 * 1000;
  await repo.day('2026-10-16');
  assert.equal(calls, 2, "today's plan refetched after an hour");

  // Current and next programme of the day's plan.
  const r2 = new CT.ScheduleRepository(fixtureApi([['graphql', 'schedule_ct1_ct24.json']]), () => now);
  const onAir = await r2.onAir(t('2026-10-16T05:30:00Z'));
  assert.equal(onAir.CH_1.current.idec, '226411010101016');
  assert.equal(onAir.CH_1.next.idec, '224381492050002');

  fail = true;
  await assert.rejects(repo.day('2026-10-20'));
  fail = false;
  await repo.day('2026-10-20');
});

test('live resolver reuses URLs for at most 60 s', async () => {
  let now = 0;
  let calls = 0;
  const api = new CT.CtApi(() => {
    calls++;
    return Promise.resolve(response('live_CH_1.json'));
  });
  const live = new CT.LiveResolver(api, () => now);
  await live.resolve('CH_1');
  now = 59 * 1000;
  await live.resolve('CH_1');
  assert.equal(calls, 1);
  await live.resolve('CH_1', true);
  assert.equal(calls, 2);
  now = 200 * 1000;
  await live.resolve('CH_1');
  assert.equal(calls, 3);
});

test('planner: startover seeks by wall clock with offset fallback', async () => {
  const now = t('2026-10-17T18:00:00Z');
  const api = fixtureApi([['channel/CH_1', 'live_CH_1.json']]);
  const planner = new CT.Planner(api, null, () => now);
  const target = await planner.plan({
    kind: 'programme', channelId: 'CH_1', title: 'x', idec: '1',
    start: t('2026-10-17T16:00:00Z'), end: t('2026-10-17T17:00:00Z'), isPlayableNow: false
  });
  assert.equal(target.kind, 'timeshift');
  assert.match(target.url, /contentType=timeshift/);
  assert.equal(target.seekToWallClock, t('2026-10-17T15:59:30Z'));
  assert.equal(target.fallbackOffsetMs, 3570 * 1000);
});

test('planner: vod, still live falls back to startover, CT errors pass through', async () => {
  const now = t('2026-10-17T18:00:00Z');
  const old = { kind: 'programme', channelId: 'CH_1', title: 'x', idec: '1',
    start: t('2026-10-17T08:00:00Z'), end: t('2026-10-17T09:00:00Z'), isPlayableNow: true };
  let planner = new CT.Planner(fixtureApi([['media/external', 'vod_ok.json']]), null, () => now);
  assert.equal((await planner.plan(old)).kind, 'vod');

  planner = new CT.Planner(fixtureApi([['media/external', 'vod_still_live.json'], ['channel/CH_24', 'live_CH_1.json']]),
    null, () => now);
  assert.equal((await planner.plan(old)).kind, 'timeshift');

  planner = new CT.Planner(fixtureApi([['media/external', 'vod_drm.json']]), null, () => now);
  await assert.rejects(planner.plan(Object.assign({}, old, { isPlayableNow: false })),
    (e) => e.status === 'UNSUPPORTED_DRM' && e.userMessage.endsWith(CT.Messages.DRM_HINT));

  await assert.rejects(planner.plan(Object.assign({}, old, { start: now + 1000, end: now + 3600000 })),
    (e) => e.userMessage === CT.Messages.FUTURE);
  await assert.rejects(planner.plan({ kind: 'live', channelId: 'CH_99', title: '' }), (e) => e instanceof CT.CtError);

  planner = new CT.Planner(fixtureApi([['channel/CH_1', { status: 200, body: '{"streamUrls":{"main":"https://m"}}' }]]),
    null, () => now);
  await assert.rejects(planner.plan({ kind: 'startover', channelId: 'CH_1', title: '', start: now - 60000 }),
    (e) => e.userMessage === CT.Messages.NO_TIMESHIFT);
});

test('entries: CT cells look and select per the rules', () => {
  const now = t('2026-10-17T18:00:00Z');
  const ch = CT.Channels.byId('CH_1');
  const e = (from, to, playable) => CT.Entries.ct(ch, {
    idec: '1', title: 'T', episodeTitle: '', description: '', genre: '', imageUrl: '',
    start: t(from), end: t(to), isPlayableNow: playable
  });
  const airing = e('2026-10-17T17:30:00Z', '2026-10-17T19:00:00Z', true);
  assert.equal(CT.Entries.look(airing, now), 'airing');
  const choice = CT.Entries.select(airing, now);
  assert.equal(choice.type, 'choice');
  assert.equal(choice.live.kind, 'live');
  assert.equal(choice.fromStart.kind, 'startover');
  assert.equal(CT.Entries.look(e('2026-10-17T09:00:00Z', '2026-10-17T10:00:00Z', false), now), 'dimmed');
  assert.equal(CT.Entries.select(e('2026-10-17T09:00:00Z', '2026-10-17T10:00:00Z', false), now).type, 'play');
  assert.equal(CT.Entries.look(e('2026-10-17T16:00:00Z', '2026-10-17T17:00:00Z', false), now), 'playable');
  const future = CT.Entries.select(e('2026-10-17T20:00:00Z', '2026-10-17T21:00:00Z', false), now);
  assert.deepEqual(future, { type: 'notice', text: CT.Messages.FUTURE });
  assert.equal(CT.Entries.progress(airing, now), 1 / 3);
});

test('GuideLayout.liveSlot picks the programme airing now, else the slot under now', () => {
  const L = CT.GuideLayout;
  const M = 60 * 1000;
  const base = t('2026-10-23T12:00:00Z');
  // a 1-min programme widened to 3 min, then a gap, then a long one
  const items = [
    { start: base, end: base + M, title: 'short' },
    { start: base + 20 * M, end: base + 60 * M, title: 'long' }
  ];
  const slots = L.row(items, base, base + 90 * M);
  assert.equal(slots[L.liveSlot(slots, base + 30 * M)].item.title, 'long');
  assert.equal(slots[L.liveSlot(slots, base + 30 * 1000)].item.title, 'short');
  // widened part of the short slot: nothing airs, the slot under now is the widened short one
  assert.equal(slots[L.liveSlot(slots, base + 2 * M)].item.title, 'short');
  assert.equal(slots[L.liveSlot(slots, base + 10 * M)].item, null); // "Mimo vysílání"
  assert.equal(L.liveSlot(slots, base + 120 * M), -1);
  assert.equal(L.liveSlot([], base), -1);
});

test('GuideLayout.window, covers and slotRange keep only the viewport plus a buffer', () => {
  const L = CT.GuideLayout;
  const H = 3600 * 1000;
  const view = { row: 10, rows: 7, start: 10 * H, end: 12 * H };
  const win = L.window(view, 60);
  assert.deepEqual(win, { r0: 6, r1: 21, t0: 8 * H, t1: 14 * H });
  assert.deepEqual(L.window({ row: 0, rows: 7, start: 0, end: 2 * H }, 5, { extraRows: 2, extraMs: H }),
    { r0: 0, r1: 5, t0: -H, t1: 3 * H });
  assert.equal(L.covers(win, view, 60, 1, 30 * 60 * 1000), true);
  assert.equal(L.covers(win, { row: 13, rows: 7, start: 10 * H, end: 12 * H }, 60, 1, 0), true); // one row to spare
  assert.equal(L.covers(win, { row: 14, rows: 7, start: 10 * H, end: 12 * H }, 60, 1, 0), false); // none left below
  assert.equal(L.covers(win, { row: 10, rows: 7, start: 11.8 * H, end: 13.8 * H }, 60, 1, 30 * 60 * 1000), false);
  assert.equal(L.covers({ r0: 0, r1: 60, t0: 0, t1: 99 * H }, { row: 0, rows: 7, start: H, end: 3 * H }, 60, 1, 0), true);
  assert.equal(L.covers(null, view, 60, 1, 0), false);
  const slots = [0, 1, 2, 3, 4, 5].map((i) => ({ start: i * H, end: (i + 1) * H }));
  assert.deepEqual(L.slotRange(slots, 1.5 * H, 3 * H), [1, 3]);
  assert.deepEqual(L.slotRange(slots, 0, 6 * H), [0, 6]);
  assert.deepEqual(L.slotRange(slots, 7 * H, 8 * H), [6, 6]);
  assert.deepEqual(L.slotRange(slots, -2 * H, -H), [0, 0]);
  assert.deepEqual(L.slotRange([], 0, H), [0, 0]);
});

test('GuideLayout.neighbourDay stops at the ends of the range', () => {
  const L = CT.GuideLayout;
  const days = ['2026-10-21', '2026-10-22', '2026-10-23'];
  assert.equal(L.neighbourDay(days, '2026-10-22', 1), '2026-10-23');
  assert.equal(L.neighbourDay(days, '2026-10-22', -1), '2026-10-21');
  assert.equal(L.neighbourDay(days, '2026-10-23', 1), null);
  assert.equal(L.neighbourDay(days, '2026-10-21', -1), null);
  assert.equal(L.neighbourDay(days, '2026-10-30', 1), null);
});
