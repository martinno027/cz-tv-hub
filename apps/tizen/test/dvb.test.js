'use strict';
// Fixtures captured from the dev gateway (fake TVHeadend) into spec/fixtures/dvb/.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT, response } = require('./load');

const KEY = 'dev-key-dev-key-dev-key-dev-key-0000';
const config = new CT.DvbConfig('http://192.168.1.5:8098', KEY);
const CT1 = 'cb2de4250e92ae9a4c549f7e48885ff8';
const captured = Date.parse('2026-10-18T06:45:00Z');

function client(routes, seen) {
  return new CT.DvbClient(config, (req) => {
    if (seen) seen.push(req);
    const hit = (routes || []).find(([part]) => req.url.includes(part));
    if (!hit) return Promise.reject(new Error('no route ' + req.url));
    return Promise.resolve(typeof hit[1] === 'string' ? response('dvb/' + hit[1]) : hit[1]);
  });
}

test('channels sorted with optional icon', async () => {
  const channels = await client([['/dvb/channels', 'dvb_channels.json']]).channels();
  assert.deepEqual(channels.map((c) => c.name), ['ČT1 HD', 'ČT2 HD', 'Nova HD', 'Prima HD']);
  assert.equal(channels[0].iconPath, '/dvb/icon/' + CT1);
  assert.equal(channels[1].iconPath, null);
});

test('every request carries the bearer key', async () => {
  const seen = [];
  const status = await client([['/dvb/status', 'dvb_status.json']], seen).status();
  assert.equal(seen[0].url, 'http://192.168.1.5:8098/dvb/status');
  assert.equal(seen[0].headers.Authorization, 'Bearer ' + KEY);
  assert.ok(status.enabled && status.tvheadendReachable);
  assert.ok(status.retentionDays >= 1 && status.retentionDays <= 14);
  assert.equal(config.cookie(), 'cztvhub_key=' + KEY);
});

test('epg parses recording states and opaque ids', async () => {
  const events = await client([['/dvb/epg?date=2026-10-18', 'dvb_epg_today.json']]).epg('2026-10-18');
  assert.ok(events.length > 100);
  const states = new Set(events.filter((e) => e.recording).map((e) => e.recording.state));
  assert.deepEqual([...states].sort(), ['completed', 'failed', 'recording', 'scheduled']);
  const past = events.find((e) => e.id.startsWith('dvr:'));
  assert.equal(past.recording.state, 'completed');
  assert.ok(events.some((e) => !e.recording && e.genre));
});

test('errors: unauthorized, bad date, tuner busy, unreachable, garbage', async () => {
  await assert.rejects(client([['/dvb/', 'dvb_unauthorized.json']]).channels(),
    (e) => e.userMessage === CT.DvbMessages.BAD_KEY);
  await assert.rejects(client([['/dvb/epg', 'dvb_epg_bad_date.json']]).epg('2020-01-01'),
    (e) => e.status === '400' && e.userMessage.length > 0);
  assert.equal(CT.DvbParser.errorText('{"error": "Tuner je obsazený"}'), 'Tuner je obsazený');
  assert.throws(() => CT.DvbParser.parseChannels(503, '{"error": "Tuner je obsazený"}'),
    (e) => e.userMessage === 'Tuner je obsazený');
  assert.equal(CT.DvbParser.errorText('<html>'), null);
  await assert.rejects(client([]).channels(), (e) => e.userMessage === CT.DvbMessages.UNREACHABLE);
  assert.deepEqual(CT.DvbParser.parseEpg(200, '{"events":[1,{"id":"x"},null]}'), []);
  assert.throws(() => CT.DvbParser.parseEpg(200, 'not json'));
});

test('warm-up surfaces the gateway text for 503', async () => {
  const c = client([['/dvb/live/', { status: 503, body: '{"error": "Tuner je obsazený"}' }],
    ['/dvb/recording/', { status: 200, body: '#EXTM3U' }]]);
  await assert.rejects(c.warmUp(c.liveUrl(CT1)), (e) => e.userMessage === 'Tuner je obsazený' && e.status === '503');
  await c.warmUp(c.recordingUrl('abc'));
});

test('repository de-duplicates events and finds what is on air', async () => {
  const repo = new CT.DvbRepository(client([['/dvb/epg', 'dvb_epg_today.json']]), () => captured);
  const onAir = await repo.onAir(captured);
  assert.equal(onAir[CT1].current.recording.state, 'recording');
  const day = await repo.day('2026-10-18');
  Object.values(day).forEach((list) => assert.equal(new Set(list.map((e) => e.id)).size, list.length));
});

