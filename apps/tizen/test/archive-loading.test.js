'use strict';
// spec/apps.md "Archiv" → Layout, "Loading is always visible (T52)": which part of the archive gets the loader, after
// 150 ms, its old content kept (dimmed), a newer choice replacing the waited one; on the shared screens (show.js,
// archive.js, also the web's) over a minimal DOM, the gateway answering when the test says so.
const test = require('node:test');
const { mock } = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

const A = CT.Archive;

function classList(node) {
  const names = () => node.className.split(/\s+/).filter(Boolean);
  return {
    add: (c) => { if (!names().includes(c)) node.className = names().concat(c).join(' '); },
    remove: (c) => { node.className = names().filter((n) => n !== c).join(' '); },
    toggle: (c, on) => { (on === undefined ? !names().includes(c) : on) ? node.classList.add(c) : node.classList.remove(c); },
    contains: (c) => names().includes(c)
  };
}

class Node {
  constructor(tag) {
    this.tagName = tag;
    this.className = '';
    this.textContent = '';
    this.children = [];
    this.style = {};
    this.dataset = {};
    this.parentNode = null;
    this.clientHeight = 0;
    this.clientWidth = 0;
    this.offsetLeft = 0;
    this.offsetWidth = 0;
    this.scrollLeft = 0;
    this.value = '';
    this.classList = classList(this);
  }
  get childNodes() { return this.children; }
  get firstChild() { return this.children[0] || null; }
  get lastChild() { return this.children[this.children.length - 1] || null; }
  set innerHTML(v) { this.children.forEach((c) => { c.parentNode = null; }); this.children = []; }
  get innerHTML() { return ''; }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); child.parentNode = null; return child; }
  addEventListener() {}
  focus() {}
  blur() {}
  querySelector(sel) {
    const cls = sel.replace(/^\./, '');
    for (const c of this.children) {
      if (c.classList.contains(cls)) return c;
      const inner = c.querySelector(sel);
      if (inner) return inner;
    }
    return null;
  }
}

const byId = {};
global.window = global;
global.document = {
  activeElement: null,
  getElementById: (id) => byId[id] || (byId[id] = new Node('div')),
  createElement: (tag) => new Node(tag),
  createTextNode: (text) => { const n = new Node('#text'); n.textContent = text; return n; }
};
const $ = (id) => document.getElementById(id);
$('sh-episodes').appendChild(new Node('div')); // its .ar-track
$('sh-tmdb').appendChild(new Node('span'));
$('ar-side').appendChild(new Node('div'));
$('ar-tmdb').appendChild(new Node('span'));
$('ar-grid').appendChild(new Node('div')); // .ar-track
const note = $('ar-grid').appendChild(new Node('div'));
note.className = 'ar-note';
CT.sourceIcon = (source) => { const n = new Node('span'); n.className = 'source-icon ' + source; return n; };
CT.DvbMessages = CT.DvbMessages || { NOT_RECORDED: 'Nenahráno' };
require('../app/js/ui/archive-cards.js');
require('../app/js/ui/show.js');
require('../app/js/ui/archive.js');

const tick = () => new Promise((resolve) => setImmediate(resolve));
/** The loader named `name` in `box`, or null. */
const loaderIn = (box, name) => box.childNodes.filter((c) => c.classList.contains('loader-' + name))[0] || null;
const has = (id, cls) => $(id).classList.contains(cls);

/** A gateway whose answers wait until the test resolves them: `calls` = [{what, args, ok(body), fail()}]. */
function gateway() {
  const calls = [];
  const call = (what, parse) => (...args) => new Promise((resolve, reject) => {
    calls.push({ what, args, ok: (body) => resolve(parse(body)), fail: () => reject({ userMessage: 'Nepovedlo se' }) });
  });
  return {
    calls,
    last: (what) => calls.filter((c) => c.what === what).slice(-1)[0],
    client: {
      show: call('show', (b) => A.parseShow(200, JSON.stringify(b))),
      categories: call('categories', (list) => list),
      category: call('category', (b) => A.parseCategory(200, JSON.stringify(b))),
      search: call('search', (b) => A.parseSearch(200, JSON.stringify(b)))
    }
  };
}

function episodes(tag, n) {
  return Array.from({ length: n }, (_, i) => ({ source: 'ct', idec: tag + '-' + i, title: tag + ' ' + i,
    durationSeconds: 60, aired: '2025-06-01T18:00:00Z', channel: 'ČT1', playable: true }));
}

