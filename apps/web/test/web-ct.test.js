'use strict';
// ČT on the web (spec/web.md "ČT (internet channels) on the web"): which CT request becomes which gateway path,
// ČT's answers reach the shared parsers unchanged, the gateway's own refusals carry its Czech text.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT, response } = require('./load');

const KEY = 'k'.repeat(43);
const config = new CT.DvbConfig('http://192.168.1.20:8098', KEY);

/** A fake gateway: records the requests, answers from `answers[path]` (a fixture response or a function). */
function gateway(answers) {
  const seen = [];
  const base = (request) => {
    seen.push(request);
    const path = request.url.replace('http://192.168.1.20:8098', '');
    const a = answers[path];
    if (!a) return Promise.resolve({ status: 404, body: '{"error": "Nenalezeno"}' });
    return Promise.resolve(typeof a === 'function' ? a(request) : a);
  };
  return { seen, base };
}

/** The real CtApi of the shared code with the web's transport, so the requests are the ones the app makes. */
function api(answers, cfg) {
  const g = gateway(answers);
  const transport = CT.WebCt.transport(g.base, () => (cfg === undefined ? config : cfg));
  return { g, api: new CT.CtApi(transport) };
}

test('live: CT playlist-live → GET /dvb/ct/live/<CH_n> with the key header, ČT body read by the shared parser', async () => {
  const { g, api: a } = api({ '/dvb/ct/live/CH_1': response('live_CH_1.json') });
  const viaGateway = await a.liveStreams('CH_1');
  const direct = CT.CtParser.parseLive('CH_1', 200, response('live_CH_1.json').body);
  assert.deepEqual(viaGateway, direct);
  assert.equal(g.seen.length, 1);
  assert.equal(g.seen[0].method, 'GET');
  assert.equal(g.seen[0].url, 'http://192.168.1.20:8098/dvb/ct/live/CH_1');
  assert.equal(g.seen[0].headers.Authorization, 'Bearer ' + KEY);
  assert.equal(g.seen[0].body, null);
});

test('vod: CT playlist-vod → GET /dvb/ct/vod/<idec>; ČT errors (DRM, licence) pass through unchanged', async () => {
  const idec = '222411000100101';
  const ok = api({ ['/dvb/ct/vod/' + idec]: response('vod_ok.json') });
  assert.equal(await ok.api.vodUrl(idec), CT.CtParser.parseVod(idec, 200, response('vod_ok.json').body));
  assert.equal(ok.g.seen[0].url, 'http://192.168.1.20:8098/dvb/ct/vod/' + idec);
  for (const name of ['vod_drm.json', 'vod_no_licence.json', 'vod_not_found.json']) {
    const r = response(name);
    const { api: a } = api({ ['/dvb/ct/vod/' + idec]: r });
    let direct = null;
    try {
      CT.CtParser.parseVod(idec, r.status, r.body);
    } catch (e) {
      direct = e;
    }
    await assert.rejects(a.vodUrl(idec), (e) => {
      assert.ok(direct, name + ' is an error');
      assert.equal(e.userMessage, direct.userMessage);
      assert.equal(e.status, direct.status);
      return true;
    });
  }
  // still live: ČT's platformChannel reaches the parser (StillLiveError), nothing rewritten
  const live = api({ ['/dvb/ct/vod/' + idec]: response('vod_still_live.json') });
  await assert.rejects(live.api.vodUrl(idec), (e) => e instanceof CT.StillLiveError);
});

test('schedule: the GraphQL POST → GET /dvb/ct/schedule?channels=&date=YYYY-MM-DD, the page query never sent', async () => {
  const day = CT.Days.today(Date.parse('2026-10-16T10:00:00Z'));
  const path = '/dvb/ct/schedule?channels=ct1,ct24&date=2026-10-16';
  const { g, api: a } = api({ [path]: response('schedule_ct1_ct24.json') });
  const got = await a.schedules(['ct1', 'ct24'], day);
  assert.deepEqual(got, CT.CtParser.parseSchedule(200, response('schedule_ct1_ct24.json').body));
  assert.equal(g.seen[0].method, 'GET');
  assert.equal(g.seen[0].url, 'http://192.168.1.20:8098' + path);
  assert.equal(g.seen[0].body, null);
  assert.ok(!g.seen[0].url.includes('query'));
  assert.ok(Object.keys(got).length > 0);
});

