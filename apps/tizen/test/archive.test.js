'use strict';
// "Archiv" (spec/apps.md "Archiv", spec/dvb-gateway.md "Archive") against spec/fixtures/archive_cases.json and
// answers in the spec's shapes.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT, json, response } = require('./load');

const A = CT.Archive;
const cases = json('archive_cases.json');
const NOW = Date.parse('2026-10-25T12:00:00Z');
const KEY = 'k'.repeat(40);

function memory(data) {
  let d = data || null;
  return { load: () => d, save: (x) => { d = JSON.parse(JSON.stringify(x)); }, peek: () => d };
}

function body(o) {
  return JSON.stringify(o);
}

const CARD = {
  id: 'ct:10195164142', source: 'both', title: 'Most!', kind: 'series', year: 2019,
  image: 'https://img.example.invalid/most.jpg', rating: { value: 8.36, votes: 1234 }, genres: ['Komedie'],
  episodeCount: 7
};

test('continueRow: golden cases', () => {
  for (const c of cases.continueRow) {
    assert.deepEqual(CT.ResumeRules.continueRow(c.entries), c.row, c.name);
  }
});

test('continueRow: at most 20, newest first, broken entries left out', () => {
  const entries = [];
  for (let i = 0; i < 25; i++) entries.push({ key: 'ct:' + i, savedAt: NOW + i * 1000, title: 'T' + i });
  entries.push({ key: 'ct:blank', savedAt: NOW + 99999, title: '  ' });
  entries.push({ key: 'ct:nodate', title: 'x' });
  entries.push(null);
  const row = CT.ResumeRules.continueRow(entries);
  assert.equal(row.length, 20);
  assert.equal(row[0], 'ct:24');
  assert.equal(row[19], 'ct:5');
});

test('resume store keeps the row fields, old entries keep working and stay out of the row', () => {
  let now = NOW;
  const backend = memory({
    // saved before T13: no title
    'ct:old': { progress: 600, length: 3600, savedAt: NOW - 1000 }
  });
  const store = new CT.ResumeStore(backend, () => now);
  assert.deepEqual(store.offer('ct:old'), { progress: 600, label: '10:00' });
  assert.deepEqual(store.continueRow(), []);

  now += 1000;
  store.save('dvb:b1-1-2', 300, 1800, { title: 'Mistři zastavárny VI (14)', showTitle: '', image: '/dvb/recording/b1-1-2/thumb.jpg', source: 'dvb' });
  now += 1000;
  store.save('ct:1', 700, 3180, CT.ResumeRules.meta('Most!', '1/26 Od začátku', 'https://img.example.invalid/a.jpg', 'ct'));
  const row = store.continueRow();
  assert.deepEqual(row.map((r) => r.key), ['ct:1', 'dvb:b1-1-2']);
  assert.deepEqual(row[0], {
    key: 'ct:1', title: '1/26 Od začátku', showTitle: 'Most!', image: 'https://img.example.invalid/a.jpg', source: 'ct',
    start: null, channel: null, progress: 700, length: 3180, label: '11:40'
  });
  assert.equal(backend.peek()['ct:1'].savedAt, now);

  // a later save without meta (an older code path) keeps the row fields
  now += 1000;
  store.save('ct:1', 800, 3180);
  assert.equal(backend.peek()['ct:1'].title, '1/26 Od začátku');
  assert.equal(backend.peek()['ct:1'].progress, 800);

  // the old entry gets the fields once it is saved from T13 on
  now += 1000;
  store.save('ct:old', 900, 3600, { title: 'Pelíšky' });
  assert.deepEqual(store.continueRow().map((r) => r.key), ['ct:old', 'ct:1', 'dvb:b1-1-2']);
  assert.equal(store.continueRow()[0].source, 'ct');

  // watched = gone from the row too; remove() = "Odebrat z řady"
  store.save('dvb:b1-1-2', 1750, 1800);
  store.remove('ct:1');
  assert.deepEqual(store.continueRow().map((r) => r.key), ['ct:old']);
  assert.equal(store.offer('ct:1'), null);

  // a non-finite progress never reaches the storage
  store.save('ct:nan', NaN, 3600, { title: 'x' });
  assert.equal(backend.peek()['ct:nan'], undefined);
});

test('resume meta: episode under its show, else the programme alone', () => {
  assert.deepEqual(CT.ResumeRules.meta('Most!', '1/26 Od začátku', 'i', 'ct'),
    { title: '1/26 Od začátku', showTitle: 'Most!', image: 'i', source: 'ct' });
  assert.deepEqual(CT.ResumeRules.meta('Zprávy', '', '', 'dvb'), { title: 'Zprávy', showTitle: '', image: '', source: 'dvb' });
});

