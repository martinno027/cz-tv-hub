'use strict';
// Weather on Domů (spec/apps.md "Domů", T59; spec/dvb-gateway.md "Weather"): the parser against the gateway's three
// fixtures, the location rounding against weather_cases.json, the texts of the header and the week cards, and when
// the app asks again.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT, response, json } = require('./load');

const W = CT.Weather;

function parse(name) {
  const r = response('dvb/' + name);
  return W.parse(r.status, r.body);
}

test('parser: the ok fixture as the gateway sends it', () => {
  const w = parse('dvb_weather_ok.json');
  assert.deepEqual(w.now, { temperature: 8.4, text: 'Polojasno', icon: 'partly' });
  assert.deepEqual(w.sun, { rise: '07:13', set: '16:19' });
  assert.equal(w.days.length, 7);
  assert.deepEqual(w.days[1], { date: '2026-11-15', text: 'Slabý déšť', icon: 'rain', max: 8.0, min: 3.5, rainChance: 60 });
  assert.deepEqual(w.days.map((d) => d.icon), ['partly', 'rain', 'cloud', 'sun', 'fog', 'snow', 'storm']);
  assert.equal(w.aqiText, 'zhoršené');
  assert.deepEqual(w.pollen, [{ name: 'olše', level: 'nízká' }]);
  assert.deepEqual(w.warnings, [{ level: 'yellow', event: 'Silný vítr', to: Date.parse('2026-11-14T17:00:00Z') },
    { level: 'orange', event: 'Riziko požárů', to: null }]); // "to": null = no end announced
  assert.equal(w.attribution, 'Počasí: Open-Meteo.com (CC BY 4.0), CAMS · Výstrahy: ČHMÚ (CC BY 4.0)');
});

test('parser: 404 (weather off) and 200 with error give nothing to show; a 401 or a broken body throws', () => {
  assert.equal(parse('dvb_weather_off.json'), null);
  assert.equal(parse('dvb_weather_error.json'), null);
  assert.throws(() => W.parse(401, '{"error": "x"}'), (e) => e.status === '401');
  assert.throws(() => W.parse(200, 'not json'));
  assert.throws(() => W.parse(500, '{"error": "Chyba"}'));
});

test('parser: unknown fields ignored, missing optional ones tolerated, bad entries dropped', () => {
  const body = JSON.parse(response('dvb/dvb_weather_ok.json').body);
  body.extra = { anything: 1 };
  body.now.feelsLike = 3;
  body.now.icon = 'hurricane'; // not one of the documented keys
  delete body.sun.set;
  body.air = null;
  body.days = [{ date: '2026-11-14' }, { date: 'zítra', max: 1 }, 7, { date: '2026-11-15', max: 4.6, rainChance: 19.6 }];
  body.warnings = [{ level: 'purple', event: 'Mráz' }, { level: 'red' }, { level: 'orange', event: 'Bouřky', to: 'nonsense' }];
  delete body.attribution;
  const w = W.parse(200, JSON.stringify(body));
  assert.equal(w.now.icon, 'cloud');
  assert.equal(w.sun, null);
  assert.equal(w.aqiText, null);
  assert.deepEqual(w.pollen, []);
  assert.deepEqual(w.days, [
    { date: '2026-11-14', text: null, icon: 'cloud', max: null, min: null, rainChance: null },
    { date: '2026-11-15', text: null, icon: 'cloud', max: 4.6, min: null, rainChance: 20 }]);
  assert.deepEqual(w.warnings, [{ level: null, event: 'Mráz', to: null }, { level: 'orange', event: 'Bouřky', to: null }]);
  assert.equal(w.attribution, null);
  // without "now" (or its temperature) there is nothing for the header
  delete body.now.temperature;
  assert.equal(W.parse(200, JSON.stringify(body)), null);
  assert.equal(W.parse(200, '{"place": {"lat": 50.1, "lon": 14.4}}'), null);
});

test('location rounding: weather_cases.json "round" (0.1°, half away from zero, null outside or not a number)', () => {
  const cases = json('weather_cases.json').round;
  assert.ok(cases.length >= 10);
  for (const c of cases) {
    const lat = typeof c.lat === 'string' ? Number(c.lat) : c.lat; // "NaN", "inf", "abc" → not a finite number
    const lon = typeof c.lon === 'string' ? Number(c.lon) : c.lon;
    assert.deepEqual(W.round(lat, lon), c.out, JSON.stringify(c));
    assert.deepEqual(W.round(c.lat, c.lon), c.out, 'as given: ' + JSON.stringify(c));
  }
  // every two-decimal tie in the range rounds up, as the gateway's decimal rounding does
  for (let i = 4805; i <= 5145; i += 10) assert.equal(W.round(i / 100, 15)[0], ((i + 5) / 10) / 10, String(i / 100));
  for (let i = 1155; i <= 1945; i += 10) assert.equal(W.round(50, i / 100)[1], ((i + 5) / 10) / 10, String(i / 100));
  assert.equal(W.roundHalfAway(-2.5, 0), -3);
  assert.ok(Object.is(W.roundHalfAway(-0.04, 1), 0));
});