test('schedule: ČT GraphQL errors pass through (the shared parser decides)', async () => {
  const day = CT.Days.today(Date.parse('2026-10-16T10:00:00Z'));
  const { api: a } = api({ '/dvb/ct/schedule?channels=ct1&date=2026-10-16': response('graphql_error.json') });
  await assert.rejects(a.schedules(['ct1'], day), (e) => e.userMessage === CT.Messages.SCHEDULE_FAILED);
});

test('schedule: a gateway refusal (ČT unreachable) is a failed ČT day, said in the guide\'s ČT rows (T36)', async () => {
  const day = CT.Days.today(Date.parse('2026-10-16T10:00:00Z'));
  const path = '/dvb/ct/schedule?channels=ct1&date=2026-10-16';
  const { api: a } = api({ [path]: { status: 502, body: '{"error": "ČT neodpovídá."}' } });
  let failed = null;
  await assert.rejects(a.schedules(['ct1'], day), (e) => { failed = e; return true; });
  // the guide's ČT promise gives build() this error; a ČT row then shows it as its filler, Anténa rows do not
  const error = failed.userMessage || CT.Messages.SCHEDULE_FAILED;
  assert.equal(error, CT.Messages.SCHEDULE_FAILED);
  assert.equal(CT.Entries.fillerText('ct', !!error), 'Program se nepodařilo načíst');
  assert.equal(CT.Entries.fillerText('dvb', !!error), 'Mimo vysílání');
});

test('route: only the three CT calls of the apps; anything else is refused and never sent', async () => {
  const R = CT.WebCt.route;
  assert.equal(R({ method: 'GET', url: 'https://api.ceskatelevize.cz/video/v1/playlist-live/v1/stream-data/channel/CH_24?x=1' }), '/dvb/ct/live/CH_24');
  assert.equal(R({ method: 'GET', url: 'https://api.ceskatelevize.cz/video/v1/playlist-vod/v1/stream-data/media/external/123?x' }), '/dvb/ct/vod/123');
  assert.equal(R({ method: 'GET', url: 'https://api.ceskatelevize.cz/video/v1/playlist-vod/v1/stream-data/media/external/1%2F2?x' }), null);
  assert.equal(R({ method: 'GET', url: 'https://evil.example/video/v1/playlist-live/v1/stream-data/channel/CH_1?' }), null);
  assert.equal(R({ method: 'POST', url: 'https://api.ceskatelevize.cz/video/v1/playlist-live/v1/stream-data/channel/CH_1?' }), null);
  assert.equal(R({ method: 'GET', url: 'https://api.ceskatelevize.cz/graphql/?client=x' }), null);
  assert.equal(R({ method: 'POST', url: 'https://api.ceskatelevize.cz/graphql/?client=x', body: '{"query":"{ x }"}' }), null);
  assert.equal(R({ method: 'POST', url: 'https://api.ceskatelevize.cz/graphql/?client=x',
    body: JSON.stringify({ variables: { channels: ['ct1', 'a&b'], date: '10.16.2026' } }) }), null);
  assert.equal(R({ method: 'POST', url: 'https://api.ceskatelevize.cz/graphql/?client=x',
    body: JSON.stringify({ variables: { channels: ['ct1'], date: '2026-10-16' } }) }), null);
  assert.equal(CT.WebCt.isoDate('10.16.2026'), '2026-10-16');
  const g = gateway({});
  await assert.rejects(CT.WebCt.transport(g.base, () => config)({ method: 'GET', url: 'https://img.example.invalid/a.jpg' }));
  assert.equal(g.seen.length, 0);
});

test('away from home: the gateway 403 shows its text ("ČT jde v prohlížeči přehrát jen doma…")', async () => {
  const body = JSON.stringify({ error: CT.WebCt.HOME_ONLY });
  const { api: a } = api({ '/dvb/ct/live/CH_1': { status: 403, body }, '/dvb/ct/vod/1': { status: 403, body } });
  await assert.rejects(a.liveStreams('CH_1'), (e) => e.userMessage === CT.WebCt.HOME_ONLY && e.status === 'GATEWAY_403');
  await assert.rejects(a.vodUrl('1'), (e) => e.userMessage === CT.WebCt.HOME_ONLY);
  assert.equal(CT.WebCt.HOME_ONLY,
    'ČT jde v prohlížeči přehrát jen doma. Mimo domov použijte aplikaci Televize nebo iVysílání.');
});