test('guide requests carry the row fields', () => {
  const store = new CT.ResumeStore(memory(), () => NOW);
  const ch = CT.Channels.byId('CH_1');
  const entry = CT.Entries.ct(ch, { idec: 'a', title: 'Most!', episodeTitle: 'Díl 1', description: '', genre: '',
    imageUrl: 'https://img.example.invalid/m.jpg', start: NOW - 6 * 3600 * 1000, end: NOW - 5 * 3600 * 1000, isPlayableNow: true });
  const s = CT.Entries.select(entry, NOW, store);
  assert.deepEqual(s.request.resumeMeta, { title: 'Díl 1', showTitle: 'Most!', image: 'https://img.example.invalid/m.jpg', source: 'ct',
    start: NOW - 6 * 3600 * 1000, channel: 'CH_1' });
  const rec = CT.Entries.dvb({ id: 'c', name: 'Nova' }, { id: 'e', channelId: 'c', title: 'Film', subtitle: '', description: '',
    genre: '', start: NOW - 7200000, end: NOW - 3600000, recording: { id: 'bc-1-2', state: 'completed' } });
  assert.deepEqual(CT.Entries.select(rec, NOW, store).request.resumeMeta,
    { title: 'Film', showTitle: '', image: '/dvb/recording/bc-1-2/thumb.jpg', source: 'dvb' });
});

test('card parser: defensive, unknown values stay unknown', () => {
  assert.deepEqual(A.parseCard(CARD), {
    id: 'ct:10195164142', source: 'both', title: 'Most!', kind: 'series', year: 2019,
    image: 'https://img.example.invalid/most.jpg', rating: { value: 8.36, votes: 1234 }, genres: ['Komedie'],
    episodeCount: 7, durationMinutes: null, tmdb: false
  });
  const odd = A.parseCard({ id: 'rec:x', title: ' X ', source: 'cloud', kind: 'podcast', year: 'loni', rating: { value: 9, votes: 3 },
    genres: ['A', 4, ''], episodeCount: -2, durationMinutes: 95.4, image: 7, tmdb: 'yes' });
  assert.deepEqual(odd, { id: 'rec:x', source: null, title: 'X', kind: null, year: null, image: '', rating: null,
    genres: ['A'], episodeCount: null, durationMinutes: 95, tmdb: false });
  assert.equal(A.parseCard({ id: 'a', title: 'a', image: null, kind: null }).image, '');
  assert.equal(A.parseCard({ id: 'x' }), null);
  assert.equal(A.parseCard({ title: 'x' }), null);
  assert.equal(A.parseCard('x'), null);
});

test('card lines: year · kind, the rating rule of the programme page, source icons', () => {
  const lines = A.cardLines(A.parseCard(CARD));
  assert.deepEqual(lines, { title: 'Most!', meta: '2019 · seriál', rating: '★ 8,4', sources: ['ct', 'dvb'] });
  const film = A.cardLines(A.parseCard({ id: 'rec:a', source: 'rec', title: 'A', kind: 'movie', rating: { value: 7.35, votes: 20 } }));
  assert.deepEqual(film, { title: 'A', meta: 'film', rating: '★ 7,4', sources: ['dvb'] });
  // 19 votes: no rating (the same limit as "Hodnocení TMDB")
  const few = A.cardLines(A.parseCard({ id: 'ct:b', source: 'ct', title: 'B', year: 1999, rating: { value: 7, votes: 19 } }));
  assert.deepEqual(few, { title: 'B', meta: '1999', rating: null, sources: ['ct'] });
  assert.notDeepEqual(film, few);
  // a search result: ČT's searchShows gives no kind, year or genres
  const bare = A.cardLines(A.parseCard({ id: 'ct:s', source: 'ct', title: 'S', kind: null, rating: null }));
  assert.deepEqual(bare, { title: 'S', meta: '', rating: null, sources: ['ct'] });
  assert.deepEqual(A.SORTS.map((s) => s.title), ['Nejsledovanější', 'Naposledy vysílané', 'A–Z']);
  for (const c of json('programme_page_cases.json').rating) {
    const line = A.ratingShort({ value: c.value, votes: c.votes });
    assert.equal(line, c.line === null ? null : '★ ' + /TMDB (\S+) /.exec(c.line)[1], JSON.stringify(c));
  }
});

