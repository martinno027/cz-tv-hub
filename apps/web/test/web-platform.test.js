'use strict';
// The web's CT.Platform (platform-web.js) over the Samsung defaults, with a fake window: gateway address, key in
// storage and cookie (set, cleared on "Odebrat bránu"), Poslanecká sněmovna forced off, errors, keys, version.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const { CT, WEB, APP } = require('./load');

// --- a fake browser around the scripts --------------------------------------------------------------------------
const storage = {};
const cookies = [];
const bodyClasses = new Set();
const opened = [];
const video = { canPlayType: () => '', audioTracks: undefined };
global.window = global;
global.location = { protocol: 'http:', hostname: '192.168.1.20', port: '8098', host: '192.168.1.20:8098',
  origin: 'http://192.168.1.20:8098', search: '' };
global.localStorage = {
  getItem: (k) => (k in storage ? storage[k] : null),
  setItem: (k, v) => { storage[k] = String(v); },
  removeItem: (k) => { delete storage[k]; }
};
global.document = {
  activeElement: { tagName: 'BODY' },
  body: { classList: { toggle: (c, on) => (on ? bodyClasses.add(c) : bodyClasses.delete(c)) } },
  getElementById: (id) => (id === 'html5player' ? video : null),
  set cookie(line) { cookies.push(line); }
};
global.open = (url, target, features) => { opened.push([url, target, features]); };
global.addEventListener = () => {};

require(path.join(APP, 'js', 'ui', 'platform.js'));
const samsung = Object.assign({}, CT.Platform);
require(path.join(APP, 'js', 'ui', 'settings-store.js'));
require(path.join(WEB, 'js', 'platform-web.js'));
require(path.join(APP, 'js', 'ui', 'app.js'));
const P = CT.Platform;
const KEY = 'AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_abcd'; // 42, URL-safe base64 like the gateway's

test('Samsung defaults keep the TV paths (nothing replaced)', () => {
  assert.equal(samsung.name, 'tizen');
  for (const k of ['playerBackend', 'refusePlay', 'playErrorAction', 'keyName', 'officialApp', 'gatewayAddress',
    'fitStage', 'onGatewayConfig', 'playerHint']) assert.equal(samsung[k], null, k);
  assert.equal(samsung.startOnLoad, true);
  assert.equal(samsung.versionSuffix, '');
  assert.equal(samsung.sourceAllowed('psp'), true);
  const t = () => {};
  assert.equal(samsung.ctTransport(t), t);
  assert.equal(samsung.fixturesBase, '../../../spec/fixtures/');
});

test('web: the gateway is the page\'s origin, port always written out', () => {
  assert.equal(P.name, 'web');
  assert.equal(P.gatewayAddress(), 'http://192.168.1.20:8098');
  const A = CT.WebGateway.address;
  assert.equal(A({ protocol: 'http:', hostname: 'pi.tail1234.ts.net', port: '' }), 'http://pi.tail1234.ts.net:80');
  assert.equal(A({ protocol: 'https:', hostname: 'pi.local', port: '' }), 'https://pi.local:443');
  assert.equal(A({ protocol: 'http:', hostname: '[fd7a:115c:a1e0::1]', port: '8098' }), 'http://[fd7a:115c:a1e0::1]:8098');
  assert.equal(CT.DvbConfig.normalizeAddress(A({ protocol: 'http:', hostname: 'pi.local', port: '' })), 'http://pi.local:80');
});

test('storage and cookie: "Uložit a ověřit" stores the key (same localStorage key as Samsung) and sets the cookie; ' +
  '"Odebrat bránu" deletes both', () => {
  CT.SettingsStore.save(P.gatewayAddress(), KEY);
  assert.deepEqual(JSON.parse(storage['cztvhub.dvb']), { address: 'http://192.168.1.20:8098', key: KEY });
  const config = CT.SettingsStore.config();
  assert.equal(config.baseUrl, 'http://192.168.1.20:8098');
  P.onGatewayConfig(config);
  assert.equal(cookies.at(-1), 'cztvhub_key=' + KEY + '; Path=/dvb; SameSite=Strict; Max-Age=34560000');
  assert.ok(!bodyClasses.has('web-nokey'));

  CT.SettingsStore.clear();
  assert.equal(CT.SettingsStore.config(), null);
  P.onGatewayConfig(null);
  assert.equal(cookies.at(-1), 'cztvhub_key=; Path=/dvb; SameSite=Strict; Max-Age=0');
  assert.ok(bodyClasses.has('web-nokey'));
  assert.ok(!JSON.stringify(storage).includes(KEY));
});

