'use strict';
// Domů in the app shell (spec/apps.md "Domů", T58): the first tab and the start, the Chamber checks and the number
// keys also on Domů, the gateway notice from the existing /dvb/status check. CT.App's own methods on fakes.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { CT } = require('./load');

global.window = global;
global.document = { hidden: false, getElementById: () => null, addEventListener: () => {} };
global.addEventListener = () => {};
require('../app/js/ui/platform.js');
require('../app/js/ui/keys.js');
require('../app/js/ui/app.js');
CT.Demo = { enabled: false };

const tick = () => new Promise((r) => setTimeout(r, 0));

function shell(tab) {
  const app = Object.create(CT.App);
  const calls = [];
  const screen = (name) => ({ show() {}, hide() {}, onMinute() {}, render() { calls.push(name + ':render'); },
    refreshDetail() {}, refreshPsp() {}, onKey() { return true; }, applyFocus() {} });
  app.tab = tab;
  app.screens = { home: screen('home'), live: screen('live'), guide: screen('guide'), archive: screen('archive'),
    settings: screen('settings') };
  app.player = { isOpen: () => false };
  app.channelPrefs = CT.ChannelPrefs.defaults ? CT.ChannelPrefs.defaults() : null;
  app.dvbConfig = null;
  app.dvbChannels = [];
  app.psp = { tick: (ids) => calls.push('psp:' + ids.length) };
  app.numberEntry = { digits: [], active: () => false, digit(d, count) { this.digits.push([d, count]); } };
  app.images = { clear() {} };
  app.watch = { setEnabled() {} };
  return { app, calls };
}

test('Domů is the first tab and the app starts on it', () => {
  assert.equal(CT.App.tab, 'home');
  const html = fs.readFileSync(path.join(__dirname, '..', 'app', 'index.html'), 'utf8');
  const tabs = Array.from(html.matchAll(/class="tab" data-tab="(\w+)">([^<]+)</g)).map((m) => m[1] + ' ' + m[2]);
  assert.deepEqual(tabs, ['home Domů', 'live Živě', 'guide TV průvodce', 'archive Archiv', 'settings Nastavení']);
  const app = fs.readFileSync(path.join(__dirname, '..', 'app', 'js', 'ui', 'app.js'), 'utf8');
  assert.match(app, /this\.showTab\('home'\); \/\/ every start/);
});

test('the Chamber checks run on Domů, Živě and the guide only', () => {
  for (const [tab, runs] of [['home', true], ['live', true], ['guide', true], ['archive', false], ['settings', false]]) {
    const { app, calls } = shell(tab);
    app.pspTick();
    assert.equal(calls.some((c) => c.startsWith('psp:')), runs, tab);
  }
});

test('number keys pick a channel on Domů, Živě and the guide, not in Archiv or Nastavení', () => {
  for (const [tab, used] of [['home', true], ['live', true], ['guide', true], ['archive', false], ['settings', false]]) {
    const { app } = shell(tab);
    assert.equal(app.onNumberKey('d2', { preventDefault() {} }), true, tab);
    assert.equal(app.numberEntry.digits.length, used ? 1 : 0, tab);
  }
});

test('a Chamber stream changing state redraws Domů while it is on screen', () => {
  const { app, calls } = shell('home');
  app.ctState = { onAir: null, error: null };
  app.dvbState = null;
  app.screens.live.render = () => calls.push('live:render');
  app.onPspChange();
  assert.ok(calls.includes('home:render'));
  const other = shell('archive');
  other.app.screens.live.render = () => {};
  other.app.onPspChange();
  assert.ok(!other.calls.includes('home:render'));
});

test('the gateway notice follows the existing /dvb/status check: failed, recording paused, fine again', async () => {
  const { app, calls } = shell('home');
  let answer = Promise.reject(new Error('unreachable'));
  app.dvbRepo = { client: { status: () => answer } };
  app.checkThumbs();
  await tick();
  assert.deepEqual(app.dvbStatus, { failed: true, badKey: false, recordingPaused: false });
  assert.equal(CT.Home.notice(true, app.dvbStatus), CT.Home.GATEWAY_DOWN);
  answer = Promise.resolve({ thumbnails: false, watchLog: false, recordingPaused: true });
  app.checkThumbs();
  await tick();
  assert.equal(CT.Home.notice(true, app.dvbStatus), CT.Home.RECORDING_PAUSED);
  const renders = calls.filter((c) => c === 'home:render').length;
  assert.equal(renders, 2);
  app.checkThumbs(); // the same answer again: no redraw
  await tick();
  assert.equal(calls.filter((c) => c === 'home:render').length, 2);
  // a 401 from the real parser (a wrong key): its own notice, not "not available"
  const P = CT.DvbParser;
  answer = Promise.resolve().then(() => P.parseStatus(401, '{"error": "bad key"}'));
  app.checkThumbs();
  await tick();
  assert.equal(CT.Home.notice(true, app.dvbStatus), CT.Home.BAD_KEY);
  answer = Promise.resolve().then(() => P.parseStatus(503, '{"error": "busy"}'));
  app.checkThumbs();
  await tick();
  assert.equal(CT.Home.notice(true, app.dvbStatus), CT.Home.GATEWAY_DOWN);
  assert.equal(calls.filter((c) => c === 'home:render').length, 4);
  answer = Promise.resolve({ thumbnails: false, watchLog: false, recordingPaused: true });
  app.checkThumbs();
  await tick();
  answer = Promise.resolve({ thumbnails: false, watchLog: false, recordingPaused: false });
  app.checkThumbs();
  await tick();
  assert.equal(CT.Home.notice(true, app.dvbStatus), null);
  assert.equal(calls.filter((c) => c === 'home:render').length, 6, 'the notice went away: redrawn');
});
