'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { CT, json, response } = require('./load');

const cases = json('programme_page_cases.json');
const MIN = 60 * 1000;
const NOW = Date.parse('2026-10-24T18:00:00Z');
const ct1 = CT.Channels.byId('CH_1');

function memoryStore(entries) {
  let data = entries || {};
  return new CT.ResumeStore({ load: () => data, save: (d) => { data = d; } }, () => NOW);
}

function ctEntry(startMin, endMin, isPlayableNow) {
  return CT.Entries.ct(ct1, {
    idec: '226 452 10001/0001', title: 'Pořad', episodeTitle: '', description: '', genre: '', imageUrl: '',
    start: NOW + startMin * MIN, end: NOW + endMin * MIN, isPlayableNow: isPlayableNow
  });
}

function dvbEntry(startMin, endMin, recState) {
  const channel = { id: 'ch-nova', name: 'Nova' };
  return CT.Entries.dvb(channel, {
    id: 'ev 42', channelId: 'ch-nova', title: 'Film', start: NOW + startMin * MIN, end: NOW + endMin * MIN,
    subtitle: '', description: '', genre: '', recording: recState ? { id: 'rec-1', state: recState } : null
  });
}

for (const c of cases.buttons) {
  test(`buttons: ${c.name}`, () => {
    assert.deepStrictEqual(CT.Programme.buttons(c, true), c.buttons);
    // Samsung: no "Otevřít v iVysílání", otherwise the same list
    assert.deepStrictEqual(CT.Programme.buttons(c, false), c.buttons.filter((b) => b !== 'openOfficialApp'));
  });
}

for (const c of cases.rating) {
  test(`rating line: ${c.value} / ${c.votes}`, () => {
    assert.strictEqual(CT.Programme.ratingLine({ value: c.value, votes: c.votes }), c.line);
  });
}

for (const c of cases.plural) {
  test(`plural: ${c.n} ${c.word}`, () => {
    assert.strictEqual(CT.Programme.votesWord(c.n), c.word);
  });
}

test('rating line: no rating, broken values', () => {
  assert.strictEqual(CT.Programme.ratingLine(null), null);
  assert.strictEqual(CT.Programme.ratingLine({ value: '7', votes: 100 }), null);
  assert.strictEqual(CT.Programme.ratingLine({ value: 6.35, votes: 20 }), 'Hodnocení TMDB 6,4 (20 hlasů)');
  assert.strictEqual(CT.Programme.ratingLine({ value: 9.96, votes: 20 }), 'Hodnocení TMDB 10,0 (20 hlasů)');
});

test('facts of real entries match the golden inputs', () => {
  const P = CT.Programme;
  assert.deepStrictEqual(P.facts(ctEntry(-10, 50, false), NOW), { source: 'ct', state: 'airing', startable: true, playable: false });
  assert.strictEqual(P.facts(ctEntry(-60, -30, false), NOW).playable, true); // in the 3 h buffer
  assert.strictEqual(P.facts(ctEntry(-300, -240, false), NOW).playable, false); // archive, not playable
  assert.strictEqual(P.facts(ctEntry(-300, -240, true), NOW).playable, true);
  assert.strictEqual(P.facts(ctEntry(10, 60, true), NOW).state, 'future');
  assert.strictEqual(P.facts(dvbEntry(-10, 50, 'recording'), NOW).startable, true);
  assert.strictEqual(P.facts(dvbEntry(-10, 50, 'scheduled'), NOW).startable, false);
  assert.strictEqual(P.facts(dvbEntry(-10, 50, null), NOW).startable, false);
  assert.strictEqual(P.facts(dvbEntry(-60, -10, 'completed'), NOW).playable, true);
  assert.strictEqual(P.facts(dvbEntry(-60, -10, 'failed'), NOW).playable, false);
});

