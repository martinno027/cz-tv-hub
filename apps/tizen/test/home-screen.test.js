'use strict';
// Domů's screen (spec/apps.md "Domů", T58) on a minimal DOM: the blocks in order and left out when empty, the first
// card focused on opening, OK plays, long OK on "Pokračovat" removes, the focus kept on the same card across a redraw;
// the weather (T59): the header lines, "Počasí na týden" (never focused) and the one-time location question.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT, response } = require('./load');

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
    Object.assign(this, { tagName: tag, className: '', textContent: '', children: [], style: {}, dataset: {},
      parentNode: null, scrollTop: 0, scrollLeft: 0, offsetLeft: 0, offsetTop: 0, offsetWidth: 0, offsetHeight: 0,
      clientWidth: 1792, clientHeight: 930 });
    this.classList = classList(this);
  }
  set innerHTML(v) { this.children = []; }
  get innerHTML() { return ''; }
  get childNodes() { return this.children; }
  appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
  setAttribute() {}
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
global.document = { getElementById: (id) => byId[id] || (byId[id] = new Node('div')), createElement: (t) => new Node(t),
  createTextNode: (t) => Object.assign(new Node('#text'), { textContent: t }) };
CT.sourceIcon = (source) => { const n = new Node('span'); n.className = 'source-icon ' + source; return n; };
CT.scrollIntoView = () => {};
CT.LIVE_PREFIX = { live: 'ct:', dvbLive: 'dvb:', pspLive: 'psp:' };
require('../app/js/ui/live.js');
require('../app/js/ui/archive-cards.js');
require('../app/js/ui/weather.js');
require('../app/js/ui/home.js');

const NOW = Date.parse('2026-11-04T18:00:00Z');
const ch = (source, id) => ({ key: source + ':' + id, source, channel: { id, name: id } });
const LIST = [ch('ct', 'CH_1'), ch('ct', 'CH_2'), ch('psp', 'stream1'), ch('psp', 'stream2'), ch('dvb', 'nova')];

function memoryStore(entries) {
  let data = entries;
  return new CT.ResumeStore({ load: () => data, save: (d) => { data = d; } }, () => NOW);
}

const ENTRIES = {
  'ct:1': { progress: 600, length: 3000, savedAt: NOW - 1000, title: 'Pořad A', showTitle: '', image: '', source: 'ct' },
  'dvb:rec1': { progress: 600, length: 3000, savedAt: NOW - 2000, title: 'Nahrávka', showTitle: '', image: '', source: 'dvb' }
};

function app(opts) {
  const calls = [];
  const a = {
    tab: 'home', zone: 'content', now: NOW, dvbConfig: opts.gateway ? {} : null, dvbStatus: opts.status || null,
    dvbChannels: [], ctState: { onAir: { CH_1: { current: { title: 'Zprávy', start: NOW - 60e3, end: NOW + 60e3 }, next: null } }, error: null },
    dvbState: null,
    resumeStore: memoryStore(JSON.parse(JSON.stringify(opts.entries || ENTRIES))),
    images: { get: () => null, load() {} },
    thumbsOn: () => false,
    inContent() { return this.zone === 'content'; },
    channelList: () => opts.list || LIST,
    pspState: (id) => (id === 'stream2' ? 'live' : 'off'),
    play: (r) => calls.push(['play', r.kind, r.resumeKey || r.channelId]),
    playItem: (item) => calls.push(['playItem', item.key]),
    showTab: (t) => calls.push(['tab', t]),
    weather: { weather: opts.weather || null, answeredAt: opts.weather ? NOW : null, refresh: (force) => calls.push(['weather', !!force]) },
    weatherConsentDue: () => !!opts.consent,
    setWeatherLocation: (on) => calls.push(['location', on]),
    // CT.App.askWeatherLocation (its own test: settings-weather.test.js)
    askWeatherLocation(after) {
      CT.ArchiveCards.Menu.open(CT.Weather.CONSENT_TEXT, [
        { label: 'Povolit', action: () => this.setWeatherLocation(true) },
        { label: 'Nepoužívat', action: () => this.setWeatherLocation(false) }], 'wide');
      CT.ArchiveCards.Menu.onClose = after;
    }
  };
  return { a, calls };
}

const root = () => document.getElementById('screen-home');
const headings = () => root().find('home-heading').map((n) => n.textContent);
const focusedText = () => root().find('focused').map((n) => n.texts().join('|'));

