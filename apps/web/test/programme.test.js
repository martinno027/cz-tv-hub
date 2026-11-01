'use strict';
// "Otevřít v iVysílání" on the web's programme page (spec/web.md): the shared golden buttons with the official app
// on, on real entries; Samsung (off) keeps its list.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

const MIN = 60 * 1000;
const NOW = Date.parse('2026-10-24T18:00:00Z');
const store = new CT.ResumeStore({ load: () => ({}), save: () => {} }, () => NOW);

function ctEntry(startMin, endMin, isPlayableNow) {
  return CT.Entries.ct(CT.Channels.byId('CH_1'), {
    idec: '226 452 10001/0001', title: 'Pořad', episodeTitle: '', description: '', genre: '', imageUrl: '',
    start: NOW + startMin * MIN, end: NOW + endMin * MIN, isPlayableNow: isPlayableNow
  });
}

test('web: started ČT programmes end with "Otevřít v iVysílání" (no request; the page opens iVysílání)', () => {
  const ids = (list) => list.map((b) => b.id);
  const airing = CT.Programme.actions(ctEntry(-10, 50, false), NOW, store, true);
  assert.deepEqual(ids(airing), ['live', 'startOver', 'openOfficialApp']);
  const official = airing[2];
  assert.equal(official.label, 'Otevřít v iVysílání');
  assert.equal(official.request, null);
  assert.deepEqual(ids(CT.Programme.actions(ctEntry(-300, -240, false), NOW, store, true)), ['startOver', 'openOfficialApp']);
  assert.deepEqual(ids(CT.Programme.actions(ctEntry(10, 60, true), NOW, store, true)), []); // future: none
});

test('Samsung: the same entries without it (the parameter left out, as before)', () => {
  const ids = (list) => list.map((b) => b.id);
  assert.deepEqual(ids(CT.Programme.actions(ctEntry(-10, 50, false), NOW, store)), ['live', 'startOver']);
  assert.deepEqual(ids(CT.Programme.actions(ctEntry(-300, -240, false), NOW, store, false)), ['startOver']);
});