test('actions: the old choice as a list, with requests', () => {
  const P = CT.Programme;
  const ids = (list) => list.map((b) => b.id);
  const airing = P.actions(ctEntry(-10, 50, false), NOW, memoryStore());
  assert.deepStrictEqual(ids(airing), ['live', 'startOver']); // Živě first (T26)
  assert.strictEqual(airing[1].label, 'Od začátku');
  assert.strictEqual(airing[1].request.kind, 'startover');
  assert.strictEqual(airing[0].request.kind, 'live');

  const resumed = P.actions(ctEntry(-60, -30, false), NOW, memoryStore({
    'ct:226 452 10001/0001': { progress: 600, length: 1800, savedAt: NOW }
  }));
  assert.deepStrictEqual(ids(resumed), ['resume', 'startOver']);
  assert.strictEqual(resumed[0].label, 'Pokračovat od 10:00');
  assert.strictEqual(resumed[0].request.resumeSeconds, 600);
  assert.strictEqual(resumed[1].request.resumeSeconds, undefined);

  // not marked playable: still offered, playing shows CT's own text (the old behaviour)
  const unavailable = P.actions(ctEntry(-300, -240, false), NOW, memoryStore());
  assert.deepStrictEqual(ids(unavailable), ['startOver']);
  assert.strictEqual(unavailable[0].request.kind, 'programme');
  assert.strictEqual(unavailable[0].request.isPlayableNow, false);
  assert.deepStrictEqual(ids(P.actions(ctEntry(10, 60, true), NOW, memoryStore())), []);
  assert.deepStrictEqual(ids(P.actions(dvbEntry(-10, 50, null), NOW, memoryStore())), ['live']);
  assert.deepStrictEqual(ids(P.actions(dvbEntry(-10, 50, 'recording'), NOW, memoryStore())), ['live', 'startOver']);
  assert.deepStrictEqual(ids(P.actions(dvbEntry(-60, -10, 'completed'), NOW, memoryStore())), ['startOver']);
  assert.deepStrictEqual(ids(P.actions(dvbEntry(-60, -10, null), NOW, memoryStore())), []);
  // a remembered position of a failed recording that is airing: no resume without a start-over source
  const failed = P.actions(dvbEntry(-10, 50, 'failed'), NOW, memoryStore({ 'dvb:rec-1': { progress: 300, length: 3600, savedAt: NOW } }));
  assert.deepStrictEqual(ids(failed), ['live']);
});

test('state line says what the buttons do or why there are none', () => {
  const P = CT.Programme;
  assert.strictEqual(P.stateLine(ctEntry(10, 60, true), NOW), 'Ještě nezačalo');
  assert.strictEqual(P.stateLine(ctEntry(-10, 50, true), NOW), 'Právě běží – od začátku nebo živě');
  assert.strictEqual(P.stateLine(ctEntry(-60, -30, false), NOW), 'Zpět v čase');
  assert.strictEqual(P.stateLine(ctEntry(-300, -240, true), NOW), 'Přehrát ze záznamu');
  assert.strictEqual(P.stateLine(ctEntry(-300, -240, false), NOW), 'Ze záznamu zřejmě nedostupné');
  assert.strictEqual(P.stateLine(dvbEntry(-10, 50, null), NOW), 'Právě běží – živě');
  assert.strictEqual(P.stateLine(dvbEntry(-10, 50, 'recording'), NOW), 'Právě běží – od začátku nebo živě');
  assert.strictEqual(P.stateLine(dvbEntry(-60, -10, 'completed'), NOW), 'Přehrát záznam');
  assert.strictEqual(P.stateLine(dvbEntry(-60, -10, null), NOW), 'Pořad nebyl nahrán');
});

test('details path: programme ids only, CT by slug, day and idec', () => {
  const P = CT.Programme;
  assert.strictEqual(P.detailsPath(dvbEntry(0, 60, null), '2026-10-24'), '/dvb/details?source=dvb&id=ev%2042');
  assert.strictEqual(P.detailsPath(ctEntry(0, 60, true), '2026-10-24'),
    '/dvb/details?source=ct&channel=ct1&date=2026-10-24&idec=226%20452%2010001%2F0001');
  assert.strictEqual(P.detailsPath(ctEntry(0, 60, true), null), null);
  const psp = CT.Entries.psp({ id: 'stream1', name: 'PS 1' }, 'live', true, NOW, NOW + 60 * MIN);
  assert.strictEqual(P.detailsPath(psp, '2026-10-24'), null);
});

const ATTRIBUTION = 'This application uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB.';

test('details parser: the shared gateway fixtures', () => {
  const P = CT.Programme;
  const m = response('dvb/dvb_details_match.json');
  const d = P.parseDetails(m.status, m.body);
  assert.strictEqual(d.attribution, ATTRIBUTION);
  assert.deepStrictEqual(d.match, {
    kind: 'movie', title: 'Hněv Titánů', originalTitle: 'Wrath of the Titans', year: 2012,
    overview: 'Smyšlený popis filmu.', genres: ['Akční'], runtimeMinutes: 99, rating: { value: 5.8, votes: 7311 },
    poster: '/dvb/details/image/w342/titanyPoster01.jpg', tmdbId: 4401
  });
  assert.strictEqual(P.ratingLine(d.match.rating), 'Hodnocení TMDB 5,8 (7 311 hlasů)');
  assert.strictEqual(P.originalTitleLine(d.match, 'Hněv Titánů'), 'Původní název: Wrath of the Titans');
  const n = response('dvb/dvb_details_nomatch.json');
  const none = P.parseDetails(n.status, n.body);
  assert.deepStrictEqual(none, { match: null, attribution: ATTRIBUTION });
  assert.notDeepStrictEqual(none, d); // the two fixtures really differ
});