test('blocks in order; Sněmovna only with a live stream; gateway notice; first card focused on opening', () => {
  const { a } = app({ gateway: true, status: { failed: false, recordingPaused: true } });
  const home = new CT.HomeScreen(a);
  home.show();
  assert.deepEqual(headings(), ['Pokračovat', 'Teď běží', 'Sněmovna']);
  assert.deepEqual(root().find('home-notice').map((n) => n.textContent), [CT.Home.RECORDING_PAUSED]);
  assert.equal(root().find('home-date')[0].textContent, CT.Calendar.today(Date.now()).fullDate);
  assert.deepEqual(home.rows.map((r) => r.items.map((i) => i.key)), [
    ['ct:1', 'dvb:rec1'], ['ct:CH_1', 'ct:CH_2', 'dvb:nova', 'more'], ['psp:stream2']]);
  assert.deepEqual(home.at, { row: 0, col: 0 });
  assert.match(focusedText()[0], /Pořad A/);
  // the Teď běží card is Živě's: number, name, programme
  assert.deepEqual(home.rows[1].items[0].el.texts().slice(0, 3), ['1', 'CH_1', 'Zprávy']);
  assert.deepEqual(home.rows[2].items[0].el.texts().slice(0, 3), ['4', 'stream2', '● ŽIVĚ']);
});

test('without a gateway: no notice, Pokračovat is Archiv\'s row as it is; nothing to resume: the first row is Teď běží', () => {
  const { a } = app({ gateway: false, status: { failed: true } });
  const home = new CT.HomeScreen(a);
  home.show();
  assert.equal(root().find('home-notice').length, 0);
  assert.deepEqual(home.rows[0].items.map((i) => i.key), a.resumeStore.continueRow().map((e) => e.key));
  assert.deepEqual(home.rows[0].items.map((i) => i.key), ['ct:1', 'dvb:rec1']);
  const empty = new CT.HomeScreen(app({ gateway: true, entries: {} }).a);
  empty.show();
  assert.deepEqual(headings(), ['Teď běží', 'Sněmovna']);
  assert.equal(empty.rows[0].id, 'now');
});

test('OK: a Pokračovat card resumes, a channel plays live, "Další kanály" opens Živě; up from the first row leaves', () => {
  const { a, calls } = app({ gateway: true });
  const home = new CT.HomeScreen(a);
  home.show();
  home.onKey('enter');
  home.onKeyUp('enter');
  assert.deepEqual(calls.pop(), ['play', 'archive', 'ct:1']);
  home.onKey('down');
  home.onKey('enter');
  assert.deepEqual(calls.pop(), ['playItem', 'ct:CH_1']);
  ['right', 'right', 'right', 'right'].forEach((k) => home.onKey(k));
  home.onKey('enter');
  assert.deepEqual(calls.pop(), ['tab', 'live']);
  home.onKey('up');
  assert.equal(home.onKey('up'), false);
});

test('long OK on a Pokračovat card offers "Odebrat z řady"; removing keeps the focus in the row', () => {
  const { a } = app({ gateway: true });
  const home = new CT.HomeScreen(a);
  home.show();
  home.onKey('right');
  const at = Date.now;
  let t = 1000;
  Date.now = () => t;
  try {
    home.onKey('enter');
    t += 900;
    home.onKey('enter'); // held: the repeat after 0.8 s
  } finally {
    Date.now = at;
  }
  assert.ok(CT.ArchiveCards.Menu.isOpen());
  assert.equal(document.getElementById('ar-menu-title').textContent, 'Nahrávka');
  home.onKeyUp('enter');
  home.onKey('enter'); // "Odebrat z řady"
  assert.ok(!CT.ArchiveCards.Menu.isOpen());
  assert.deepEqual(home.rows[0].items.map((i) => i.key), ['ct:1']);
  assert.deepEqual(home.at, { row: 0, col: 0 });
  assert.equal(a.resumeStore.offer('dvb:rec1'), null);
});

test('a redraw keeps the focused card; back from the player focuses the channel that played', () => {
  const { a } = app({ gateway: true });
  const home = new CT.HomeScreen(a);
  home.show();
  home.onKey('down');
  home.onKey('right');
  a.ctState = { onAir: {}, error: null };
  a.channelList = () => [ch('ct', 'CH_24')].concat(LIST); // a channel came in before the focused one
  home.render();
  assert.equal(home.focused(), 'ct:CH_2');
  assert.deepEqual(home.at, { row: 1, col: 2 });
  home.onPlayerClosed({ kind: 'dvbLive', channelId: 'nova' });
  assert.equal(home.focused(), 'dvb:nova');
  home.hide();
  home.show(); // opened again from another tab: the first card
  assert.equal(home.focused(), 'ct:1');
});