const SEASONS = [{ id: 's1', title: '1. řada' }, { id: 's2', title: '2. řada' }, { id: 's3', title: '3. řada' }];

function showBody(tag, extra) {
  return Object.assign({ card: { id: 'ct:1', title: 'Pořad', kind: 'series', source: 'ct' }, description: 'Popis',
    creators: '', countries: [], seasons: SEASONS,
    groups: [{ title: 'Díly', season: 's1', nextPage: 2, episodes: episodes(tag, 8) }] }, extra || {});
}

const periodsBody = (tag, year) => showBody(tag, { seasons: [], groups: [{ title: 'Díly', season: null, nextPage: 2,
  periods: { years: [2026, 2025, 2024], year, months: [], month: null }, episodes: episodes(tag, 8) }] });

const app = { resumeStore: { offer: () => null, continueRow: () => [] }, images: { get: () => null, load: () => {} },
  thumbsOn: () => true, toast: () => {}, play: () => {}, dvbConfig: {}, tab: 'archive', inContent: () => true };

function showPage() {
  const g = gateway();
  const screen = { client: () => g.client, applyFocus: () => {} };
  return { show: new CT.ShowPage(app, screen), g };
}

const lineTitles = (show) => show.lines.filter((l) => l.episode).map((l) => l.episode.title);

test('opening a show: the card at once, the loader in place of the page after 150 ms, no "Načítám…"', async (t) => {
  mock.timers.enable({ apis: ['setTimeout'] });
  t.after(() => mock.timers.reset());
  const { show, g } = showPage();
  show.open({ id: 'ct:1', title: 'Z karty', kind: 'series', source: 'ct' });
  assert.equal($('sh-title').textContent, 'Z karty'); // the title from the card, at once
  assert.equal($('sh-state').textContent, ''); // the loader replaces the T13 text
  mock.timers.tick(149);
  assert.equal(has('show', 'opening'), false);
  assert.equal(loaderIn($('show'), 'sh-opening'), null);
  mock.timers.tick(1);
  assert.equal(has('show', 'opening'), true);
  assert.ok(loaderIn($('show'), 'sh-opening'));
  assert.equal(loaderIn($('sh-episodes'), 'over'), null); // the page's loader, not the list's
  g.last('show').ok(showBody('a'));
  await tick();
  assert.equal(has('show', 'opening'), false);
  assert.equal(loaderIn($('show'), 'sh-opening'), null);
  assert.equal(lineTitles(show).length, 8);
  show.close();
});

test('a fast answer (under 150 ms) shows no loader at all', async (t) => {
  mock.timers.enable({ apis: ['setTimeout'] });
  t.after(() => mock.timers.reset());
  const { show, g } = showPage();
  show.open({ id: 'ct:1', title: 'Pořad', kind: 'series', source: 'ct' });
  mock.timers.tick(100);
  g.last('show').ok(showBody('a'));
  await tick();
  mock.timers.tick(500);
  assert.equal(has('show', 'opening'), false);
  assert.equal(loaderIn($('show'), 'sh-opening'), null);
  show.close();
});

test('a year chip: the old episodes stay, dimmed under the loader; the chips keep working', async (t) => {
  mock.timers.enable({ apis: ['setTimeout'] });
  t.after(() => mock.timers.reset());
  const { show, g } = showPage();
  show.open({ id: 'ct:1', title: 'Pořad', kind: 'series', source: 'ct' });
  g.last('show').ok(periodsBody('y2026', 2026));
  await tick();
  assert.equal(show.zone, 'years');
  show.onKey('right'); // 2025
  show.onKey('enter');
  mock.timers.tick(150);
  assert.equal(has('sh-episodes', 'waiting'), true); // dimmed (CSS .waiting > .ar-track)
  assert.ok(loaderIn($('sh-episodes'), 'over'));
  assert.equal(has('show', 'opening'), false);
  assert.deepEqual(lineTitles(show).slice(0, 1), ['y2026 0']); // the old list kept
  assert.ok($('sh-episodes').firstChild.childNodes.length > 0);
  show.onKey('right'); // the focus moves on the chips meanwhile
  assert.equal(show.yearFocus, 2);
  g.last('show').ok(periodsBody('y2025', 2025));
  await tick();
  assert.equal(has('sh-episodes', 'waiting'), false);
  assert.equal(loaderIn($('sh-episodes'), 'over'), null);
  assert.deepEqual(lineTitles(show).slice(0, 1), ['y2025 0']);
  show.close();
});