test('request path: only the rounded place, or nothing', () => {
  assert.equal(W.path(null), '/dvb/weather');
  assert.equal(W.path(W.round(50.0755, 14.4378)), '/dvb/weather?lat=50.1&lon=14.4');
  assert.equal(W.path(W.round(50.04, 15.0)), '/dvb/weather?lat=50.0&lon=15.0');
});

test('header texts: now, sun, air, pollen, warnings in Prague time', () => {
  const w = parse('dvb_weather_ok.json');
  assert.equal(W.nowLine(w), '8 °C, polojasno');
  assert.equal(W.sunLine(w), 'Slunce 7:13–16:19');
  assert.equal(W.airLine(w), 'Ovzduší: zhoršené');
  assert.equal(W.pollenLine(w), 'Pyl: olše nízká');
  const today = Date.parse('2026-11-14T10:00:00Z');
  // 17:00Z in November = 18:00 in Prague; in summer 15:00Z = 17:00
  assert.equal(W.warningLine(w.warnings[0], today), 'Výstraha ČHMÚ: Silný vítr do 18:00');
  assert.equal(W.warningLine(w.warnings[1], today), 'Výstraha ČHMÚ: Riziko požárů (do odvolání)'); // to: null
  assert.equal(W.warningLine({ event: 'Vedro', to: Date.parse('2026-07-01T15:00:00Z') }, Date.parse('2026-07-01T08:00:00Z')),
    'Výstraha ČHMÚ: Vedro do 17:00');
  // another Prague day: weekday and date too
  assert.equal(W.warningLine({ event: 'Silný vítr', to: Date.parse('2026-11-16T17:00:00Z') }, today),
    'Výstraha ČHMÚ: Silný vítr do po 16. 11. 18:00');
  assert.equal(W.warningLine({ event: 'Mráz', to: Date.parse('2026-11-15T06:05:00Z') }, today), 'Výstraha ČHMÚ: Mráz do ne 15. 11. 7:05');
  // 23:30Z on the 14th is already the 15th in Prague
  assert.equal(W.warningLine({ event: 'Mráz', to: Date.parse('2026-11-14T23:30:00Z') }, today), 'Výstraha ČHMÚ: Mráz do ne 15. 11. 0:30');
  assert.equal(W.warningLine({ event: 'Mráz', to: Date.parse('2026-11-14T22:30:00Z') }, today), 'Výstraha ČHMÚ: Mráz do 23:30');
  const two = Object.assign({}, w, { pollen: [{ name: 'bříza', level: 'střední' }, { name: 'tráva', level: 'nízká' }] });
  assert.equal(W.pollenLine(two), 'Pyl: bříza střední, tráva nízká');
  const bare = Object.assign({}, w, { now: { temperature: -0.4, text: null, icon: 'cloud' }, sun: null, aqiText: null, pollen: [] });
  assert.equal(W.nowLine(bare), '0 °C');
  assert.equal(W.sunLine(bare), null);
  assert.equal(W.airLine(bare), null);
  assert.equal(W.pollenLine(bare), null);
});

test('warnings: in the gateway\'s order, never sorted by level or end', () => {
  const body = JSON.parse(response('dvb/dvb_weather_ok.json').body);
  body.warnings = [
    { level: 'yellow', event: 'A', to: '2026-11-14T20:00:00Z' },
    { level: 'red', event: 'B', to: '2026-11-14T13:00:00Z' },
    { level: 'orange', event: 'C', to: null },
    { level: 'red', event: 'D', to: '2026-11-14T18:00:00Z' }];
  const w = W.parse(200, JSON.stringify(body));
  assert.deepEqual(W.currentWarnings(w, Date.parse('2026-11-14T10:00:00Z')).map((x) => x.event), ['A', 'B', 'C', 'D']);
});

test('warnings: those already over are not shown', () => {
  const w = parse('dvb_weather_ok.json');
  assert.deepEqual(W.currentWarnings(w, Date.parse('2026-11-14T16:59:00Z')).map((x) => x.event), ['Silný vítr', 'Riziko požárů']);
  assert.deepEqual(W.currentWarnings(w, Date.parse('2026-11-14T17:00:00Z')).map((x) => x.event), ['Riziko požárů']);
});

