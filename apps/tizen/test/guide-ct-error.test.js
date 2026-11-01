'use strict';
// spec/apps.md (T36): when a day's ČT schedule failed, each ČT row says "Program se nepodařilo načíst" as its filler
// and nothing is drawn over other rows. Regression: the text was an opaque band at the grid's first row (#grid-message,
// T29), which after scrolling covered an Anténa row. The failure comes from the shared fixture graphql_error.json.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT, response } = require('./load');

const FAILED = 'Program se nepodařilo načíst';
const OFF = 'Mimo vysílání';

// --- a minimal DOM, enough for GuideScreen.build / LiveScreen.render ---
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
    this.clientHeight = 76 * 2; // two rows on screen, so the three-row guide can scroll by one
    this.clientWidth = 1792;
    this.classList = classList(this);
  }
  set innerHTML(v) { this.children = []; }
  get innerHTML() { return ''; }
  adopt(child) {
    const list = child.tagName === '#fragment' ? child.children.splice(0) : [child];
    list.forEach((c) => { c.parentNode = this; });
    return list;
  }
  appendChild(child) { this.children.push(...this.adopt(child)); return child; }
  insertBefore(child, ref) {
    const at = this.children.indexOf(ref);
    this.children.splice(at < 0 ? this.children.length : at, 0, ...this.adopt(child));
    return child;
  }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); child.parentNode = null; return child; }
  replaceChild(fresh, old) { this.children[this.children.indexOf(old)] = fresh; fresh.parentNode = this; return old; }
  setAttribute() {}
  /** All text below this node, depth first. */
  texts() { return [this.textContent].concat(...this.children.map((c) => c.texts())).filter(Boolean); }
  find(cls) {
    const out = [];
    const walk = (n) => { if (n.classList.contains(cls)) out.push(n); n.children.forEach(walk); };
    walk(this);
    return out;
  }
}

const byId = {};
global.window = global;
global.document = {
  getElementById: (id) => byId[id] || (byId[id] = new Node('div')),
  createElement: (tag) => new Node(tag),
  createDocumentFragment: () => new Node('#fragment')
};
CT.sourceIcon = (source) => { const n = new Node('span'); n.className = 'source-icon ' + source; return n; };
CT.scrollIntoView = () => {};
require('../app/js/ui/guide.js');
require('../app/js/ui/live.js');

const NOW = Date.parse('2026-10-24T18:00:00Z');
const CHANNELS = [
  { key: 'ct:CH_1', source: 'ct', channel: { id: 'CH_1', name: 'ČT1' } },
  { key: 'dvb:nova-action', source: 'dvb', channel: { id: 'nova-action', name: 'Nova Action' } },
  { key: 'ct:CH_24', source: 'ct', channel: { id: 'CH_24', name: 'ČT24' } }
];

function app() {
  return {
    now: NOW,
    inContent: () => true,
    sourceOn: () => true,
    channelList: () => CHANNELS,
    pspState: () => 'unknown',
    images: { prefetch: () => {}, load: () => {} }
  };
}

/** What the guide's ČT promise gives build() for the failing fixture (as selectDay does). */
function failedCt() {
  const r = response('graphql_error.json');
  try {
    CT.CtParser.parseSchedule(r.status, r.body);
  } catch (e) {
    return { error: e.userMessage || CT.Messages.SCHEDULE_FAILED };
  }
  assert.fail('graphql_error.json must fail');
}

function guide(ct) {
  const day = CT.Days.openDay(NOW);
  const [start] = CT.Days.bounds(day);
  const g = new CT.GuideScreen(app());
  g.day = day;
  g.days = CT.Days.guideDays(CT.Days.today(NOW));
  g.showDetail = () => {};
  g.prefetch = () => {}; // images are not this test's business
  g.build(ct, { channels: [], plan: { 'nova-action': [{ id: 'e1', channelId: 'nova-action', title: 'Film', start: start + 13 * 3600e3, end: start + 15 * 3600e3 }] } });
  return g;
}

/** The filler texts drawn in each row (by channel name). */
function fillers(g) {
  const out = {};
  g.rows.forEach((row) => {
    out[row.name] = row.line ? [...new Set(row.track.find('filler').map((c) => c.texts().join('')))] : null;
  });
  return out;
}

test('the failing fixture gives the schedule error text', () => {
  assert.equal(failedCt().error, FAILED);
});

test('Entries.fillerText: only a ČT row of a failed day says the schedule failed', () => {
  assert.equal(CT.Entries.fillerText('ct', true), FAILED);
  assert.equal(CT.Entries.fillerText('dvb', true), OFF);
  assert.equal(CT.Entries.fillerText('psp', true), OFF);
  assert.equal(CT.Entries.fillerText('ct', false), OFF);
  assert.notEqual(CT.Entries.fillerText('ct', true), CT.Entries.fillerText('ct', false));
});

test('Entries.offAirText: a ČT card without data on a failed schedule says so', () => {
  assert.equal(CT.Entries.offAirText('ct', null, true), FAILED);
  assert.equal(CT.Entries.offAirText('ct', null, false), ''); // still loading
  assert.equal(CT.Entries.offAirText('ct', { current: null }, false), OFF);
  assert.equal(CT.Entries.offAirText('dvb', null, true), '');
  assert.equal(CT.Entries.offAirText('dvb', { current: null }, true), OFF);
});

test('guide, ČT failed: ČT rows say it in their fillers, the Anténa row keeps "Mimo vysílání", no band', () => {
  const g = guide(failedCt());
  assert.equal(document.getElementById('grid-message').textContent, '');
  const f = fillers(g);
  assert.deepEqual(f['ČT1'], [FAILED]);
  assert.deepEqual(f['ČT24'], [FAILED]);
  assert.deepEqual(f['Nova Action'], [OFF]);
  // the Anténa programme itself is drawn
  assert.ok(g.rows[1].track.texts().includes('Film'));
});

test('guide, ČT failed: after scrolling a row down the rows still carry their own text', () => {
  const g = guide(failedCt());
  g.scrollView(0, 1);
  assert.equal(g.scrollRow, 1);
  assert.equal(document.getElementById('grid-message').textContent, '');
  const f = fillers(g);
  assert.deepEqual(f['Nova Action'], [OFF]);
  assert.deepEqual(f['ČT24'], [FAILED]);
});

test('guide, ČT loaded: ČT rows say "Mimo vysílání" (the two variants differ)', () => {
  const g = guide({ plan: {} });
  const f = fillers(g);
  assert.deepEqual(f['ČT1'], [OFF]);
  assert.notDeepEqual(f['ČT1'], fillers(guide(failedCt()))['ČT1']);
});

test('Živě, ČT failed: ČT cards show the text, the Anténa card does not, no banner', () => {
  const root = document.getElementById('screen-live');
  const live = new CT.LiveScreen(app());
  live.render({ onAir: null, error: FAILED }, { channels: [], onAir: { 'nova-action': { current: null, next: null } }, error: null }, NOW);
  assert.equal(root.find('group-error').length, 0);
  const titles = live.cards.map((c) => c.el.find('card-title').map((n) => n.textContent).join(''));
  assert.deepEqual(titles, [FAILED, OFF, FAILED]);
});