test('a newer chip replaces the waited one: the older answer is dropped, the loader stays', async (t) => {
  mock.timers.enable({ apis: ['setTimeout'] });
  t.after(() => mock.timers.reset());
  const { show, g } = showPage();
  show.open({ id: 'ct:1', title: 'Pořad', kind: 'series', source: 'ct' });
  g.last('show').ok(periodsBody('y2026', 2026));
  await tick();
  show.onKey('right');
  show.onKey('enter'); // 2025
  const first = g.last('show');
  mock.timers.tick(100);
  show.onKey('right');
  show.onKey('enter'); // 2024 before 2025 came
  const second = g.last('show');
  assert.notEqual(first, second);
  mock.timers.tick(50); // 150 ms after the first choice: the wait went on
  assert.ok(loaderIn($('sh-episodes'), 'over'));
  first.ok(periodsBody('y2025', 2025));
  await tick();
  assert.deepEqual(lineTitles(show).slice(0, 1), ['y2026 0']); // dropped
  assert.ok(loaderIn($('sh-episodes'), 'over')); // still waiting for 2024
  second.ok(periodsBody('y2024', 2024));
  await tick();
  assert.deepEqual(lineTitles(show).slice(0, 1), ['y2024 0']);
  assert.equal(loaderIn($('sh-episodes'), 'over'), null);
  show.close();
});

test('season A, then season B before A came, B fails: the chips go back to the season listed, not to A', async (t) => {
  mock.timers.enable({ apis: ['setTimeout'] });
  t.after(() => mock.timers.reset());
  const { show, g } = showPage();
  show.open({ id: 'ct:1', title: 'Pořad', kind: 'series', source: 'ct' });
  g.last('show').ok(showBody('s1'));
  await tick();
  show.moveZoneTo('seasons');
  show.onKey('right');
  show.onKey('enter'); // 2. řada
  const a = g.last('show');
  assert.equal(a.args[1], 's2');
  show.onKey('right');
  show.onKey('enter'); // 3. řada
  const b = g.last('show');
  assert.equal(b.args[1], 's3');
  assert.equal(show.season, 2);
  b.fail();
  await tick();
  assert.equal(show.season, 0); // what is listed: 1. řada
  assert.equal(show.seasonFocus, 0);
  const selected = $('sh-seasons').childNodes.map((c) => c.classList.contains('selected'));
  assert.deepEqual(selected, [true, false, false]);
  assert.deepEqual(lineTitles(show).slice(0, 1), ['s1 0']);
  a.ok(showBody('s2')); // the older answer comes late: dropped
  await tick();
  assert.deepEqual(lineTitles(show).slice(0, 1), ['s1 0']);
  assert.equal(show.season, 0);
  show.close();
});

test('year A, then year B before A came, B fails: the year chips show the year listed', async (t) => {
  mock.timers.enable({ apis: ['setTimeout'] });
  t.after(() => mock.timers.reset());
  const { show, g } = showPage();
  show.open({ id: 'ct:1', title: 'Pořad', kind: 'series', source: 'ct' });
  g.last('show').ok(periodsBody('y2026', 2026));
  await tick();
  show.onKey('right');
  show.onKey('enter'); // 2025
  show.onKey('right');
  show.onKey('enter'); // 2024
  g.last('show').fail();
  await tick();
  const selected = $('sh-years').childNodes.filter((c) => c.classList.contains('selected')).map((c) => c.textContent);
  assert.deepEqual(selected, ['2026']);
  assert.equal(loaderIn($('sh-episodes'), 'over'), null);
  show.close();
});

