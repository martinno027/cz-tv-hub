'use strict';
// Mouse like a web page (spec/web.md "Input (T29)"): the remote's focus only while the keyboard is used; one click
// acts on the clicked item directly (no focus walking through others); right click / long press = long OK.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

const F = CT.WebFocus;

test('keyboard vs mouse: the focus shows with the first key and goes with the first mouse action', () => {
  const m = new F.Mode();
  assert.equal(m.pointer, true); // a page starts without a remote focus
  assert.deepEqual(m.onKey('down'), { reveal: true, act: false }); // the first arrow only shows where the focus is
  assert.deepEqual(m.onKey('down'), { reveal: false, act: true });
  assert.equal(m.onPointer(), true); // hide now
  assert.equal(m.onPointer(), false); // already hidden
  assert.deepEqual(m.onKey('enter'), { reveal: true, act: false }); // OK on an unseen item would surprise
  m.onPointer();
  assert.deepEqual(m.onKey('back'), { reveal: true, act: true }); // Esc/Backspace, digits, Space act at once
  m.onPointer();
  assert.deepEqual(m.onKey('d5'), { reveal: true, act: true });
});

test('hideFocus removes every remote focus (no second highlighted item next to the selection)', () => {
  const removed = [];
  const els = [1, 2].map((n) => ({ classList: { remove: (c) => removed.push(n + c) } }));
  F.hideFocus({ querySelectorAll: (s) => (s === '.focused' ? els : []) });
  assert.deepEqual(removed, ['1focused', '2focused']);
});

/** A fake element: `selectors` it matches, `parent` with children (for indexes), `extra` fields. */
function node(selectors, parent, extra) {
  const e = Object.assign({ dataset: {}, getAttribute(k) { return this.attrs ? this.attrs[k] : null; } }, extra || {});
  e.parentNode = parent || null;
  if (parent) parent.children.push(e);
  e.closest = (sel) => (sel.split(',').some((s) => selectors.includes(s.trim())) ? e : null);
  return e;
}

function box() {
  return { children: [] };
}

function fakeApp() {
  const sends = [];
  const app = {
    zone: 'tabs',
    screens: {
      live: { focus: 0, cards: [] },
      guide: { zone: 'grid', dayFocus: 7, focusRow: 0, focusSlot: 0, anchor: 0, liveMode: true, rows: [] },
      archive: { zone: 'menu', menuFocus: 0, sortFocus: 0, grid: { focus: 0 }, removed: 0, continueGrid: false,
        isContinue() { return this.continueGrid; }, offerRemove() { this.removed++; },
        page: { zone: 'buttons', button: 0, episodes: [], episode: 0 } },
      settings: { zone: 'choice', items: [], chItems: [] }
    },
    programme: { focus: 0 }
  };
  return { app, sends, ok: () => sends.push('enter') };
}

test('one click acts: a Živě card becomes the focus and gets OK, exactly one OK, no arrows', () => {
  const { app, sends, ok } = fakeApp();
  const grid = box();
  const cards = [0, 1, 2, 3].map(() => node(['#screen-live .card'], grid));
  app.screens.live.cards = cards.map((el) => ({ el }));
  assert.equal(F.act(app, cards[3], false, ok), true);
  assert.equal(app.screens.live.focus, 3);
  assert.equal(app.zone, 'content');
  assert.deepEqual(sends, ['enter']);
});

test('one click acts in the guide: a cell and a day chip, without walking from the old focus', () => {
  const { app, sends, ok } = fakeApp();
  const cell = node(['#grid-rows .cell']);
  app.screens.guide.rows = [{ slots: [{ el: {}, start: 0, end: 10 }] }, { slots: [{ el: {}, start: 0, end: 600000 }, { el: cell, start: 600000, end: 1800000 }] }];
  assert.equal(F.act(app, cell, false, ok), true);
  const g = app.screens.guide;
  assert.deepEqual([g.zone, g.focusRow, g.focusSlot, g.liveMode], ['grid', 1, 1, false]);
  assert.ok(g.anchor >= 600000 && g.anchor < 1800000);
  const days = box();
  const chips = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map(() => node(['#days .day'], days));
  F.act(app, chips[2], false, ok); // a chip far from "Dnes" (7): set directly, not stepped towards
  assert.equal(g.zone, 'days');
  assert.equal(g.dayFocus, 2);
  assert.deepEqual(sends, ['enter', 'enter']);
});

