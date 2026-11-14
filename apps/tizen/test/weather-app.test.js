'use strict';
// Weather in the app shell (spec/apps.md "Location for weather", T59): the location only with the switch on and a
// location source, the question only with a gateway and a source and before a decision, only the choice stored, the
// location never (the web's HTTPS rule: apps/web/test/web-weather.test.js). CT.App's own methods on fakes.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT, response } = require('./load');

const stored = {};
global.window = global;
global.localStorage = {
  getItem: (k) => (k in stored ? stored[k] : null),
  setItem: (k, v) => { stored[k] = String(v); },
  removeItem: (k) => { delete stored[k]; }
};
global.document = { hidden: false, getElementById: () => null, addEventListener: () => {} };
global.addEventListener = () => {};
require('../app/js/ui/platform.js');
require('../app/js/ui/keys.js');
require('../app/js/ui/settings-store.js');
require('../app/js/ui/weather.js');
require('../app/js/ui/app.js');
CT.Demo = { enabled: false };

const PRAGUE = { coords: { latitude: 50.0755, longitude: 14.4378 } };

function geo(answer) {
  const g = { asked: 0 };
  g.getCurrentPosition = (ok, fail) => {
    g.asked++;
    if (answer) ok(answer);
    else fail({ code: 1 });
  };
  return g;
}

function shell(opts) {
  const app = Object.create(CT.App);
  const calls = [];
  app.tab = 'home';
  app.screens = { home: { render: () => calls.push('home:render') } };
  app.dvbConfig = opts.gateway === false ? null : {};
  app.dvbRepo = opts.gateway === false ? null : {
    client: { get: (path) => { calls.push(path); return Promise.resolve(response('dvb/dvb_weather_ok.json')); } }
  };
  app.geolocation = () => opts.geo || null;
  app.weather = app.makeWeather();
  return { app, calls };
}

function reset(state) {
  Object.keys(stored).forEach((k) => delete stored[k]);
  if (state) stored['cztvhub.weatherLocation'] = state;
}

test('the question: with a gateway and a location source, until Povolit or Nepoužívat', () => {
  reset();
  assert.equal(CT.WeatherLocationSwitch.state(), 'ask');
  assert.equal(shell({ geo: geo(PRAGUE) }).app.weatherConsentDue(), true);
  assert.equal(shell({ geo: null }).app.weatherConsentDue(), false); // no location source: nothing to ask
  assert.equal(shell({ geo: geo(PRAGUE), gateway: false }).app.weatherConsentDue(), false);
  reset('off');
  assert.equal(shell({ geo: geo(PRAGUE) }).app.weatherConsentDue(), false);
  reset('on');
  assert.equal(shell({ geo: geo(PRAGUE) }).app.weatherConsentDue(), false);
});

test('Povolit: only the choice is stored, the rounded location goes once to the gateway', async () => {
  reset();
  const g = geo(PRAGUE);
  const { app, calls } = shell({ geo: g });
  app.setWeatherLocation(true);
  await app.weather.busy;
  assert.deepEqual(stored, { 'cztvhub.weatherLocation': 'on' });
  assert.deepEqual(calls, ['/dvb/weather?lat=50.1&lon=14.4', 'home:render']);
  assert.equal(g.asked, 1);
  assert.ok(!JSON.stringify(stored).includes('50.'), 'no coordinates in storage');
});

test('Nepoužívat or the switch off: the OS is never asked, the household place', async () => {
  reset();
  const g = geo(PRAGUE);
  const { app, calls } = shell({ geo: g });
  app.setWeatherLocation(false);
  await app.weather.busy;
  assert.equal(CT.WeatherLocationSwitch.state(), 'off');
  assert.deepEqual(calls, ['/dvb/weather', 'home:render']);
  assert.equal(g.asked, 0);
  reset(); // undecided (Return on the question): not asked either
  const undecided = shell({ geo: g });
  await undecided.app.weather.refresh();
  assert.deepEqual(undecided.calls, ['/dvb/weather', 'home:render']);
  assert.equal(g.asked, 0);
});

test('refused or no location on the TV: the household place', async () => {
  reset('on');
  const refused = shell({ geo: geo(null) });
  await refused.app.weather.refresh();
  assert.deepEqual(refused.calls, ['/dvb/weather', 'home:render']);
  const broken = shell({ geo: { getCurrentPosition: () => { throw new Error('no provider'); } } });
  await broken.app.weather.refresh();
  assert.deepEqual(broken.calls, ['/dvb/weather', 'home:render']);
});

test('switching it on in Nastavení asks again at once while Domů is on screen, else on the next Domů', async () => {
  reset('off');
  const { app, calls } = shell({ geo: geo(PRAGUE) });
  await app.weather.refresh();
  app.tab = 'settings';
  app.setWeatherLocation(true);
  assert.equal(app.weather.answeredAt, null); // forgotten: due on the next show
  assert.deepEqual(calls, ['/dvb/weather', 'home:render']);
  app.tab = 'home';
  await app.weather.refresh();
  assert.equal(calls[2], '/dvb/weather?lat=50.1&lon=14.4');
});

test('the demo keeps the answer in memory, never in the browser store the real web app shares', () => {
  reset();
  CT.Demo = { enabled: true };
  try {
    assert.equal(CT.WeatherLocationSwitch.state(), 'ask');
    CT.WeatherLocationSwitch.set(true);
    assert.equal(CT.WeatherLocationSwitch.state(), 'on');
    assert.deepEqual(stored, {});
  } finally {
    CT.Demo = { enabled: false };
  }
  assert.equal(CT.WeatherLocationSwitch.state(), 'ask');
});
