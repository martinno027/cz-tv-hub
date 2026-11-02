'use strict';
// spec/dvb-gateway.md "Sign-in log (T37)", spec/apps.md "Sign-in note (T37)": `X-Cztvhub-Client: tizen/<version>
// (<build>)` only on the profile requests and GET /dvb/status, never on other gateway requests, never to ČT or the
// Chamber's CDN; the Czech note.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT, response } = require('./load');

const H = CT.ClientHeader;
const NAME = 'X-Cztvhub-Client';
const KEY = 'k'.repeat(43);
const BASE = 'http://192.168.1.20:8098';
const config = new CT.DvbConfig(BASE, KEY);
// The gateway's validation (server/src/cztvhub/dvb/signinlog.py CLIENT_RE): anything else it ignores.
const GATEWAY_RE = /^[A-Za-z0-9 ./()+_-]{1,64}$/;

CT.Platform = { name: 'tizen' };
global.CT_BUILD_INFO = { version: '1.0.0', build: '412', commit: 'abc1234+', time: '1. 11. 20:00' };

/** A transport recording every request, answering `answer(request)` or 200 {}. */
function recorder(answer) {
  const seen = [];
  const transport = (request) => {
    seen.push(request);
    return Promise.resolve(answer ? answer(request) : { status: 200, body: '{}' });
  };
  return { seen, transport };
}

test('the note says what is logged, word for word (spec/apps.md "Sign-in note (T37)", T53 without its last sentence)', () => {
  assert.equal(H.NOTE, 'Připojení a přihlášení se zaznamenává (kdy, odkud a z jakého zařízení). ' +
    'Záznam vidí jen správce a po 90 dnech se smaže.');
  assert.ok(!/sledujete/.test(H.NOTE), 'T53: „Co sledujete, se nezaznamenává." is no longer true');
});

test('value: "<platform>/<version> (<build>)", accepted by the gateway', () => {
  assert.equal(H.value('tizen', { version: '1.0.0', build: '412' }), 'tizen/1.0.0 (412)');
  assert.equal(H.value('web', { version: '1.0.0', build: '412' }), 'web/1.0.0 (412)');
  assert.equal(H.current(), 'tizen/1.0.0 (412)');
  for (const v of [H.current(), H.value('web', global.CT_BUILD_INFO)]) assert.match(v, GATEWAY_RE);
});

test('value: a development build (no build info) or "?" parts still name the platform; bad characters dropped', () => {
  assert.equal(H.value('tizen', undefined), 'tizen');
  assert.equal(H.value('web', {}), 'web');
  assert.equal(H.value('tizen', { version: '?', build: '?' }), 'tizen'); // build.py without git / config.xml
  assert.equal(H.value('tizen', { version: '1.0.0', build: '?' }), 'tizen/1.0.0');
  assert.equal(H.value('tizen', { version: '1.0 (beta)', build: '4/12' }), 'tizen/1.0 beta (412)');
  assert.equal(H.value('tizen', { version: 'verze\n1', build: 'č1' }), 'tizen/verze1 (1)');
  const long = H.value('tizen', { version: '1'.repeat(80), build: '412' });
  assert.equal(long.length, 64);
  assert.match(long, GATEWAY_RE);
  assert.equal(H.value(null, global.CT_BUILD_INFO), null);
  assert.equal(H.value('', global.CT_BUILD_INFO), null);
});

test('applies: GET /dvb/status, POST /dvb/watch (T53) and the profile routes only', () => {
  const yes = [['GET', '/dvb/status'], ['POST', '/dvb/watch'], ['post', '/dvb/watch'], ['POST', '/dvb/profiles'], ['POST', '/dvb/profiles/login'], ['GET', '/dvb/profile'],
    ['PUT', '/dvb/profile'], ['POST', '/dvb/profile/logout'], ['get', '/dvb/status']];
  const no = [['POST', '/dvb/status'], ['GET', '/dvb/watch'], ['POST', '/dvb/watchx'], ['POST', '/dvb/watch/1'], ['GET', '/dvb/statusx'], ['GET', '/dvb/channels'], ['GET', '/dvb/epg?date=2026-11-01'],
    ['GET', '/dvb/live/ct1.m3u8'], ['GET', '/dvb/recording/r1.m3u8'], ['GET', '/dvb/recording/r1/breaks'],
    ['GET', '/dvb/timeline/r1'], ['GET', '/dvb/defaults'], ['GET', '/dvb/archive'], ['GET', '/dvb/thumb/r1.jpg'],
    ['GET', '/dvb/ct/live/CH_1'], ['GET', '/dvb/ct/vod/1'], ['GET', '/dvb/ct/schedule?channels=ct1&date=2026-11-01'],
    ['GET', '/dvb/profilex'], ['GET', '/dvb/profiles-x'], ['GET', '/web/'], ['GET', '/dvb']];
  for (const [m, p] of yes) assert.equal(H.applies(m, p), true, m + ' ' + p);
  for (const [m, p] of no) assert.equal(H.applies(m, p), false, m + ' ' + p);
});