test('one click acts in the Archiv: a menu item, a card; a click on nothing known does nothing', () => {
  const { app, sends, ok } = fakeApp();
  const track = box();
  const items = [0, 1, 2, 3, 4].map(() => node(['#ar-side .ar-menu-item'], track));
  F.act(app, items[4], false, ok);
  assert.equal(app.screens.archive.menuFocus, 4);
  assert.equal(app.screens.archive.zone, 'menu');
  const card = node(['.ar-card'], null, { dataset: { index: '17' } });
  F.act(app, card, false, ok);
  assert.equal(app.screens.archive.grid.focus, 17);
  assert.equal(app.screens.archive.zone, 'grid');
  assert.equal(F.act(app, node(['.ar-note']), false, ok), false);
  assert.deepEqual(sends, ['enter', 'enter']);
});

test('right click / long press: long OK only where the screen has one', () => {
  const { app, sends, ok } = fakeApp();
  const card = node(['.ar-card'], null, { dataset: { index: '2' } });
  assert.equal(F.act(app, card, true, ok), true);
  assert.equal(app.screens.archive.removed, 0); // not "Pokračovat": nothing to offer
  app.screens.archive.continueGrid = true;
  F.act(app, card, true, ok);
  assert.equal(app.screens.archive.removed, 1);
  assert.deepEqual(sends, []); // long OK is no OK
  const grid = box();
  const live = node(['#screen-live .card'], grid);
  app.screens.live.cards = [{ el: live }];
  assert.equal(F.act(app, live, true, ok), false);
});

test('Nastavení: a choice, a channel control, a form button by click', () => {
  const { app, sends, ok } = fakeApp();
  const choice = node(['#set-choice .choice'], null, { attrs: { 'data-section': 'server' } });
  F.act(app, choice, false, ok);
  assert.equal(app.screens.settings.section, 'server');
  const up = node(['#set-channels .ch-btn']);
  app.screens.settings.chItems = [{ controls: [{}, {}, {}] }, { controls: [{}, up, {}] }];
  F.act(app, up, false, ok);
  assert.deepEqual([app.screens.settings.zone, app.screens.settings.chRow, app.screens.settings.chCol], ['channels', 1, 1]);
  const save = node(['#set-save']);
  app.screens.settings.items = [{}, {}, save];
  F.act(app, save, false, ok);
  assert.deepEqual([app.screens.settings.zone, app.screens.settings.focus], ['form', 2]);
  assert.equal(sends.length, 3);
});

test('guide detail follows the resting mouse (0.2 s), leaving the grid shows the focus again; no focus change', () => {
  let next = 0;
  const timers = { pending: new Map(), set(fn, ms) { this.pending.set(++next, { fn, ms }); return next; }, clear(id) { this.pending.delete(id); } };
  const run = () => { const p = Array.from(timers.pending.values()); timers.pending.clear(); p.forEach((t) => t.fn()); };
  const a = {};
  const b = {};
  const shown = [];
  const guide = { focusRow: 0, focusSlot: 0, scrollRow: 0, scrollX: 0,
    rows: [{ slots: [{ el: {}, item: 'focused one' }, { el: a, item: 'A' }, { el: b, item: 'B' }] }],
    focused() { return this.rows[this.focusRow].slots[this.focusSlot]; }, showDetail(e) { shown.push(e); } };
  const h = new F.HoverDetail(guide, timers);
  h.over(a);
  assert.equal(Array.from(timers.pending.values())[0].ms, 200);
  h.over(b); // moved on before 0.2 s: A never shows
  run();
  assert.deepEqual(shown, ['B']);
  h.over(null); // between cells: B stays
  run();
  assert.deepEqual(shown, ['B']);
  h.out();
  assert.deepEqual(shown, ['B', 'focused one']);
  h.out(); // already back
  assert.equal(shown.length, 2);
  assert.deepEqual([guide.focusRow, guide.focusSlot, guide.scrollRow, guide.scrollX], [0, 0, 0, 0]);
  h.over(a);
  h.cancel(); // a press: no late change
  run();
  assert.equal(shown.length, 2);
});