test('week cards: Dnes / "út 4. 11.", "9° / 2°", "srážky 60 %" only from 20 %', () => {
  const w = parse('dvb_weather_ok.json');
  const today = '2026-11-14';
  assert.deepEqual(w.days.map((d) => W.dayLabel(d.date, today)),
    ['Dnes', 'ne 15. 11.', 'po 16. 11.', 'út 17. 11.', 'st 18. 11.', 'čt 19. 11.', 'pá 20. 11.']);
  assert.equal(W.dayLabel('2026-11-04', '2026-11-03'), 'st 4. 11.'); // no "Zítra" here
  assert.equal(W.dayLabel('2026-11-03', '2026-11-01'), 'út 3. 11.');
  assert.deepEqual(w.days.map(W.rangeLine), ['9° / 2°', '8° / 4°', '7° / 4°', '7° / -1°', '5° / -2°', '2° / -3°', '12° / 6°']);
  assert.deepEqual(w.days.map(W.rainLine), [null, 'srážky 60 %', 'srážky 30 %', null, null, 'srážky 70 %', 'srážky 80 %']);
  assert.equal(W.rainLine({ rainChance: 19 }), null);
  assert.equal(W.rainLine({ rainChance: 20 }), 'srážky 20 %');
  assert.equal(W.rainLine({ rainChance: null }), null);
  assert.equal(W.rangeLine({ max: 2.5, min: null }), '3°');
  assert.equal(W.rangeLine({ max: null, min: -2.5 }), '-3°');
  assert.equal(W.rangeLine({ max: null, min: null }), null);
});

test('asked again only after 30 minutes (or a clock that went back)', () => {
  const t = 1e12;
  assert.equal(W.due(null, t), true);
  assert.equal(W.due(t, t + 29 * 60e3), false);
  assert.equal(W.due(t, t + 30 * 60e3), true);
  assert.equal(W.due(t, t - 1000), true);
});

function source(opts) {
  const calls = [];
  let now = 1e12;
  const answer = opts.answer || response('dvb/dvb_weather_ok.json');
  const client = { get: (path) => { calls.push(path); return opts.fail ? Promise.reject(new Error('down')) : Promise.resolve(answer); } };
  const s = new CT.WeatherSource({
    client: () => (opts.noGateway ? null : client),
    locate: opts.locate || (() => Promise.resolve(null)),
    clock: () => now
  });
  return { s, calls, advance: (ms) => { now += ms; } };
}

test('source: the rounded location only, or none; one request at a time; again after 30 min', async () => {
  const { s, calls, advance } = source({ locate: () => Promise.resolve([50.0755, 14.4378]) });
  let changes = 0;
  s.onChange = () => { changes++; };
  const a = s.refresh();
  assert.equal(s.refresh(), a); // the same request
  const w = await a;
  assert.equal(w.now.text, 'Polojasno');
  assert.deepEqual(calls, ['/dvb/weather?lat=50.1&lon=14.4']);
  assert.equal(changes, 1);
  await s.refresh();
  advance(29 * 60e3);
  await s.refresh();
  assert.equal(calls.length, 1);
  advance(60e3);
  await s.refresh();
  assert.equal(calls.length, 2);
  await s.refresh(true);
  assert.equal(calls.length, 3);
});

test('source: no location (none, refused, outside the gateway range) = the household place; failures show nothing', async () => {
  for (const locate of [() => Promise.resolve(null), () => Promise.reject(new Error('denied')), () => Promise.resolve([52.52, 13.4])]) {
    const { s, calls } = source({ locate });
    await s.refresh();
    assert.deepEqual(calls, ['/dvb/weather']);
    assert.ok(s.weather);
  }
  const down = source({ fail: true });
  assert.equal(await down.s.refresh(), null);
  assert.notEqual(down.s.answeredAt, null); // an answer too: not asked again at once
  const off = source({ answer: response('dvb/dvb_weather_off.json') });
  assert.equal(await off.s.refresh(), null);
  const none = source({ noGateway: true });
  assert.equal(await none.s.refresh(), null);
  assert.deepEqual(none.calls, []);
});

test('source: an answer for a gateway that was replaced meanwhile is dropped', async () => {
  const { s } = source({});
  const pending = s.refresh();
  s.reset();
  assert.equal(await pending, null);
  assert.equal(s.weather, null);
  assert.equal(s.answeredAt, null);
});

test('golden "temperature": half away from zero, never -0, ASCII hyphen; now "8 °C", cards "9°"', () => {
  const cases = json('weather_cases.json').temperature;
  assert.ok(cases.length >= 8);
  for (const c of cases) {
    assert.equal(W.nowLine({ now: { temperature: c.value, text: null } }), c.now, String(c.value));
    assert.equal(W.rangeLine({ max: c.value, min: null }), c.card, String(c.value));
  }
  assert.equal(W.rangeLine({ max: 9.1, min: 2.3 }), '9° / 2°');
});

test('golden "weekday": Dnes, else "út 17. 11."', () => {
  const g = json('weather_cases.json').weekday;
  for (const c of g.cases) assert.equal(W.dayLabel(c.date, c.today), c.label, c.date);
  // the names in Monday-first order: 2026-11-16 is a Monday
  assert.deepEqual([0, 1, 2, 3, 4, 5, 6].map((i) => W.weekdayDate(CT.Days.addDays('2026-11-16', i)).split(' ')[0]), g.names);
});