test('TMDb notice: the card\'s tmdb flag', () => {
  assert.equal(A.showsTmdb(A.parseCard(Object.assign({}, CARD, { tmdb: true }))), true);
  assert.equal(A.showsTmdb(A.parseCard(CARD)), false); // a rating alone does not decide it
  const home = response('dvb/dvb_archive_home.json');
  const cards = [].concat(...A.parseHome(home.status, home.body).rows.map((r) => r.cards));
  assert.deepEqual(cards.filter(A.showsTmdb).map((c) => c.title), ['Tichá hora', 'Noční vlak', 'Tichá hora', 'Tichá hora']);
});

test('images: CT URLs, the gateway poster and thumbnail paths only', () => {
  assert.deepEqual(A.image('https://img.example.invalid/a.jpg'),
    { key: 'ct:https://img.example.invalid/a.jpg', kind: 'ct', final: true, url: 'https://img.example.invalid/a.jpg' });
  assert.deepEqual(A.image('/dvb/details/image/w342/abc_D-1.jpg'),
    { key: 'gw:poster:/dvb/details/image/w342/abc_D-1.jpg', kind: 'gw', final: true, path: '/dvb/details/image/w342/abc_D-1.jpg' });
  assert.deepEqual(A.image('/dvb/recording/bnova1-1-2/thumb.jpg'),
    { key: 'gw:bnova1-1-2', kind: 'gw', final: true, recordingId: 'bnova1-1-2', thumb: true });
  for (const bad of ['http://img.example.invalid/a.jpg', '//evil/a.jpg', '/dvb/status', '/dvb/details/image/original/a.jpg',
    '/dvb/recording/../status/thumb.jpg', 'javascript:alert(1)', '', null, 5]) {
    assert.equal(A.image(bad), null, String(bad));
  }
});

test('search query: at least 2 characters, at most 100', () => {
  assert.equal(A.query(' a '), null);
  assert.equal(A.query('most'), 'most');
  assert.equal(A.query('  Most   pod '), 'Most pod');
  assert.equal(A.query('x'.repeat(150)).length, 100);
  assert.equal(A.DEBOUNCE_MS, 500);
});

test('attribution: in the answers that carry TMDb cards, never invented', () => {
  const parsed = {
    home: (r) => A.parseHome(r.status, r.body), category: (r) => A.parseCategory(r.status, r.body),
    search: (r) => A.parseSearch(r.status, r.body), show_movie: (r) => A.parseShow(r.status, r.body),
    show_series: (r) => A.parseShow(r.status, r.body), show_season_page: (r) => A.parseShow(r.status, r.body),
    show_ctdown: (r) => A.parseShow(r.status, r.body)
  };
  const seen = {};
  for (const name of Object.keys(parsed)) {
    const a = parsed[name](response('dvb/dvb_archive_' + name + '.json'));
    const cards = a.rows ? [].concat(...a.rows.map((r) => r.cards)) : a.cards || [a.card];
    seen[name] = [a.attribution === A.ATTRIBUTION, cards.some(A.showsTmdb)];
    assert.equal(a.attribution !== '', cards.some(A.showsTmdb), name);
  }
  assert.deepEqual(seen.home, [true, true]);
  assert.deepEqual(seen.search, [false, false]);
});

test('episodes of the shared samples: ČT ones with their channelId, ours never', () => {
  for (const name of ['show_series', 'show_movie', 'show_season_page', 'show_ctdown']) {
    const r = response('dvb/dvb_archive_' + name + '.json');
    for (const g of A.parseShow(r.status, r.body).groups) {
      for (const e of g.episodes) {
        if (e.source === 'rec') assert.equal(e.channelId, null, name);
        else assert.ok(/^CH_/.test(e.channelId), name + ' ' + e.idec);
      }
    }
  }
  const mv = response('dvb/dvb_archive_show_movie.json');
  const film = A.filmEpisode(A.parseShow(mv.status, mv.body));
  assert.deepEqual([film.channel, film.channelId], ['ČT2', 'CH_2']);
});

