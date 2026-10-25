'use strict';
// The Archiv menu (spec/apps.md "Archiv", layout T23) against spec/fixtures/archive_cases.json "menu" and the
// categories answer of GET /dvb/archive/categories (spec/dvb-gateway.md "Archive").
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { CT, json } = require('./load');

const M = CT.ArchiveMenu;
const cases = json('archive_cases.json');
const KEY = 'k'.repeat(40);
const CATEGORIES_FIXTURE = path.join(__dirname, '..', '..', '..', 'spec', 'fixtures', 'dvb', 'dvb_archive_categories.json');

function body(o) {
  return JSON.stringify(o);
}

test('menu: every golden case, step by step', () => {
  assert.ok(cases.menu.length >= 6);
  for (const c of cases.menu) {
    let state = null;
    c.steps.forEach((step, i) => {
      const where = `${c.name} / step ${i + 1} (${step.do}${step.on ? ' ' + step.on : ''})`;
      if (step.do === 'open') {
        state = M.open(c.categories, c.continueEntries, c.genres);
      } else if (step.do === 'ok') {
        const r = M.ok(state, step.on);
        assert.equal(r.focus, step.focus || null, where + ': focus');
        state = r.state;
      } else if (step.do === 'back') {
        const r = M.back(state);
        assert.equal(r.leave, step.leave === true, where + ': leave');
        if (r.leave) return;
        state = r.state;
      } else {
        assert.fail(where + ': unknown step');
      }
      assert.deepEqual(M.view(state), step.state, where);
    });
  }
});

const CATS = [{ id: '3947-filmy', title: 'Filmy' }, { id: '3976-serialy', title: 'Seriály' }];
const GENRES = { '3947-filmy': [{ id: '4012', title: 'Krimi' }, { id: '4013', title: 'Komedie' }] };

test('menu: the item titles of the main menu and of a sub-menu', () => {
  const main = M.open(CATS, 0, GENRES);
  assert.deepEqual(main.items.map((i) => i.title), ['Hledat', 'Pokračovat ve sledování', 'Naše nahrávky', 'Filmy', 'Seriály']);
  const sub = M.ok(main, '3947-filmy').state;
  assert.deepEqual(sub.items.map((i) => i.title), ['‹ Filmy', 'Všechny', 'Krimi', 'Komedie']);
});

test('menu: resting focus selects (the back item selects nothing), unknown ids change nothing', () => {
  const main = M.open(CATS, 0, GENRES);
  const moved = M.select(main, 'recordings');
  assert.deepEqual(moved.grid, { show: 'recordings' });
  assert.equal(moved.level, 'main');
  assert.equal(M.select(main, 'nonsense'), main);
  const search = M.select(main, 'search');
  assert.deepEqual(search.grid, { show: 'search' });
  const sub = M.ok(main, '3947-filmy').state;
  assert.equal(M.select(sub, 'back'), sub);
  assert.deepEqual(M.select(sub, '4013').grid, { show: 'category', category: '3947-filmy', genre: '4013' });
  assert.equal(M.selectedIndex(sub), 1);
  // moving the focus in the main menu onto a category does not open its sub-menu
  assert.equal(M.select(main, '3976-serialy').level, 'main');
});

test('menu: without categories (they did not load) it opens on Naše nahrávky, or Pokračovat with an entry', () => {
  assert.equal(M.open([], 0).selected, 'recordings');
  assert.equal(M.open(null, 0).selected, 'recordings');
  assert.equal(M.open([], 3).selected, 'continue');
  assert.deepEqual(M.view(M.open([], 0)).items, ['search', 'continue', 'recordings']);
});

test('menu: sub-genres arriving later fill the open sub-menu, the selection kept when still there', () => {
  let s = M.ok(M.open(CATS, 0), '3947-filmy').state;
  assert.deepEqual(M.view(s).items, ['back', 'all']);
  s = M.withGenres(s, '3947-filmy', [{ id: '4012', title: 'Krimi' }, { bad: 1 }, { id: '4012', title: 'again' }]);
  assert.deepEqual(M.view(s).items, ['back', 'all', '4012']);
  s = M.ok(s, '4012').state;
  // another category's genres: stored, the open sub-menu unchanged
  const other = M.withGenres(s, '3976-serialy', [{ id: '9', title: 'Sitcom' }]);
  assert.deepEqual(M.view(other).items, ['back', 'all', '4012']);
  assert.equal(other.selected, '4012');
  // the selected sub-genre vanished: Všechny
  const gone = M.withGenres(s, '3947-filmy', [{ id: '4013', title: 'Komedie' }]);
  assert.equal(gone.selected, 'all');
  assert.deepEqual(gone.grid, { show: 'category', category: '3947-filmy', genre: null });
  // the stored genres open the next sub-menu at once
  const back = M.back(other).state;
  const serialy = M.ok(back, '3976-serialy').state;
  assert.deepEqual(M.view(serialy).items, ['back', 'all', '9']);
});

