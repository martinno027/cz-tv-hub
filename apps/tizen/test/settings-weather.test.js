'use strict';
// Nastavení → Server, "Počasí podle polohy této televize" (spec/apps.md "Location for weather", T59): shown with a
// gateway on a device with a location source, reached with ▼ under the buttons (and under the thumbnails switch),
// off at once, on only through the same question as on Domů (Return = no answer, the switch stays off).
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

function node(id) {
  const classes = new Set();
  return {
    id, textContent: '', value: '', type: 'password',
    classList: {
      toggle: (c, on) => ((on === undefined ? !classes.has(c) : on) ? classes.add(c) : classes.delete(c)),
      contains: (c) => classes.has(c), add: (c) => classes.add(c), remove: (c) => classes.delete(c)
    },
    children: [],
    set innerHTML(v) { this.children = []; },
    get childNodes() { return this.children; },
    appendChild(c) { this.children.push(c); return c; },
    addEventListener() {}, focus() {}
  };
}

const nodes = {};
const stored = {};
global.window = global;
global.document = {
  getElementById: (id) => nodes[id] || (nodes[id] = node(id)),
  createElement: () => node(''),
  addEventListener() {},
  hidden: false,
  querySelectorAll: () => [],
  activeElement: null
};
global.localStorage = {
  getItem: (k) => (k in stored ? stored[k] : null),
  setItem: (k, v) => { stored[k] = String(v); }
};
CT.scrollIntoView = () => {};
require('../app/js/ui/platform.js');
require('../app/js/ui/settings-store.js');
require('../app/js/ui/archive-cards.js');
require('../app/js/ui/settings.js');
global.addEventListener = () => {};
require('../app/js/ui/keys.js');
require('../app/js/ui/app.js');
CT.Demo = { enabled: false };
const Menu = CT.ArchiveCards.Menu;

function screen(opts) {
  const calls = [];
  const app = {
    dvbConfig: opts.gateway ? {} : null, dvbThumbs: !!opts.thumbs, profile: null,
    inContent: () => true,
    geolocation: () => (opts.geo ? {} : null),
    setWeatherLocation: (on) => { calls.push(on); CT.WeatherLocationSwitch.set(on); }
  };
  app.askWeatherLocation = CT.App.askWeatherLocation;
  const s = new CT.SettingsScreen(app);
  s.zone = 'form';
  s.focus = 2; // "Uložit a vyzkoušet"
  s.renderWeather();
  return { s, calls };
}

const toggle = () => document.getElementById('set-weather');

test('shown only with a gateway and a location source; the text says the state', () => {
  screen({ gateway: true, geo: false });
  assert.ok(toggle().classList.contains('hidden'));
  screen({ gateway: false, geo: true });
  assert.ok(toggle().classList.contains('hidden'));
  assert.ok(document.getElementById('set-weather-note').classList.contains('hidden'));
  screen({ gateway: true, geo: true });
  assert.ok(!toggle().classList.contains('hidden'));
  assert.equal(toggle().textContent, '☐ Počasí podle polohy této televize'); // not decided yet = off
  assert.equal(document.getElementById('set-weather-note').textContent, CT.Weather.SETTING_NOTE);
});

test('▼ from the buttons reaches it (under the thumbnails switch when there is one); on = the question; ▲ goes back', () => {
  Object.keys(stored).forEach((k) => delete stored[k]);
  const { s, calls } = screen({ gateway: true, geo: true });
  s.onKey('down');
  assert.equal(s.focus, 6);
  s.onKey('enter'); // on: the question first, nothing switched yet
  assert.ok(Menu.isOpen());
  assert.equal(document.getElementById('ar-menu-title').textContent, CT.Weather.CONSENT_TEXT);
  assert.deepEqual(calls, []);
  assert.equal(s.onBack(), true); // Return: no answer
  assert.ok(!Menu.isOpen());
  assert.deepEqual(calls, []);
  assert.equal(CT.WeatherLocationSwitch.state(), 'ask');
  assert.equal(toggle().textContent, '☐ Počasí podle polohy této televize');
  s.onKey('enter');
  s.onKey('right');
  s.onKey('enter'); // Nepoužívat
  assert.deepEqual(calls, [false]);
  s.onKey('enter');
  s.onKey('enter'); // Povolit
  assert.deepEqual(calls, [false, true]);
  assert.equal(toggle().textContent, '☑ Počasí podle polohy této televize');
  s.onKey('enter'); // off: at once, no question
  assert.ok(!Menu.isOpen());
  assert.deepEqual(calls, [false, true, false]);
  s.onKey('up');
  assert.equal(s.focus, 2);

  const withThumbs = screen({ gateway: true, geo: true, thumbs: true }).s;
  withThumbs.onKey('down');
  assert.equal(withThumbs.focus, 4);
  withThumbs.onKey('down');
  assert.equal(withThumbs.focus, 6);
  withThumbs.onKey('down');
  assert.equal(withThumbs.focus, 6);
  withThumbs.onKey('up');
  assert.equal(withThumbs.focus, 4);

  const none = screen({ gateway: true, geo: false }).s;
  none.onKey('down');
  assert.equal(none.focus, 2); // nothing below the buttons
});