test('home, category and search: the shared gateway samples', () => {
  const h = response('dvb/dvb_archive_home.json');
  const home = A.parseHome(h.status, h.body);
  assert.deepEqual(home.rows.map((r) => [r.id, r.cards.length]), [['recordings', 3], ['3947-filmy', 2], ['3976-serialy', 2],
    ['4003-dokumenty', 1], ['4079-historie', 1], ['4106-priroda', 1], ['4055-rady-a-recepty', 1]]);
  assert.equal(home.error, '');
  // any https card image is a CT image (the gateway lets only ČT's through)
  assert.equal(A.image(home.rows[1].cards[0].image).kind, 'ct');
  const zahrada = home.rows[0].cards[0];
  assert.deepEqual([zahrada.id, zahrada.source, zahrada.kind, zahrada.year, zahrada.episodeCount, zahrada.tmdb],
    ['rec:zahradniserial', 'rec', 'series', null, 1, false]);
  assert.equal(A.image(zahrada.image).recordingId, 'b0b3e1d5a7c9f4e2a8b6d4c2e0f1a3b5c-1792294200-1792296900');
  assert.deepEqual(A.cardLines(home.rows[1].cards[0]), { title: 'Noční vlak', meta: '2019 · film', rating: '★ 6,4', sources: ['ct'] });
  const c = response('dvb/dvb_archive_category.json');
  const cat = A.parseCategory(c.status, c.body);
  assert.deepEqual([cat.title, cat.genres, cat.cards.map((x) => x.id), cat.nextPage],
    ['Filmy', [{ id: '3951', title: 'Drama' }], ['ct:10000000002', 'rec:tichahora'], null]);
  const q = response('dvb/dvb_archive_search.json');
  const found = A.parseSearch(q.status, q.body);
  assert.deepEqual([found.cards.map((x) => [x.id, x.source]), found.nextPage], [[['ct:10000000001', 'both']], null]);
  assert.deepEqual(A.cardLines(found.cards[0]).sources, ['ct', 'dvb']);
});

test('iVysílání not answering: 200 with the gateway\'s text, our recordings still listed', () => {
  const h = response('dvb/dvb_archive_home.json');
  const withError = JSON.parse(h.body);
  withError.error = 'iVysílání teď neodpovídá';
  withError.rows = withError.rows.slice(0, 1);
  const home = A.parseHome(200, body(withError));
  assert.deepEqual([home.error, home.rows.map((r) => r.id)], ['iVysílání teď neodpovídá', ['recordings']]);
  assert.equal(A.parseCategory(200, body({ title: 'Filmy', cards: [], error: 'iVysílání teď neodpovídá' })).error, 'iVysílání teď neodpovídá');
  assert.equal(A.parseSearch(200, body({ cards: [], error: 'iVysílání teď neodpovídá' })).error, 'iVysílání teď neodpovídá');
  // a show in both sources (the shared sample): 200 with only our recorded episodes and the text
  const cd = response('dvb/dvb_archive_show_ctdown.json');
  const both = A.parseShow(cd.status, cd.body);
  assert.deepEqual([cd.status, both.card.source, both.error, both.seasons, both.groups.map((g) => g.title)],
    [200, 'both', 'iVysílání teď neodpovídá', [], ['Nahráno z antény']]);
  assert.ok(both.groups[0].episodes.every((e) => e.source === 'rec' && e.channelId === null));
  assert.equal(A.parseShow(200, response('dvb/dvb_archive_show_series.json').body).error, '');
  // a ČT show page the gateway could not get: 502 with its text
  assert.throws(() => A.parseShow(502, body({ error: 'iVysílání teď neodpovídá' })), (e) => e.userMessage === 'iVysílání teď neodpovídá');
});

test('home, category and search answers: defensive', () => {
  const home = A.parseHome(200, body({ rows: [
    { id: 'recordings', title: 'Naše nahrávky', cards: [{ id: 'rec:a', source: 'rec', title: 'A' }], more: true },
    { id: '3947-filmy', title: 'Filmy', cards: [] },
    { id: '3976-serialy', title: 'Seriály', cards: [CARD, { broken: true }] },
    { title: 'no id', cards: [CARD] }
  ] }));
  assert.deepEqual(home.rows.map((r) => [r.id, r.cards.length, r.more]), [['recordings', 1, true], ['3976-serialy', 1, false]]);
  const cat = A.parseCategory(200, body({ title: 'Filmy', genres: [{ id: '4', title: 'Komedie' }, { id: '5' }],
    cards: [CARD], nextPage: 2 }));
  assert.deepEqual([cat.title, cat.genres, cat.cards.length, cat.nextPage], ['Filmy', [{ id: '4', title: 'Komedie' }], 1, 2]);
  assert.equal(A.parseCategory(200, body({ cards: [], nextPage: null })).nextPage, null);
  assert.deepEqual(A.parseSearch(200, body({ cards: [CARD], nextPage: 'x' })).nextPage, null);
  assert.throws(() => A.parseHome(401, '{}'), (e) => e.userMessage === CT.DvbMessages.BAD_KEY);
  assert.throws(() => A.parseHome(500, body({ error: 'ČT neodpovídá' })), (e) => e.userMessage === 'ČT neodpovídá');
  assert.throws(() => A.parseHome(200, 'not json'));
});