test('DvbClient: the header on GET /dvb/status only, not on channels, EPG, breaks, timeline, defaults, warm-up', async () => {
  const r = recorder();
  const client = new CT.DvbClient(config, r.transport);
  await client.status().catch(() => {});
  assert.equal(r.seen[0].url, BASE + '/dvb/status');
  assert.equal(r.seen[0].headers[NAME], 'tizen/1.0.0 (412)');
  assert.equal(r.seen[0].headers.Authorization, 'Bearer ' + KEY);
  const others = [() => client.channels(), () => client.epg('2026-11-01'), () => client.breaks('r1'),
    () => client.timeline('r1'), () => CT.Household.fetchDefaults(client),
    () => client.warmUp(client.liveUrl('ct1')), () => client.warmUp(client.recordingUrl('r1'))];
  for (const call of others) await Promise.resolve().then(call).catch(() => {});
  assert.equal(r.seen.length, 1 + others.length);
  r.seen.slice(1).forEach((req) => assert.equal(req.headers[NAME], undefined, req.url));
});

test('ProfileClient: every profile call carries the header', async () => {
  const r = recorder(() => ({ status: 200, body: '{"token": "t", "name": "a", "settings": {"v": 1}, "version": 1}' }));
  const p = new CT.Profile.ProfileClient(config, r.transport);
  await p.create('a', 'heslo1234', { v: 1 });
  await p.login('a', 'heslo1234');
  await p.get('t');
  await p.put('t', { v: 1 });
  await p.logout('t');
  assert.deepEqual(r.seen.map((q) => q.method + ' ' + q.url.replace(BASE, '')),
    ['POST /dvb/profiles', 'POST /dvb/profiles/login', 'GET /dvb/profile', 'PUT /dvb/profile', 'POST /dvb/profile/logout']);
  r.seen.forEach((q) => assert.equal(q.headers[NAME], 'tizen/1.0.0 (412)', q.url));
});

test('no header to ČT: live, archive and schedule requests of CtApi', async () => {
  const r = recorder((q) => (q.method === 'POST' ? response('schedule_ct1_ct24.json') : response('live_CH_1.json')));
  const api = new CT.CtApi(r.transport);
  await api.liveStreams('CH_1').catch(() => {});
  await api.vodUrl('222411000100101').catch(() => {});
  await api.schedules(['ct1', 'ct24'], '2026-11-01').catch(() => {});
  assert.equal(r.seen.length, 3);
  r.seen.forEach((q) => {
    assert.ok(!/192\.168\.1\.20/.test(q.url), q.url); // straight to ČT on Samsung
    assert.equal((q.headers || {})[NAME], undefined, q.url);
  });
});

test('without a platform (core outside a page) nothing is added', () => {
  const saved = CT.Platform;
  CT.Platform = undefined;
  try {
    assert.deepEqual(H.add({}, 'GET', '/dvb/status'), {});
  } finally {
    CT.Platform = saved;
  }
  assert.deepEqual(H.add({}, 'GET', '/dvb/status'), { [NAME]: 'tizen/1.0.0 (412)' });
  assert.deepEqual(H.add({}, 'GET', '/dvb/channels'), {});
});

test('markup: the note under the profile form and right under the gateway status in Nastavení → Server', () => {
  const html = require('fs').readFileSync(require('path').join(__dirname, '..', 'app', 'index.html'), 'utf8');
  const profile = html.slice(html.indexOf('id="set-section-profile"'), html.indexOf('id="set-section-server"'));
  assert.match(profile, /id="pf-submit"[\s\S]*id="pf-signin-note"/);
  assert.match(html, /<div id="set-status"><\/div>\s*<p id="set-signin-note"/);
  const settings = require('fs').readFileSync(require('path').join(__dirname, '..', 'app', 'js', 'ui', 'settings.js'), 'utf8');
  assert.match(settings, /\$\('pf-signin-note'\)\.textContent = CT\.ClientHeader\.NOTE/);
  assert.match(settings, /\$\('set-signin-note'\)\.textContent = CT\.ClientHeader\.NOTE/);
});
