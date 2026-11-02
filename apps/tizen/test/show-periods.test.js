'use strict';
// spec/apps.md "Archiv" → show page, "Years only (T51)": a year whose answer has `months: []` / `month: null` hides the
// month row, the remote's up/down never lands on it, the year chip asks for ?year= alone and its next page too.
// The show page (ui/show.js, shared by the web) on a minimal DOM; the answers go through the real parser.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT, json } = require('./load');

const A = CT.Archive;
const cases = json('archive_cases.json');

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
    this.clientHeight = 0; // before layout: the TV's fixed line count
    this.clientWidth = 0;
    this.offsetLeft = 0;
    this.offsetWidth = 0;
    this.scrollLeft = 0;
    this.classList = classList(this);
  }
  get childNodes() { return this.children; }
  get firstChild() { return this.children[0] || null; }
  get lastChild() { return this.children[this.children.length - 1] || null; }
  set innerHTML(v) { this.children = []; }
  get innerHTML() { return ''; }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); child.parentNode = null; return child; }
}

const byId = {};
global.window = global;
global.document = {
  getElementById: (id) => byId[id] || (byId[id] = new Node('div')),
  createElement: (tag) => new Node(tag),
  createTextNode: (text) => { const n = new Node('#text'); n.textContent = text; return n; }
};
document.getElementById('sh-episodes').appendChild(new Node('div')); // its .ar-track
document.getElementById('sh-tmdb').appendChild(new Node('span'));
CT.sourceIcon = (source) => { const n = new Node('span'); n.className = 'source-icon ' + source; return n; };
require('../app/js/ui/archive-cards.js');
require('../app/js/ui/show.js');

// the golden year shown whole as the gateway sends it: 2026, 2025 → 2025, `months: []`, `month: null`
const WHOLE = cases.periods.chips.filter((c) => c.periods && Array.isArray(c.periods.months) && !c.periods.months.length &&
  c.periods.month === null)[0].periods;
const MONTHLY = { years: WHOLE.years, year: WHOLE.years[0], months: [3, 2, 1], month: 3 };

function episodes(tag, n) {
  return Array.from({ length: n }, (_, i) => ({ source: 'ct', idec: tag + '-' + i, title: tag + ' ' + i,
    durationSeconds: 60, aired: '2025-06-01T18:00:00Z', channel: 'ČT1', playable: true }));
}

function answer(periods, tag) {
  return { status: 200, body: JSON.stringify({ card: { id: 'ct:1', title: 'Pořad', kind: 'series', source: 'ct' },
    description: '', creators: '', countries: [], seasons: [],
    groups: [{ title: 'Díly', season: null, nextPage: 2, periods: periods, episodes: episodes(tag, 8) }] }) };
}

/** A show page whose gateway answers `first` on opening and `then(query)` afterwards; `queries` = what was asked. */
function page(first, then) {
  const queries = [];
  const client = {
    show: (id, season, pageNo, period) => {
      const query = A.showQuery(season, pageNo, period);
      queries.push(query);
      const r = queries.length === 1 ? first : then(query);
      return Promise.resolve(A.parseShow(r.status, r.body));
    }
  };
  const app = { resumeStore: { offer: () => null }, images: { get: () => null, load: () => {} }, thumbsOn: () => true,
    toast: () => {}, play: () => {} };
  const screen = { client: () => client, applyFocus: () => {} };
  return { show: new CT.ShowPage(app, screen), queries };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
const hidden = (id) => document.getElementById(id).classList.contains('hidden');
const chipLabels = (id) => document.getElementById(id).childNodes.map((c) => c.textContent);

test('a year shown whole (T51): no month row, the list takes the year, up/down skip the month row', async () => {
  const { show, queries } = page(answer(WHOLE, 'y2025'), () => answer(WHOLE, 'y2025p2'));
  show.open({ id: 'ct:1', title: 'Pořad', kind: 'series', source: 'ct' });
  await tick();
  assert.deepEqual(chipLabels('sh-years'), ['2026', '2025']);
  assert.equal(hidden('sh-years'), false);
  assert.equal(hidden('sh-months'), true);
  assert.deepEqual(chipLabels('sh-months'), []);
  assert.ok(document.getElementById('show').classList.contains('periods')); // the year row still takes its line
  assert.deepEqual(show.zones(), ['years', 'episodes']);
  assert.equal(show.zone, 'years'); // a show without seasons starts on its year
  show.onKey('down');
  assert.equal(show.zone, 'episodes');
  show.onKey('up');
  assert.equal(show.zone, 'years');
  // the next page of the year: the year alone, no month
  show.onKey('down');
  for (let i = 0; i < 6; i++) show.onKey('down');
  await tick();
  assert.deepEqual(queries, ['', '?year=2025&page=2']);
});

test('from a year with months to a year shown whole and back (T51)', async () => {
  const { show, queries } = page(answer(MONTHLY, 'm2026'), (q) => (q.indexOf('2025') >= 0 ? answer(WHOLE, 'y2025') : answer(MONTHLY, 'm2026')));
  show.open({ id: 'ct:1', title: 'Pořad', kind: 'series', source: 'ct' });
  await tick();
  assert.deepEqual(chipLabels('sh-months'), ['březen', 'únor', 'leden']);
  assert.deepEqual(show.zones(), ['years', 'months', 'episodes']); // differs from the year shown whole
  show.onKey('right'); // 2025
  show.onKey('enter');
  assert.equal(queries[1], '?year=2025&page=1'); // the year chip: ?year= alone
  await tick();
  assert.equal(hidden('sh-months'), true);
  assert.deepEqual(show.zones(), ['years', 'episodes']);
  show.onKey('down');
  assert.equal(show.zone, 'episodes');
  show.onKey('up');
  assert.equal(show.zone, 'years');
  show.onKey('left'); // back to 2026: the month row returns
  show.onKey('enter');
  await tick();
  assert.equal(queries[2], '?year=2026&page=1');
  assert.equal(hidden('sh-months'), false);
  assert.deepEqual(show.zones(), ['years', 'months', 'episodes']);
});

test('the focus on the month row moves to the years when the answer has no months (a stale month chip, T51)', async () => {
  const { show, queries } = page(answer(MONTHLY, 'm2026'), () => answer({ years: [2026, 2025], year: 2026, months: [], month: null }, 'w'));
  show.open({ id: 'ct:1', title: 'Pořad', kind: 'series', source: 'ct' });
  await tick();
  show.onKey('down');
  assert.equal(show.zone, 'months');
  show.onKey('right'); // únor
  show.onKey('enter');
  assert.equal(queries[1], '?year=2026&month=2&page=1');
  await tick();
  assert.equal(hidden('sh-months'), true);
  assert.equal(show.zone, 'years');
  assert.equal(show.yearFocus, 0); // on the selected year
});