test('show page: the shared series and film samples', () => {
  const sr = response('dvb/dvb_archive_show_series.json');
  const series = A.parseShow(sr.status, sr.body);
  assert.deepEqual(series.seasons.map((x) => x.title), ['I. řada', 'II. řada']);
  assert.deepEqual(series.groups.map((g) => [g.title, g.season, g.nextPage, g.episodes.length]), [
    ['I. řada', 'season:00000000000000000000aa01', 2, 20], ['Nahráno z antény', null, null, 1]]);
  assert.deepEqual(series.creators, ['Režie: Jan Příklad. Hrají: Eva Vzorová, Petr Ukázka a další.']);
  assert.deepEqual(A.showFacts(series), ['iVysílání a z antény', 'seriál', '52 dílů']);
  const first = series.groups[0].episodes[0];
  assert.equal(A.episodeLine(first, NOW), '1/26 Smyšlený díl 1 · 52 min · ČT1 1. 4.');
  const r = A.episodeRequest(first, series);
  assert.deepEqual([r.kind, r.channelId, r.idec, r.resumeKey, r.resumeMeta.start, r.resumeMeta.channel],
    ['programme', 'CH_1', '208522161400001', 'ct:208522161400001', Date.parse('2026-04-01T18:10:00Z'), 'CH_1']);
  const rec = A.episodeRequest(series.groups[1].episodes[0], series);
  assert.deepEqual([rec.kind, rec.recordingId, rec.resumeMeta.start], ['dvbRecorded',
    'b0b3e1d5a7c9f4e2a8b6d4c2e0f1a3b5c-1792294200-1792296900', undefined]);

  const mv = response('dvb/dvb_archive_show_movie.json');
  const movie = A.parseShow(mv.status, mv.body);
  assert.deepEqual([movie.card.kind, movie.groups.map((g) => [g.title, g.season, g.nextPage])],
    ['movie', [['Díly', null, null]]]);
  assert.deepEqual(A.showFacts(movie), ['iVysílání', '2019', 'film', 'Česko, Slovensko']);
  assert.equal(A.filmEpisode(movie).idec, '22638149399');
  assert.notDeepEqual(series.groups, movie.groups);
});

test('show paging: another season replaces the iVysílání group, a next page adds to its group', () => {
  const sr = response('dvb/dvb_archive_show_series.json');
  const groups = A.parseShow(sr.status, sr.body).groups;
  // a later page of the same season (no shared sample for it): appended, its nextPage taken over
  const page2 = A.parseShow(200, body({ card: { id: 'ct:1', title: 'Z' }, groups: [{ title: 'I. řada',
    season: 'season:00000000000000000000aa01', nextPage: null, episodes: [{ source: 'ct', idec: '21', title: '21/26' }] }] })).groups;
  const paged = A.withPage(groups, 0, page2);
  assert.deepEqual(paged.map((g) => [g.title, g.nextPage, g.episodes.length]), [['I. řada', null, 21], ['Nahráno z antény', null, 1]]);
  assert.equal(paged[0].episodes[20].idec, '21');
  assert.equal(groups[0].episodes.length, 20); // the old list is not changed
  // another season: the shared sample of ?season=…aa02&page=1 replaces the iVysílání group, ours stay once
  const sp = response('dvb/dvb_archive_show_season_page.json');
  const season2 = A.parseShow(sp.status, sp.body);
  const switched = A.withSeason(groups, season2.groups);
  assert.deepEqual(switched.map((g) => [g.title, g.season, g.nextPage, g.episodes.length]),
    [['II. řada', 'season:00000000000000000000aa02', null, 6], ['Nahráno z antény', null, null, 1]]);
  assert.equal(switched[0].episodes[0].idec, '208522161500021');
  assert.notDeepEqual(switched[0], groups[0]);
});

