'use strict';
// Domů's header (spec/apps.md "Domů", T58) against spec/fixtures/calendar_cases.json and namedays_cz.json.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT, json } = require('./load');

const C = CT.Calendar;
const cases = json('calendar_cases.json');
const namedays = json('namedays_cz.json');

test('embedded tables equal the fixtures (name days, holidays, school holidays, words)', () => {
  assert.deepEqual(C.NAMEDAYS, namedays.days);
  assert.equal(Object.keys(C.NAMEDAYS).length, 366);
  assert.deepEqual(C.STATE_HOLIDAYS, cases.stateHolidays);
  assert.deepEqual(C.EASTER_HOLIDAYS, cases.easterHolidays);
  assert.deepEqual(C.SCHOOL_HOLIDAYS, cases.schoolHolidays);
  assert.deepEqual(C.WEEKDAYS, cases.weekdays);
  assert.deepEqual(C.MONTHS, cases.monthsGenitive);
});

test('Easter Sunday by the computus', () => {
  for (const [year, date] of Object.entries(cases.easter)) assert.equal(C.easter(Number(year)), date, year);
});

test('clock changes: last Sunday of March and of October', () => {
  for (const [year, dates] of Object.entries(cases.clockChange)) assert.deepEqual(C.clockChanges(Number(year)), dates, year);
});

for (const year of [2026, 2027]) {
  test('moon: every new and full moon of ' + year + ' on its Prague day, and no other day', () => {
    const list = cases['moon' + year];
    assert.ok(list.length >= 24, 'moon' + year);
    assert.deepEqual(C.moonPhases(year), list);
    const listed = new Map(list.map((m) => [m.date, m.phase]));
    let days = 0;
    for (let t = Date.UTC(year, 0, 1); t < Date.UTC(year + 1, 0, 1); t += 864e5, days++) {
      const day = new Date(t).toISOString().slice(0, 10);
      assert.equal(C.moon(day), listed.get(day) || null, day);
    }
    assert.equal(days, 365);
  });
}

test('header: full date, name line and at most two calendar lines', () => {
  assert.ok(cases.cases.length >= 30);
  for (const c of cases.cases) {
    assert.deepEqual(C.header(c.date), { fullDate: c.fullDate, nameLine: c.nameLine, lines: c.lines }, c.date);
  }
});

test('today: the Prague calendar day of the moment, not the UTC one', () => {
  // 23:30 UTC on 3 November = 0:30 on 4 November in Prague (winter time)
  assert.equal(C.today(Date.parse('2026-11-03T23:30:00Z')).fullDate, 'středa 4. listopadu');
  // 22:30 UTC on 3 July = 0:30 on 4 July (summer time)
  assert.equal(C.today(Date.parse('2026-07-03T22:30:00Z')).fullDate, 'sobota 4. července');
});

test('moon: the Prague day, not the UTC one (full moon 4 Dec 2025 23:14 UTC = 5 Dec 0:14 in Prague)', () => {
  assert.equal(C.moon('2025-12-05'), 'full');
  assert.equal(C.moon('2025-12-04'), null);
});

test('moon: the shared instant "moonInstant" to the second (E on the sin M term, TT taken as UT)', () => {
  const m = cases.moonInstant;
  const near = Date.parse(m.near + 'T12:00:00Z');
  // the phase of that kind closest to the day (k + 0.5 = full moon)
  const k0 = Math.round((near / 864e5 + 2440587.5 - 2451550.09766) / 29.530588861) + (m.phase === 'full' ? 0.5 : 0);
  const k = [k0 - 1, k0, k0 + 1].sort((a, b) => Math.abs(C.phaseMs(a) - near) - Math.abs(C.phaseMs(b) - near))[0];
  assert.equal(k, m.k); // the same phase every app compares
  const got = C.phaseMs(m.k);
  assert.ok(Math.abs(got - Date.parse(m.utc)) <= 1000, new Date(got).toISOString() + ' vs ' + m.utc);
});