test('the next page of episodes: a small loader below the last episode, nothing dimmed', async (t) => {
  mock.timers.enable({ apis: ['setTimeout'] });
  t.after(() => mock.timers.reset());
  const { show, g } = showPage();
  show.open({ id: 'ct:1', title: 'Pořad', kind: 'series', source: 'ct' });
  g.last('show').ok(showBody('s1'));
  await tick();
  assert.equal(show.zone, 'episodes');
  const before = g.calls.length;
  for (let i = 0; i < 5; i++) show.onKey('down'); // near the end: page 2
  assert.equal(g.calls.length, before + 1);
  assert.equal(g.last('show').args[2], 2);
  mock.timers.tick(150);
  const track = $('sh-episodes').firstChild;
  const below = loaderIn(track, 'below');
  assert.ok(below);
  assert.equal(below.style.top, show.lines.length * 76 + 'px'); // under the last line (a header + 8 episodes)
  assert.equal(has('sh-episodes', 'waiting'), false);
  assert.equal(loaderIn($('sh-episodes'), 'over'), null);
  show.onKey('down'); // no second request while it waits
  assert.equal(g.calls.length, before + 1);
  // the list is exactly as tall as its lines: at its end it scrolls one line further, so the loader is in view
  show.onKey('down');
  assert.equal(show.focusedEpisode().title, 's1 7'); // the last one
  assert.equal(show.list.top + show.list.visible, show.lines.length + 1);
  assert.equal(track.style.transform, 'translateY(' + -show.list.top * 76 + 'px)');
  g.last('show').ok(showBody('p2', { groups: [{ title: 'Díly', season: 's1', nextPage: null, episodes: episodes('p2', 3) }] }));
  await tick();
  assert.equal(loaderIn(track, 'below'), null);
  assert.equal(lineTitles(show).length, 11);
  assert.equal(show.list.tail, 0); // no free line once the page is there
  show.close();
});

test('no next page while a season is waited for (its answer would be dropped by the page)', async (t) => {
  mock.timers.enable({ apis: ['setTimeout'] });
  t.after(() => mock.timers.reset());
  const { show, g } = showPage();
  show.open({ id: 'ct:1', title: 'Pořad', kind: 'series', source: 'ct' });
  g.last('show').ok(showBody('s1'));
  await tick();
  show.moveZoneTo('seasons');
  show.onKey('right');
  show.onKey('enter'); // 2. řada asked
  const asked = g.calls.length;
  show.moveZoneTo('episodes');
  for (let i = 0; i < 7; i++) show.onKey('down'); // the old list's end
  assert.equal(g.calls.length, asked);
  g.last('show').ok(showBody('s2'));
  await tick();
  assert.deepEqual(lineTitles(show).slice(0, 1), ['s2 0']);
  assert.equal(show.season, 1);
  show.close();
});

test('closing the page drops its wait: no loader, a late answer ignored', async (t) => {
  mock.timers.enable({ apis: ['setTimeout'] });
  t.after(() => mock.timers.reset());
  const { show, g } = showPage();
  show.open({ id: 'ct:1', title: 'Pořad', kind: 'series', source: 'ct' });
  mock.timers.tick(150);
  assert.ok(loaderIn($('show'), 'sh-opening'));
  show.close();
  assert.equal(loaderIn($('show'), 'sh-opening'), null);
  g.last('show').ok(showBody('a'));
  await tick();
  assert.equal(show.data, null);
});

// --- the grid ---

const CATEGORIES = [{ id: 'filmy', title: 'Filmy' }, { id: 'serialy', title: 'Seriály' }];
const cards = (tag, n, next) => ({ title: 'X', cards: Array.from({ length: n }, (_, i) => ({ id: 'ct:' + tag + i, title: tag + ' ' + i,
  kind: 'series', source: 'ct' })), nextPage: next || null, genres: [] });
const gridTitles = (screen) => (screen.stale || screen.grid).items.map((c) => c.title);
const builtCards = () => $('ar-grid').firstChild.childNodes.filter((r) => r.classList.contains('ar-grid-row'))
  .reduce((all, r) => all.concat(r.childNodes.map((c) => c.childNodes.filter((n) => n.classList.contains('ar-title'))[0].textContent)), []);

function archive() {
  const g = gateway();
  const screen = new CT.ArchiveScreen(app);
  screen.client = () => g.client;
  return { screen, g };
}

async function opened(t, n) {
  mock.timers.enable({ apis: ['setTimeout'] });
  t.after(() => mock.timers.reset());
  const { screen, g } = archive();
  screen.show();
  mock.timers.tick(150);
  // before the menu: the categories are what the grid waits for, the loader instead of "Načítám…"
  assert.ok(loaderIn($('ar-grid'), 'over'));
  assert.equal(note.textContent, '');
  g.last('categories').ok(CATEGORIES);
  await tick();
  assert.equal(g.last('category').args[0], 'filmy');
  assert.ok(loaderIn($('ar-grid'), 'over')); // the wait goes on into the first page, no gap
  g.last('category').ok(cards('film', n || 6, 2));
  await tick();
  assert.equal(loaderIn($('ar-grid'), 'over'), null);
  assert.deepEqual(builtCards().slice(0, 2), ['film 0', 'film 1']);
  return { screen, g };
}