const SHOW = {
  card: CARD, description: 'Popis.', creators: ['Jan Prušinovský'], countries: ['Česko'],
  seasons: [{ id: 's1', title: '1. řada' }, { id: 's2', title: '2. řada' }],
  groups: [
    { title: 'iVysílání', season: 's1', nextPage: 2, episodes: [
      { source: 'ct', idec: '219 562 22021/0001', title: '1/7 Od začátku', description: 'd', durationSeconds: 3180,
        aired: '2026-01-09T19:20:00Z', channel: 'ČT1', playable: true },
      { source: 'ct', title: 'no idec' },
      { source: 'ct', idec: '2', title: '2/7 Bez kanálu', durationSeconds: 3000, aired: '2019-01-16T19:20:00Z',
        channel: 'Zahraniční kanál', playable: false }
    ] },
    { title: 'Nahráno z antény', episodes: [
      { source: 'rec', recordingId: 'bct1-1792000000-1792003000', title: 'Most! (3)', durationSeconds: 3000,
        aired: '2026-10-20T19:20:00Z', channel: 'ČT1 HD', playable: true }
    ] },
    { title: 'empty', episodes: [] }
  ]
};

test('show answer: episodes defensive, empty groups left out', () => {
  const show = A.parseShow(200, body(SHOW));
  assert.equal(show.card.title, 'Most!');
  assert.deepEqual(show.creators, ['Jan Prušinovský']);
  assert.deepEqual(show.seasons.map((s) => s.id), ['s1', 's2']);
  assert.deepEqual(show.groups.map((g) => [g.title, g.season, g.nextPage, g.episodes.length]),
    [['iVysílání', 's1', 2, 2], ['Nahráno z antény', null, null, 1]]);
  assert.deepEqual(show.groups[0].episodes[0], {
    source: 'ct', idec: '219 562 22021/0001', recordingId: null, title: '1/7 Od začátku', description: 'd',
    durationSeconds: 3180, aired: Date.parse('2026-01-09T19:20:00Z'), channel: 'ČT1', channelId: 'CH_1', playable: true
  });
  assert.deepEqual(A.showFacts(show), ['iVysílání a z antény', '2019', 'seriál', 'Česko', '7 dílů']);
  assert.throws(() => A.parseShow(200, body({ groups: [] })));
  const minimal = A.parseShow(200, body({ card: { id: 'rec:x', title: 'X', source: 'rec' }, creators: 'Někdo' }));
  assert.deepEqual([minimal.creators, minimal.countries, minimal.seasons, minimal.groups],
    [['Někdo'], [], [], []]);
});

test('episode channel: the gateway\'s channelId first, else the display name, else none (VOD only)', () => {
  const ep = (extra) => A.parseEpisode(Object.assign({ source: 'ct', idec: '1', title: 'T', durationSeconds: 600,
    aired: '2026-10-25T10:00:00Z', channel: 'ČT1', playable: true }, extra));
  assert.equal(ep({ channelId: 'CH_24' }).channelId, 'CH_24'); // the id wins over the name
  assert.equal(ep({ channelId: null }).channelId, 'CH_1'); // null: the display name decides
  assert.equal(ep({ channelId: 'CH_99' }).channelId, 'CH_1'); // unknown id: the display name too
  assert.equal(ep({}).channelId, 'CH_1'); // field missing
  assert.equal(ep({ channelId: null, channel: 'Zahraniční kanál' }).channelId, null); // neither known: VOD only
  assert.equal(A.parseEpisode({ source: 'rec', recordingId: 'r', channelId: 'CH_1' }).channelId, null);
  const show = A.parseShow(200, body({ card: { id: 'ct:1', title: 'S' } }));
  assert.deepEqual([A.episodeRequest(ep({ channelId: 'CH_24' }), show).channelId,
    A.episodeRequest(ep({ channelId: null, channel: '' }), show).kind], ['CH_24', 'archive']);
});

test('Czech plurals of episodes and the episode line', () => {
  assert.deepEqual([1, 2, 4, 5, 11, 22, 0].map(A.episodesWord), ['díl', 'díly', 'díly', 'dílů', 'dílů', 'dílů', 'dílů']);
  // the spec's texts: "1 díl / 2–4 díly / 5 dílů" in the facts
  const facts = (n) => A.showFacts({ card: A.parseCard({ id: 'a', title: 'a', episodeCount: n }), countries: [] }).join();
  assert.deepEqual([1, 2, 4, 5].map(facts), ['1 díl', '2 díly', '4 díly', '5 dílů']);
  const show = A.parseShow(200, body(SHOW));
  const [first, second] = show.groups[0].episodes;
  assert.equal(A.episodeLine(first, NOW), '1/7 Od začátku · 53 min · ČT1 9. 1.');
  assert.equal(A.episodeLine(second, NOW), '2/7 Bez kanálu · 50 min · Zahraniční kanál 16. 1. 2019');
  // Prague day, not UTC: 23:30 UTC on 31 Dec is 1 Jan in Prague
  assert.equal(A.shortDate(Date.parse('2025-12-31T23:30:00Z'), NOW), '1. 1.');
  assert.equal(A.episodeLine({ title: 'X', durationSeconds: null, channel: '', aired: null }, NOW), 'X');
});