const WEATHER = (() => { const r = response('dvb/dvb_weather_ok.json'); return CT.Weather.parse(r.status, r.body); })();

test('weather: the header lines and "Počasí na týden" after the rows, never a focus stop; asked for on showing Domů', () => {
  const at = Date.now;
  Date.now = () => Date.parse('2026-11-14T10:00:00Z'); // the fixture's today, before the wind warning ends
  try {
    const { a, calls } = app({ gateway: true, weather: WEATHER });
    const home = new CT.HomeScreen(a);
    home.show();
    assert.deepEqual(calls, [['weather', false]]);
    const box = root().find('home-weather')[0];
    assert.deepEqual(box.texts(), ['8 °C, polojasno', 'Slunce 7:13–16:19', 'Ovzduší: zhoršené', 'Pyl: olše nízká',
      'Výstraha ČHMÚ: Silný vítr do 18:00', 'Výstraha ČHMÚ: Riziko požárů (do odvolání)', WEATHER.attribution]);
    assert.ok(box.find('wx-warning')[0].classList.contains('wx-yellow'));
    assert.ok(box.find('wx-warning')[1].classList.contains('wx-orange'));
    assert.ok(box.find('wx-partly').length === 1);
    const attribution = box.find('wx-attribution')[0];
    assert.deepEqual(attribution.children.map((n) => n.tagName), ['#text']); // Samsung: plain text, no link
    assert.deepEqual(headings(), ['Pokračovat', 'Teď běží', 'Sněmovna', 'Počasí na týden']);
    const cards = root().find('wx-day');
    assert.equal(cards.length, 7);
    assert.deepEqual(cards[0].texts(), ['Dnes', '9° / 2°']);
    assert.deepEqual(cards[1].texts(), ['ne 15. 11.', '8° / 4°', 'srážky 60 %']);
    assert.deepEqual(home.rows.map((r) => r.id), ['continue', 'now', 'psp']); // the week is not a row of the focus
    home.onKey('down');
    home.onKey('down');
    home.onKey('down');
    assert.equal(home.rows[home.at.row].id, 'psp');
  } finally {
    Date.now = at;
  }
});

test('weather: without an answer the header has the date only and there is no week block', () => {
  const { a } = app({ gateway: true });
  new CT.HomeScreen(a).show();
  assert.equal(root().find('home-weather').length, 0);
  assert.equal(root().find('wx-day').length, 0);
  assert.equal(root().find('home-date').length, 1);
});

test('location question: once before the OS is asked; Povolit / Nepoužívat set the switch; Return leaves it undecided', () => {
  const Menu = CT.ArchiveCards.Menu;
  const allow = app({ gateway: true, consent: true });
  const home = new CT.HomeScreen(allow.a);
  home.show();
  assert.ok(Menu.isOpen());
  assert.equal(document.getElementById('ar-menu-title').textContent, CT.Weather.CONSENT_TEXT);
  assert.ok(document.getElementById('ar-menu').classList.contains('wide'));
  assert.deepEqual(allow.calls, []); // nothing asked before the answer
  home.onKey('enter');
  assert.ok(!Menu.isOpen());
  assert.deepEqual(allow.calls, [['location', true]]);

  const deny = app({ gateway: true, consent: true });
  const second = new CT.HomeScreen(deny.a);
  second.show();
  second.onKey('right');
  second.onKey('enter');
  assert.deepEqual(deny.calls, [['location', false]]);

  const back = app({ gateway: true, consent: true });
  const third = new CT.HomeScreen(back.a);
  third.show();
  assert.equal(third.onBack(), true);
  assert.deepEqual(back.calls, [['weather', false]]); // without the location for now, nothing stored
  third.hide();
  third.show();
  assert.ok(!Menu.isOpen(), 'not asked again in this run');
  // the next menu (Archiv's "Odebrat z řady") is one line again
  Menu.open('x', [{ label: 'Zrušit', action: null }]);
  assert.ok(!document.getElementById('ar-menu').classList.contains('wide'));
  Menu.close();
});

test('the last focusable row scrolls Domů to its end ("Počasí na týden" under it takes no focus)', () => {
  const { a } = app({ gateway: true, weather: WEATHER });
  const home = new CT.HomeScreen(a);
  home.show();
  root().scrollHeight = 2400;
  home.onKey('down');
  assert.equal(root().scrollTop, 0); // the middle row fits
  home.onKey('down');
  assert.equal(home.rows[home.at.row].id, 'psp');
  assert.equal(root().scrollTop, 2400);
  home.onKey('up');
  home.onKey('up');
  assert.equal(root().scrollTop, 0);
});
