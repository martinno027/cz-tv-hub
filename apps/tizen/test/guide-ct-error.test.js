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

// --- T38: selecting a filler that covers now plays that channel live, as its Živě card does ---

/** Presses OK on the slot of row `name` that `pick(slot)` chooses; returns what the app was asked to do. */
function pressOk(g, name, pick) {
  const calls = [];
  g.app.playItem = (item) => calls.push(['playItem', item.source, item.channel.id, item.key]);
  g.app.select = (entry) => calls.push(['select', entry.source, entry.channel.id]);
  g.app.toast = (text) => calls.push(['toast', text]);
  g.zone = 'grid';
  g.focusRow = g.rows.findIndex((r) => r.name === name);
  g.focusSlot = g.rows[g.focusRow].slots.findIndex(pick);
  assert.ok(g.focusSlot >= 0, 'no such slot in ' + name);
  assert.equal(g.onKey('enter'), true);
  return calls;
}
const coversNow = (s) => s.start <= NOW && NOW < s.end;

test('Entries.fillerPlaysLive: ČT and Anténa fillers at now only, never Sněmovna', () => {
  const at = { start: NOW - 60e3, end: NOW + 60e3 };
  assert.equal(CT.Entries.fillerPlaysLive('ct', at, NOW), true);
  assert.equal(CT.Entries.fillerPlaysLive('dvb', at, NOW), true);
  assert.equal(CT.Entries.fillerPlaysLive('psp', at, NOW), false);
  assert.equal(CT.Entries.fillerPlaysLive('ct', { start: NOW, end: NOW + 60e3 }, NOW), true); // start inclusive
  assert.equal(CT.Entries.fillerPlaysLive('ct', { start: NOW - 60e3, end: NOW }, NOW), false); // end exclusive
  assert.equal(CT.Entries.fillerPlaysLive('dvb', { start: NOW + 60e3, end: NOW + 120e3 }, NOW), false);
  // a failed ČT row on the day containing now: any of its fillers (spec/apps.md, T38 follow-up)
  const later = { start: NOW + 3600e3, end: NOW + 7200e3 };
  assert.equal(CT.Entries.fillerPlaysLive('ct', later, NOW, true, true), true);
  assert.equal(CT.Entries.fillerPlaysLive('ct', later, NOW, false, true), false); // schedule loaded: only now
  assert.equal(CT.Entries.fillerPlaysLive('ct', later, NOW, true, false), false); // another day
  assert.equal(CT.Entries.fillerPlaysLive('dvb', later, NOW, true, true), false); // Anténa: only now
  assert.equal(CT.Entries.fillerPlaysLive('psp', at, NOW, true, true), false);
});

test('guide, ČT failed: OK on the ČT filler at now plays that ČT channel live (as Živě)', () => {
  const g = guide(failedCt());
  assert.deepEqual(pressOk(g, 'ČT24', (s) => !s.item && coversNow(s)), [['playItem', 'ct', 'CH_24', 'ct:CH_24']]);
});

test('guide: OK on an Anténa gap at now plays that channel live; a gap at another time does nothing', () => {
  const g = guide(failedCt());
  assert.deepEqual(pressOk(g, 'Nova Action', (s) => !s.item && coversNow(s)), [['playItem', 'dvb', 'nova-action', 'dvb:nova-action']]);
  assert.deepEqual(pressOk(g, 'Nova Action', (s) => !s.item && s.end <= NOW), []);
});

test('guide, ČT loaded: OK on a ČT gap at now plays live; on a programme it still opens the programme', () => {
  const day = CT.Days.openDay(NOW);
  const [start] = CT.Days.bounds(day);
  const p = { idec: 'x1', title: 'Zprávy', start: start + 2 * 3600e3, end: start + 3 * 3600e3, isPlayableNow: true };
  const g = guide({ plan: { CH_1: [p] } });
  assert.deepEqual(pressOk(g, 'ČT1', (s) => !s.item && coversNow(s)), [['playItem', 'ct', 'CH_1', 'ct:CH_1']]);
  assert.deepEqual(pressOk(g, 'ČT1', (s) => !!s.item), [['select', 'ct', 'CH_1']]);
});

test('guide, ČT failed on another day: its filler does not cover now and does nothing', () => {
  const day = CT.Days.openDay(NOW - 24 * 3600e3);
  const g = new CT.GuideScreen(app());
  g.day = day;
  g.days = CT.Days.guideDays(CT.Days.today(NOW));
  g.showDetail = () => {};
  g.prefetch = () => {};
  g.build(failedCt(), { channels: [], plan: {} });
  assert.ok(!g.rows[0].slots.some(coversNow));
  assert.deepEqual(pressOk(g, 'ČT1', (s) => !s.item), []);
});

test('guide, ČT failed today: a ČT filler away from now (split by hand: Samsung draws one whole-day filler) also plays live; Anténa away from now does not', () => {
  const g = guide(failedCt());
  const row = g.rows.find((r) => r.name === 'ČT1');
  assert.equal(row.ctFailed, true);
  // The Samsung layout makes one filler over the whole day; split it so a filler not covering now exists.
  const whole = row.slots[0];
  row.slots = [{ start: whole.start, end: NOW - 3600e3, item: null, el: null }, { start: NOW - 3600e3, end: whole.end, item: null, el: null }];
  g.applyFocus = () => {}; // the drawn cells belong to the original slot; only the OK decision is under test
  assert.ok(!coversNow(row.slots[0]));
  assert.deepEqual(pressOk(g, 'ČT1', (s) => s === row.slots[0]), [['playItem', 'ct', 'CH_1', 'ct:CH_1']]);
  assert.deepEqual(pressOk(g, 'Nova Action', (s) => !s.item && s.end <= NOW), []);
});