test('episode requests: guide path with a CT channel, idec only without one, recordings through the Anténa player', () => {
  const show = A.parseShow(200, body(SHOW));
  const [first, second] = show.groups[0].episodes;
  const rec = show.groups[1].episodes[0];
  const r1 = A.episodeRequest(first, show);
  assert.deepEqual(r1, {
    kind: 'programme', channelId: 'CH_1', title: 'Most!', idec: '219 562 22021/0001',
    start: Date.parse('2026-01-09T19:20:00Z'), end: Date.parse('2026-01-09T20:13:00Z'), isPlayableNow: true,
    episodeTitle: '1/7 Od začátku', resumeKey: 'ct:219 562 22021/0001', lengthSeconds: 3180,
    resumeMeta: { title: '1/7 Od začátku', showTitle: 'Most!', image: 'https://img.example.invalid/most.jpg', source: 'ct',
      start: Date.parse('2026-01-09T19:20:00Z'), channel: 'CH_1' }
  });
  const r2 = A.episodeRequest(second, show, 600);
  assert.deepEqual([r2.kind, r2.idec, r2.channelName, r2.start, r2.resumeSeconds],
    ['archive', '2', 'Zahraniční kanál', Date.parse('2019-01-16T19:20:00Z'), 600]);
  // no CT channel: nothing to decide by, so no start or channel kept for the row either
  assert.deepEqual([r2.resumeMeta.start, r2.resumeMeta.channel], [undefined, undefined]);
  const r3 = A.episodeRequest(rec, show);
  assert.deepEqual([r3.kind, r3.recordingId, r3.fromStart, r3.resumeKey, r3.resumeMeta.source, r3.channelName],
    ['dvbRecorded', 'bct1-1792000000-1792003000', false, 'dvb:bct1-1792000000-1792003000', 'dvb', 'ČT1 HD']);
  assert.equal(A.filmEpisode(show), first);
  // a film: its first iVysílání episode even when not marked playable (ČT's text says why), else its first episode
  const unplayable = A.parseShow(200, body({ card: { id: 'ct:f', title: 'F', kind: 'movie' }, groups: [
    { title: 'Nahráno z antény', episodes: [{ source: 'rec', recordingId: 'r1', title: 'F', playable: true }] },
    { title: 'Díly', episodes: [{ source: 'ct', idec: 'x', title: 'F', playable: false }] }] }));
  assert.equal(A.filmEpisode(unplayable).idec, 'x');
  unplayable.groups.pop();
  assert.equal(A.filmEpisode(unplayable).recordingId, 'r1');
  assert.equal(A.ctChannel('ct24').id, 'CH_24');
  assert.equal(A.ctChannel('ČT sport').id, 'CH_4');
  assert.equal(A.ctChannel('Nova'), null);
});

test('continue row card plays at once from its position', () => {
  const ct = A.continueRequest({ key: 'ct:1/2', title: '1/26 Od začátku', showTitle: 'Most!', image: '', source: 'ct',
    start: null, channel: null, progress: 700, length: 3180 });
  assert.deepEqual([ct.kind, ct.idec, ct.title, ct.episodeTitle, ct.resumeSeconds, ct.resumeKey, ct.lengthSeconds],
    ['archive', '1/2', 'Most!', '1/26 Od začátku', 700, 'ct:1/2', 3180]);
  const start = NOW - 3600 * 1000;
  const kept = A.continueRequest({ key: 'ct:9', title: 'Zprávy', showTitle: '', image: '', source: 'ct',
    start: start, channel: 'CH_24', progress: 700, length: 1800 });
  assert.deepEqual([kept.kind, kept.channelId, kept.idec, kept.start, kept.end, kept.resumeSeconds, kept.resumeMeta.start],
    ['programme', 'CH_24', '9', start, start + 1800 * 1000, 700, start]);
  const dvb = A.continueRequest({ key: 'dvb:b1-1-2', title: 'Zprávy', showTitle: '', image: '', source: 'dvb', progress: 90, length: 1800 });
  assert.deepEqual([dvb.kind, dvb.recordingId, dvb.title, dvb.episodeTitle, dvb.resumeSeconds],
    ['dvbRecorded', 'b1-1-2', 'Zprávy', '', 90]);
});