test('menu: sort chips and their default, kept in the sub-menu, reset for another category', () => {
  let s = M.open(CATS, 0, GENRES);
  assert.equal(s.sort, 'popular');
  assert.deepEqual(M.sorts(s).map((x) => x.title), ['Nejsledovanější', 'Naposledy vysílané', 'A–Z']);
  s = M.withSort(s, 'az');
  assert.equal(s.sort, 'az');
  assert.equal(M.withSort(s, 'bogus'), s);
  s = M.ok(s, '3947-filmy').state;
  assert.equal(s.sort, 'az', 'kept when the sub-menu opens');
  s = M.ok(s, '4012').state;
  assert.equal(s.sort, 'az', 'kept for a sub-genre');
  s = M.back(s).state;
  assert.equal(s.sort, 'az', 'kept back on the category');
  s = M.select(s, '3976-serialy');
  assert.equal(s.sort, 'popular', 'reset for another category');
  s = M.select(s, 'recordings');
  assert.equal(s.sort, 'newest');
  assert.deepEqual(M.sorts(s).map((x) => [x.id, x.title]), [['newest', 'Nejnovější'], ['az', 'A–Z']]);
  assert.equal(M.withSort(s, 'popular'), s, 'our recordings have no Nejsledovanější');
  assert.deepEqual(M.sorts(M.select(s, 'continue')), []);
  assert.deepEqual(M.sorts(M.select(s, 'search')), []);
});

test('menu: the grid request and the empty texts', () => {
  const s = M.open(CATS, 0, GENRES);
  assert.deepEqual(M.request(s), { kind: 'category', slug: '3947-filmy', genre: null, sort: 'popular' });
  const krimi = M.ok(M.ok(s, '3947-filmy').state, '4012').state;
  assert.deepEqual(M.request(krimi), { kind: 'category', slug: '3947-filmy', genre: '4012', sort: 'popular' });
  const rec = M.select(s, 'recordings');
  assert.deepEqual(M.request(rec), { kind: 'category', slug: 'recordings', genre: null, sort: 'newest' });
  assert.deepEqual(M.request(M.select(s, 'continue')), { kind: 'continue' });
  assert.deepEqual(M.request(M.select(s, 'search')), { kind: 'search' });
  assert.equal(M.emptyText(M.select(s, 'continue')), 'Zatím nemáte nic rozkoukaného');
  assert.equal(M.emptyText(rec), 'Zatím žádné nahrávky');
  assert.equal(M.emptyText(s), 'Nic nenalezeno');
  assert.equal(M.emptyText(krimi), 'Nic nenalezeno');
  assert.equal(M.FOCUS_DEBOUNCE_MS, 400);
});

test('categories answer: the shape of the spec, defensive', () => {
  const answer = { categories: [
    { id: '3947-filmy', title: 'Filmy' }, { id: '3976-serialy', title: 'Seriály' }, { id: '', title: 'x' },
    { id: 'y' }, null, 'z', { id: '3947-filmy', title: 'Again' }, { id: '4732-archiv', title: ' Archiv ' }
  ] };
  assert.deepEqual(M.parseCategories(200, body(answer)), [
    { id: '3947-filmy', title: 'Filmy' }, { id: '3976-serialy', title: 'Seriály' }, { id: '4732-archiv', title: 'Archiv' }
  ]);
  assert.deepEqual(M.parseCategories(200, body({})), []);
  assert.throws(() => M.parseCategories(401, body({ error: 'Neplatný klíč' })));
  assert.throws(() => M.parseCategories(200, 'not json'));
});

test('categories answer: the shared gateway sample (14, Archiv last)', { skip: !fs.existsSync(CATEGORIES_FIXTURE) && 'spec/fixtures/dvb/dvb_archive_categories.json not there yet' }, () => {
  const file = json('dvb/dvb_archive_categories.json');
  const raw = file.body !== undefined ? file.body : file; // wrapped as {status, body} like the other samples, or bare
  const cats = M.parseCategories(file.status || 200, body(raw));
  assert.equal(cats.length, 14);
  assert.equal(cats[0].title, 'Filmy');
  assert.equal(cats[cats.length - 1].title, 'Archiv');
  const s = M.open(raw.categories, 0);
  assert.equal(s.selected, cats[0].id);
  assert.equal(s.items.length, 17);
});

test('client: categories path with the key header', async () => {
  const seen = [];
  const client = new CT.ArchiveClient(new CT.DvbClient(new CT.DvbConfig('http://pi.local:8098', KEY), (req) => {
    seen.push(req);
    return Promise.resolve({ status: 200, body: body({ categories: [{ id: '3947-filmy', title: 'Filmy' }] }) });
  }));
  assert.deepEqual(await client.categories(), [{ id: '3947-filmy', title: 'Filmy' }]);
  assert.equal(seen[0].url, 'http://pi.local:8098/dvb/archive/categories');
  assert.equal(seen[0].headers.Authorization, 'Bearer ' + KEY);
});