test('storage: a key saved at another address of the same gateway still talks to this page\'s origin', () => {
  CT.SettingsStore.save('http://192.168.1.99:8098', KEY);
  assert.equal(CT.SettingsStore.config().baseUrl, 'http://192.168.1.20:8098');
  CT.SettingsStore.clear();
});

test('cookie: Secure over https; a key that cannot be a cookie value is not set (the header still works)', () => {
  assert.match(CT.WebGateway.setCookie(KEY, true), /; Secure$/);
  assert.equal(CT.WebGateway.setCookie('a;b', false), null);
  assert.equal(CT.WebGateway.setCookie('a b', false), null);
  const doc = { set cookie(v) { this.last = v; } };
  CT.WebGateway.syncCookie(doc, { key: 'bad;key' }, false);
  assert.equal(doc.last, 'cztvhub_key=; Path=/dvb; SameSite=Strict; Max-Age=0');
});

test('Poslanecká sněmovna is on the web as on Samsung (owner 2026-11-01, T28)', () => {
  const App = CT.App;
  App.channelPrefs = CT.ChannelPrefs.empty();
  App.dvbConfig = null;
  const sources = () => App.channelList([]).map((i) => i.source);
  assert.equal(App.sourceOn('psp'), true);
  assert.ok(sources().includes('psp'));
  assert.ok(sources().includes('ct'));
  // its own switch still turns it off, as on the TV
  App.channelPrefs = CT.ChannelPrefs.setSource(CT.ChannelPrefs.empty(), 'psp', false);
  assert.ok(!sources().includes('psp'));
  App.channelPrefs = CT.ChannelPrefs.empty();
});

test('errors: ČT requests get "Otevřít v iVysílání" (iVysílání in a new tab, nothing sent along); others none', () => {
  for (const kind of ['live', 'startover', 'programme', 'archive']) {
    const a = P.playErrorAction({ kind });
    assert.equal(a.label, 'Otevřít v iVysílání', kind);
  }
  for (const kind of ['dvbLive', 'dvbRecorded', 'pspLive']) assert.equal(P.playErrorAction({ kind }), null, kind);
  P.playErrorAction({ kind: 'live' }).run();
  P.officialApp();
  assert.deepEqual(opened, [['https://www.ceskatelevize.cz/ivysilani/', '_blank', 'noopener,noreferrer'],
    ['https://www.ceskatelevize.cz/ivysilani/', '_blank', 'noopener,noreferrer']]);
});

test('Anténa without HEVC: the Czech text instead of playing; ČT and the demo timeline play', () => {
  const dvb = { kind: 'live', dvb: {}, psp: false, url: 'x' };
  assert.equal(P.refusePlay(dvb), CT.WebPlayer.HEVC_MESSAGE); // the fake video decodes nothing, no hls.js
  assert.equal(P.refusePlay({ kind: 'live', dvb: null, psp: false }), null);
  video.canPlayType = (t) => (t === 'application/vnd.apple.mpegurl' || t === CT.WebPlayer.HEVC_TYPE ? 'maybe' : '');
  assert.equal(P.refusePlay(dvb), null);
});

test('keys go through the web mapping; version line "… (web)"; the app starts itself after the loader', () => {
  assert.equal(P.keyName({ keyCode: 32 }, { 32: 'x' }), 'playpause');
  document.activeElement = { tagName: 'INPUT' };
  assert.equal(P.keyName({ keyCode: 8 }, { 8: 'back' }), null);
  document.activeElement = { tagName: 'BODY' };
  assert.equal(P.versionSuffix, ' (web)');
  assert.equal(P.startOnLoad, false);
  assert.equal(P.fixturesBase, 'fixtures/');
  assert.equal(CT.Web.thirdPartyLine(), 'Third-party: hls.js 1.7.4 (Apache-2.0)');
});

test('the CT transport of the web wraps the app\'s (and the demo\'s) transport', async () => {
  const seen = [];
  const t = P.ctTransport((r) => { seen.push(r.url); return Promise.resolve({ status: 200, body: '{}' }); },
    () => new CT.DvbConfig('http://192.168.1.20:8098', KEY));
  await t({ method: 'GET', url: CT.WebCt.API + '/video/v1/playlist-live/v1/stream-data/channel/CH_1?x', headers: {} });
  assert.deepEqual(seen, ['http://192.168.1.20:8098/dvb/ct/live/CH_1']);
});