test('details parser: edge cases, 404, garbage', () => {
  const P = CT.Programme;
  const body = {
    match: {
      kind: 'movie', title: 'Pelíšky', originalTitle: 'Pelíšky', year: 1999, overview: 'Vánoce 1967.',
      genres: ['Komedie', 7, 'Drama'], runtimeMinutes: 115, rating: { value: 8.1, votes: 1234 },
      poster: '/dvb/details/image/w342/abc_DEF-1.jpg', tmdbId: 1234
    },
    attribution: ATTRIBUTION
  };
  const d = P.parseDetails(200, JSON.stringify(body));
  assert.strictEqual(d.attribution, ATTRIBUTION);
  assert.deepStrictEqual(d.match.genres, ['Komedie', 'Drama']);
  assert.strictEqual(d.match.year, 1999);
  assert.strictEqual(d.match.poster, '/dvb/details/image/w342/abc_DEF-1.jpg');
  assert.deepStrictEqual(d.match.rating, { value: 8.1, votes: 1234 });

  // a poster anywhere else (another host, another size, a path trick) is dropped
  for (const poster of ['https://image.tmdb.org/t/p/w342/abc.jpg', '/dvb/details/image/original/abc.jpg',
    '/dvb/details/image/w342/../../status.jpg', '/dvb/details/image/w342/abc.gif']) {
    const other = P.parseDetails(200, JSON.stringify({ match: Object.assign({}, body.match, { poster }) }));
    assert.strictEqual(other.match.poster, null, poster);
  }
  const few = P.parseDetails(200, JSON.stringify({ match: Object.assign({}, body.match, { rating: { value: 9, votes: 19 } }) }));
  assert.strictEqual(few.match.rating, null);

  assert.deepStrictEqual(P.parseDetails(200, JSON.stringify({ match: null, attribution: ATTRIBUTION })),
    { match: null, attribution: ATTRIBUTION });
  assert.deepStrictEqual(P.parseDetails(200, JSON.stringify({ match: null, error: 'TMDb neodpovídá' })),
    { match: null, attribution: '' });
  assert.strictEqual(P.parseDetails(404, JSON.stringify({ error: 'Podrobnosti nejsou zapnuté' })), null);
  assert.strictEqual(P.parseDetails(200, 'not json'), null);
});

test('details client: key header, 404 and network errors are no details', async () => {
  const config = new CT.DvbConfig('http://pi.local:8098', 'k'.repeat(40));
  const seen = [];
  let answer = { status: 200, body: JSON.stringify({ match: null, attribution: ATTRIBUTION }) };
  const transport = (request) => {
    seen.push(request);
    return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer);
  };
  const client = new CT.DvbClient(config, transport);
  const entry = dvbEntry(-60, -10, 'completed');
  assert.deepStrictEqual(await CT.Programme.fetchDetails(client, entry, '2026-10-24'), { match: null, attribution: ATTRIBUTION });
  assert.strictEqual(seen[0].url, 'http://pi.local:8098/dvb/details?source=dvb&id=ev%2042');
  assert.strictEqual(seen[0].headers.Authorization, 'Bearer ' + 'k'.repeat(40));
  answer = { status: 404, body: '{"error": "Podrobnosti nejsou zapnuté"}' };
  assert.strictEqual(await CT.Programme.fetchDetails(client, entry, '2026-10-24'), null);
  answer = new Error('network');
  assert.strictEqual(await CT.Programme.fetchDetails(client, entry, '2026-10-24'), null);
  assert.strictEqual(await CT.Programme.fetchDetails(null, entry, '2026-10-24'), null); // no gateway
  assert.strictEqual(seen.length, 3);
});

test('original title only when it differs', () => {
  const P = CT.Programme;
  assert.strictEqual(P.originalTitleLine({ title: 'Pelíšky', originalTitle: 'Pelíšky' }, 'Pelíšky'), '');
  assert.strictEqual(P.originalTitleLine({ title: 'Sám doma', originalTitle: 'Home Alone' }, 'Sám doma'),
    'Původní název: Home Alone');
  assert.strictEqual(P.originalTitleLine(null, 'x'), '');
  assert.strictEqual(P.minutes({ start: 0, end: 110 * MIN }), 110);
});