test('decisions follow spec/dvb-gateway.md', async () => {
  const events = await client([['/dvb/epg', 'dvb_epg_today.json']]).epg('2026-10-18');
  const airing = (e) => e.start <= captured && captured < e.end;
  const action = (state, isAiring) => {
    const e = events.find((x) => (x.recording ? x.recording.state : null) === state && airing(x) === isAiring &&
      (isAiring || x.end <= captured));
    return CT.DvbDecisions.decide(e, captured);
  };
  assert.equal(action('recording', true), 'live_or_startover');
  assert.equal(action('completed', false), 'recording');
  assert.equal(action('failed', false), 'not_recorded');
  assert.equal(action(null, true), 'live');
  assert.equal(CT.DvbDecisions.decide(events.find((e) => e.recording && e.recording.state === 'scheduled'), captured),
    'future');

  const start = Date.parse('2026-10-18T10:00:00Z');
  const end = Date.parse('2026-10-18T11:00:00Z');
  const e = (state) => ({ id: '1', channelId: 'c', title: 't', start, end, recording: state ? { id: 'r', state } : null });
  assert.equal(CT.DvbDecisions.decide(e('completed'), start), 'live');
  assert.equal(CT.DvbDecisions.decide(e(null), end), 'not_recorded');
  assert.equal(CT.DvbDecisions.decide(e('recording'), end), 'not_recorded');
  assert.equal(CT.DvbDecisions.decide(e('scheduled'), end), 'not_recorded');
});

test('entries: Anténa cells', () => {
  const ch = { id: CT1, number: 1, name: 'ČT1 HD', iconPath: null };
  const start = Date.parse('2026-10-18T10:00:00Z');
  const end = Date.parse('2026-10-18T11:00:00Z');
  const entry = (state) => CT.Entries.dvb(ch, { id: '1', channelId: CT1, title: 't', subtitle: '', description: '',
    genre: '', start, end, recording: state ? { id: 'rec', state } : null });
  const s = CT.Entries.select(entry('recording'), start + 1000);
  assert.equal(s.type, 'choice');
  assert.equal(s.live.kind, 'dvbLive');
  assert.deepEqual([s.fromStart.kind, s.fromStart.recordingId, s.fromStart.fromStart], ['dvbRecorded', 'rec', true]);
  assert.equal(CT.Entries.select(entry(null), start + 1000).fromStart, null);
  assert.equal(CT.Entries.select(entry('completed'), end).request.fromStart, false);
  assert.deepEqual(CT.Entries.select(entry('failed'), end), { type: 'notice', text: CT.DvbMessages.NOT_RECORDED });
  assert.equal(CT.Entries.look(entry('failed'), end), 'dimmed');
});

test('planner: Anténa targets carry the client for the key cookie', async () => {
  const c = client([['/dvb/', { status: 200, body: '#EXTM3U' }]]);
  const planner = new CT.Planner(new CT.CtApi(() => Promise.reject(new Error('no CT'))), null, Date.now, () => c);
  const live = await planner.plan({ kind: 'dvbLive', channelId: CT1, title: 'ČT1 HD' });
  assert.equal(live.url, 'http://192.168.1.5:8098/dvb/live/' + CT1 + '.m3u8');
  assert.equal(live.dvb, c);
  const rec = await planner.plan({ kind: 'dvbRecorded', channelId: CT1, title: 't', recordingId: 'abc', fromStart: true });
  assert.equal(rec.url, 'http://192.168.1.5:8098/dvb/recording/abc.m3u8');
  assert.equal(rec.startPositionMs, 0);
  const none = new CT.Planner(new CT.CtApi(() => Promise.reject(new Error('x'))));
  await assert.rejects(none.plan({ kind: 'dvbLive', channelId: CT1, title: '' }),
    (e) => e.userMessage === CT.DvbMessages.UNREACHABLE);
});

test('address normalisation allows only private hosts', () => {
  const n = CT.DvbConfig.normalizeAddress;
  assert.equal(n('pi.tail1234.ts.net'), 'http://pi.tail1234.ts.net:8098');
  assert.equal(n('pi'), 'http://pi:8098');
  assert.equal(n('http://192.168.1.5:9000/'), 'http://192.168.1.5:9000');
  assert.equal(n('100.101.102.103'), 'http://100.101.102.103:8098');
  assert.equal(n('10.0.2.2:8098'), 'http://10.0.2.2:8098');
  assert.equal(n('cztvhub.local'), 'http://cztvhub.local:8098');
  assert.equal(n('[fd7a:115c:a1e0::1]'), 'http://[fd7a:115c:a1e0::1]:8098');
  assert.equal(n('https://pi.tail1234.ts.net'), 'https://pi.tail1234.ts.net:443');
  for (const bad of ['example.com', '8.8.8.8', '100.200.1.1', '', '   ', 'https://example.com', 'ftp://pi', '300.1.1.1'])
    assert.equal(n(bad), null, bad);
  assert.equal(CT.DvbConfig.isPrivateHost('172.32.0.1'), false);
  assert.throws(() => CT.DvbConfig.fromSettings('pi', 'short'), (e) => e.userMessage === CT.DvbMessages.KEY_TOO_SHORT);
  assert.throws(() => CT.DvbConfig.fromSettings('example.com', KEY), (e) => e.userMessage === CT.DvbMessages.HOST_NOT_ALLOWED);
  assert.equal(CT.DvbConfig.fromSettings(' pi ', ' ' + KEY + ' ').key, KEY);
});