test('row resume within 3 h of the start plays from the buffer at the position, later from the archive', async () => {
  const backend = memory();
  let now = NOW;
  const store = new CT.ResumeStore(backend, () => now);
  const start = NOW - 3600 * 1000;
  store.save('ct:9', 700, 1800, CT.ResumeRules.meta('Zprávy', '', '', 'ct', start, 'CH_24'));
  assert.deepEqual([backend.peek()['ct:9'].start, backend.peek()['ct:9'].channel], [start, 'CH_24']);
  // an unknown channel or a DVB entry keeps neither
  store.save('ct:x', 700, 1800, { title: 'x', source: 'ct', start: start, channel: 'CH_99' });
  store.save('dvb:y', 700, 1800, { title: 'y', source: 'dvb', start: start, channel: 'CH_1' });
  assert.deepEqual([backend.peek()['ct:x'].start, backend.peek()['dvb:y'].channel], [undefined, undefined]);
  const item = store.continueRow().filter((r) => r.key === 'ct:9')[0];
  const planner = (vod) => new CT.Planner(new CT.CtApi((req) =>
    Promise.resolve(req.url.indexOf('/media/external/') >= 0 ? response(vod) : response('live_CH_1.json'))), null, () => now);
  const buffer = await planner('vod_ok.json').plan(A.continueRequest(item));
  assert.deepEqual([buffer.kind, buffer.seekToWallClock], ['timeshift', start + 700 * 1000]);
  now = NOW + 5 * 3600 * 1000;
  const vod = await planner('vod_ok.json').plan(A.continueRequest(item));
  assert.deepEqual([vod.kind, vod.startPositionMs], ['vod', 700000]);
});

test('planner: archive kind = VOD by idec at the position, CT text on errors, still live → buffer', async () => {
  const fixtures = require('./load').response;
  const now = NOW;
  const plan = (vod, request) => new CT.Planner(new CT.CtApi((req) =>
    Promise.resolve(req.url.indexOf('/media/external/') >= 0 ? fixtures(vod) : fixtures('live_CH_1.json'))), null, () => now)
    .plan(request);
  const t = await plan('vod_ok.json', { kind: 'archive', idec: '1', title: 'Most!', resumeSeconds: 700 });
  assert.deepEqual([t.kind, t.startPositionMs], ['vod', 700000]);
  await assert.rejects(plan('vod_drm.json', { kind: 'archive', idec: '1', title: 'x' }), (e) => e.status === 'UNSUPPORTED_DRM');
  const still = await plan('vod_still_live.json', { kind: 'archive', idec: '1', title: 'x', start: now - 600000 });
  assert.equal(still.kind, 'timeshift');
  const live = await plan('vod_still_live.json', { kind: 'archive', idec: '1', title: 'x' });
  assert.equal(live.kind, 'live');
});

test('client: paths and the key header', async () => {
  const seen = [];
  const client = new CT.ArchiveClient(new CT.DvbClient(new CT.DvbConfig('http://pi.local:8098', KEY), (req) => {
    seen.push(req);
    return Promise.resolve({ status: 200, body: body(req.url.indexOf('/show/') >= 0 ? SHOW : { rows: [], cards: [] }) });
  }));
  await client.home();
  await client.category('3947-filmy', 'newest', '4', 2);
  await client.category('recordings', null, null, null);
  await client.search('Most & spol', 1);
  await client.show('ct:10195164142');
  await client.show('rec:mistrizastavarny', 's/2', 3);
  assert.deepEqual(seen.map((r) => r.url.replace('http://pi.local:8098', '')), [
    '/dvb/archive',
    '/dvb/archive/category/3947-filmy?sort=newest&genre=4&page=2',
    '/dvb/archive/category/recordings?sort=popular&page=1',
    '/dvb/archive/search?q=Most%20%26%20spol&page=1',
    '/dvb/archive/show/ct:10195164142',
    '/dvb/archive/show/rec:mistrizastavarny?season=s%2F2&page=3'
  ]);
  assert.ok(seen.every((r) => r.headers.Authorization === 'Bearer ' + KEY));
});

test('client: a transport that throws at once is a rejected promise (the page does not get stuck loading)', async () => {
  const client = new CT.ArchiveClient(new CT.DvbClient(new CT.DvbConfig('http://pi.local:8098', KEY), () => {
    throw new Error('boom');
  }));
  await assert.rejects(client.show('ct:1', 's', 2));
  await assert.rejects(client.home());
});