test('gateway errors: 502 its text, 401 the bad-key text; ČT\'s own error bodies are not rewritten', async () => {
  const { api: a } = api({
    '/dvb/ct/live/CH_1': { status: 502, body: '{"error": "iVysílání teď neodpovídá"}' },
    '/dvb/ct/live/CH_2': { status: 401, body: '{"error": "Chybí nebo nesedí klíč"}' }
  });
  await assert.rejects(a.liveStreams('CH_1'), (e) => e.userMessage === 'iVysílání teď neodpovídá');
  await assert.rejects(a.liveStreams('CH_2'), (e) => e.userMessage === CT.DvbMessages.BAD_KEY);
  const ctOwn = response('vod_drm.json');
  assert.deepEqual(CT.WebCt.answer(ctOwn.status, ctOwn.body), ctOwn);
  assert.deepEqual(CT.WebCt.answer(200, '{"error": "x"}'), { status: 200, body: '{"error": "x"}' });
  assert.deepEqual(CT.WebCt.answer(500, 'not json'), { status: 500, body: 'not json' });
});

test('no key: nothing is sent, the text says where to enter it', async () => {
  const { g, api: a } = api({}, null);
  await assert.rejects(a.liveStreams('CH_1'), (e) => e.userMessage === 'Zadejte klíč brány v Nastavení → Server.');
  assert.equal(g.seen.length, 0);
});

test('demo: the web demo turns the gateway routes back into the CT requests the shared demo answers', async () => {
  const seen = [];
  const base = (r) => { seen.push(r); return Promise.resolve({ status: 200, body: '{}' }); };
  const wrapped = CT.WebDemo.wrap(base, false);
  await wrapped({ method: 'GET', url: 'http://h:8098/dvb/ct/live/CH_4', headers: {} });
  await wrapped({ method: 'GET', url: 'http://h:8098/dvb/ct/schedule?channels=ct1,ct24&date=2026-10-16', headers: {} });
  await wrapped({ method: 'GET', url: 'http://h:8098/dvb/channels', headers: {} });
  assert.match(seen[0].url, /\/stream-data\/channel\/CH_4\?$/);
  assert.equal(seen[1].method, 'POST');
  assert.deepEqual(JSON.parse(seen[1].body).variables, { channels: ['ct1', 'ct24'], date: '10.16.2026' });
  assert.equal(seen[2].url, 'http://h:8098/dvb/channels');
  const away = CT.WebDemo.wrap(base, true);
  const r = await away({ method: 'GET', url: 'http://h:8098/dvb/ct/vod/12', headers: {} });
  assert.equal(r.status, 403);
  assert.equal(JSON.parse(r.body).error, CT.WebCt.HOME_ONLY);
});

test('ČT through the gateway never carries X-Cztvhub-Client (T37: only /dvb/status and the profile routes)', async () => {
  global.CT_BUILD_INFO = { version: '1.0.0', build: '412' };
  const saved = CT.Platform;
  CT.Platform = { name: 'web' };
  try {
    assert.equal(CT.ClientHeader.current(), 'web/1.0.0 (412)'); // would be sent if the rule let it
    const { g, api: a } = api({
      '/dvb/ct/live/CH_1': response('live_CH_1.json'),
      '/dvb/ct/vod/1': response('vod_ok.json')
    });
    await a.liveStreams('CH_1').catch(() => {});
    await a.vodUrl('1').catch(() => {});
    await a.schedules(['ct1', 'ct24'], '2026-11-01').catch(() => {});
    assert.equal(g.seen.length, 3);
    assert.ok(g.seen.every((q) => /\/dvb\/ct\//.test(q.url)));
    g.seen.forEach((q) => assert.equal(q.headers['X-Cztvhub-Client'], undefined, q.url));
  } finally {
    CT.Platform = saved;
    delete global.CT_BUILD_INFO;
  }
});