test('the grid: another menu item keeps the old cards, dimmed under the loader, until the new ones come', async (t) => {
  const { screen, g } = await opened(t);
  screen.menuFocus = screen.menu.items.map((i) => i.title).indexOf('Seriály');
  screen.commitFocus();
  assert.equal(g.last('category').args[0], 'serialy');
  assert.equal(has('ar-grid', 'stale'), true); // the old cards kept
  assert.deepEqual(builtCards().slice(0, 2), ['film 0', 'film 1']); // kept, not emptied
  assert.equal(has('ar-grid', 'waiting'), false); // not dimmed before 150 ms
  assert.equal(note.textContent, '');
  mock.timers.tick(150);
  assert.equal(has('ar-grid', 'waiting'), true);
  assert.ok(loaderIn($('ar-grid'), 'over'));
  assert.deepEqual(builtCards().slice(0, 2), ['film 0', 'film 1']);
  g.last('category').ok(cards('serial', 4));
  await tick();
  assert.equal(has('ar-grid', 'waiting'), false);
  assert.equal(has('ar-grid', 'stale'), false);
  assert.equal(loaderIn($('ar-grid'), 'over'), null);
  assert.deepEqual(builtCards().slice(0, 2), ['serial 0', 'serial 1']);
});

test('the grid: a sort chosen while another menu item loads replaces it, the old cards still those on screen', async (t) => {
  const { screen, g } = await opened(t);
  screen.menuFocus = screen.menu.items.map((i) => i.title).indexOf('Seriály');
  screen.commitFocus();
  const first = g.last('category');
  mock.timers.tick(150);
  screen.zone = 'sorts';
  screen.onKey('right');
  screen.onKey('enter'); // another sort of Seriály
  const second = g.last('category');
  assert.notEqual(first, second);
  assert.deepEqual(builtCards().slice(0, 1), ['film 0']);
  assert.ok(loaderIn($('ar-grid'), 'over'));
  first.ok(cards('popular', 3));
  await tick();
  assert.deepEqual(builtCards().slice(0, 1), ['film 0']); // the older answer dropped
  assert.ok(loaderIn($('ar-grid'), 'over'));
  second.ok(cards('newest', 3));
  await tick();
  assert.deepEqual(builtCards().slice(0, 1), ['newest 0']);
  assert.equal(loaderIn($('ar-grid'), 'over'), null);
  assert.deepEqual(gridTitles(screen), ['newest 0', 'newest 1', 'newest 2']);
});

test('the grid: a failed load ends the wait with its error instead of the old cards', async (t) => {
  const { screen, g } = await opened(t);
  screen.menuFocus = screen.menu.items.map((i) => i.title).indexOf('Seriály');
  screen.commitFocus();
  mock.timers.tick(150);
  g.last('category').fail();
  await tick();
  assert.equal(loaderIn($('ar-grid'), 'over'), null);
  assert.equal(has('ar-grid', 'waiting'), false);
  assert.deepEqual(builtCards(), []);
  assert.equal(note.textContent, 'Nepovedlo se');
});

test('the grid: the next page has a small loader below the last row, the cards not dimmed', async (t) => {
  const { screen, g } = await opened(t);
  screen.zone = 'grid';
  screen.onKey('down'); // row 2 of 2: near the end, page 2
  assert.equal(g.last('category').args[3], 2);
  mock.timers.tick(150);
  const below = loaderIn($('ar-grid').firstChild, 'below');
  assert.ok(below);
  assert.equal(below.style.top, 2 * 350 + 'px'); // two rows of three
  assert.equal(has('ar-grid', 'waiting'), false);
  assert.equal(loaderIn($('ar-grid'), 'over'), null);
  assert.equal(note.textContent, ''); // instead of "Načítám další…"
  g.last('category').ok(cards('more', 3));
  await tick();
  assert.equal(loaderIn($('ar-grid').firstChild, 'below'), null);
  assert.equal(screen.grid.items.length, 9);
});

test('the grid: "Pokračovat ve sledování" is this TV\'s own list, no wait and no loader', async (t) => {
  const { screen, g } = await opened(t);
  const asked = g.calls.length;
  screen.menuFocus = screen.menu.items.map((i) => i.title).indexOf('Pokračovat ve sledování');
  assert.ok(screen.menuFocus >= 0);
  screen.commitFocus();
  mock.timers.tick(500);
  assert.equal(g.calls.length, asked);
  assert.equal(loaderIn($('ar-grid'), 'over'), null);
  assert.equal(has('ar-grid', 'stale'), false);
});

test('the grid: search results from the 0.5 s pause on, the previous results dimmed under the loader', async (t) => {
  const { screen, g } = await opened(t);
  screen.menuFocus = screen.menu.items.map((i) => i.title).indexOf('Hledat');
  screen.menuOk();
  assert.deepEqual(builtCards(), []); // Hledat without text: nothing to wait for, no loader
  mock.timers.tick(150);
  assert.equal(loaderIn($('ar-grid'), 'over'), null);
  screen.typed('vlak');
  mock.timers.tick(499);
  assert.equal(g.last('search'), undefined);
  mock.timers.tick(1);
  assert.equal(g.last('search').args[0], 'vlak');
  mock.timers.tick(150);
  assert.ok(loaderIn($('ar-grid'), 'over'));
  g.last('search').ok(cards('vlak', 2));
  await tick();
  assert.deepEqual(builtCards(), ['vlak 0', 'vlak 1']);
  screen.typed('vlaky');
  mock.timers.tick(500); // the pause, then (a timer of its own) the loader
  mock.timers.tick(150);
  assert.ok(loaderIn($('ar-grid'), 'over'));
  assert.equal(has('ar-grid', 'waiting'), true);
  assert.deepEqual(builtCards(), ['vlak 0', 'vlak 1']); // kept while the new text is searched
  g.last('search').ok(cards('vlaky', 1));
  await tick();
  assert.deepEqual(builtCards(), ['vlaky 0']);
  // the search cleared: nothing to wait for any more, the loader goes at once
  screen.typed('xyz');
  mock.timers.tick(500); // the pause, then (a timer of its own) the loader
  mock.timers.tick(150);
  assert.ok(loaderIn($('ar-grid'), 'over'));
  screen.clearSearch();
  assert.equal(loaderIn($('ar-grid'), 'over'), null);
  assert.equal(has('ar-grid', 'stale'), false);
  assert.deepEqual(builtCards(), []);
});

test('the grid: the old cards stay focusable and open while the new ones load (lead decision, all apps)', async (t) => {
  const { screen, g } = await opened(t, 15);
  screen.menuFocus = screen.menu.items.map((i) => i.title).indexOf('Seriály');
  screen.commitFocus();
  mock.timers.tick(150);
  assert.ok(loaderIn($('ar-grid'), 'over'));
  const pages = g.calls.length;
  screen.onKey('right'); // from the menu into the dimmed cards
  assert.equal(screen.zone, 'grid');
  screen.onKey('right');
  screen.onKey('down'); // row 2: no next page of the old cards (what comes replaces them)
  assert.equal(g.calls.length, pages);
  assert.equal(screen.stale.focus, 4);
  const focused = () => $('ar-grid').firstChild.childNodes.filter((r) => r.classList.contains('ar-grid-row'))
    .reduce((all, r) => all.concat(r.childNodes), []).filter((c) => c.classList.contains('focused'))
    .map((c) => c.childNodes.filter((n) => n.classList.contains('ar-title'))[0].textContent);
  assert.deepEqual(focused(), ['film 4']);
  screen.onKey('down');
  screen.onKey('down'); // row 4 of 5: the old cards scroll with the focus
  assert.equal(screen.stale.lines.top, 2);
  assert.deepEqual(focused(), ['film 10']);
  screen.onKey('up');
  screen.onKey('up');
  screen.onKey('enter'); // opens that card
  assert.equal(screen.page.isOpen(), true);
  assert.equal(screen.page.card.id, 'ct:film4');
  assert.equal(g.last('show').args[0], 'ct:film4');
  screen.page.close();
  // the new cards come: they replace the old ones, the focus on their first
  g.last('category').ok(cards('serial', 4));
  await tick();
  assert.equal(screen.stale, null);
  assert.equal(screen.zone, 'grid');
  assert.deepEqual(focused(), ['serial 0']);
});
