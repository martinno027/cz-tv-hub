'use strict';
// spec/apps.md "Archiv" → Layout, "Loading is always visible (T52)": the loader of a part waiting for the gateway
// shows only after 150 ms, a newer request replaces the waited one (its loader stays), only the newest answer
// counts; new content = over the part, its next page = below it.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

const L = CT.Loading;

/** Timers driven by hand: `advance(ms)` runs what is due. */
function clock() {
  let now = 0;
  let id = 0;
  const due = new Map();
  return {
    set: (fn, ms) => { due.set(++id, { at: now + ms, fn }); return id; },
    clear: (t) => { due.delete(t); },
    advance(ms) {
      now += ms;
      for (const [k, t] of [...due]) if (t.at <= now) { due.delete(k); t.fn(); }
    },
    pending: () => due.size
  };
}

test('the loader shows only after 150 ms: a fast answer shows none', () => {
  assert.equal(L.DELAY_MS, 150);
  const c = clock();
  let shown = 0;
  const w = new L.Wait(() => shown++, c);
  const t = w.begin(L.REPLACE);
  assert.equal(w.place(), 'none');
  c.advance(149);
  assert.equal(w.place(), 'none');
  assert.equal(shown, 0);
  assert.equal(w.finish(t), true); // answered at 149 ms: no loader ever
  c.advance(10);
  assert.equal(shown, 0);
  assert.equal(w.place(), 'none');
  assert.equal(c.pending(), 0);
});

test('a slow answer: the loader is over the part from 150 ms until the answer', () => {
  const c = clock();
  let shown = 0;
  const w = new L.Wait(() => shown++, c);
  const t = w.begin(L.REPLACE);
  c.advance(150);
  assert.equal(shown, 1);
  assert.equal(w.place(), 'over');
  assert.equal(w.waiting(), true);
  assert.equal(w.finish(t), true);
  assert.equal(w.place(), 'none');
  assert.equal(w.waiting(), false);
});

test('a next page: a small loader below the last row', () => {
  const c = clock();
  const w = new L.Wait(null, c);
  assert.equal(L.kindOfPage(1), L.REPLACE);
  assert.equal(L.kindOfPage(2), L.MORE);
  w.begin(L.kindOfPage(3));
  c.advance(150);
  assert.equal(w.place(), 'below');
});

test('a newer choice replaces the waited one: its loader stays, the older answer is dropped', () => {
  const c = clock();
  let shown = 0;
  const w = new L.Wait(() => shown++, c);
  const first = w.begin(L.REPLACE);
  c.advance(100);
  const second = w.begin(L.REPLACE); // the wait goes on from where it was
  c.advance(50);
  assert.equal(shown, 1); // 150 ms after the first choice, not after the second
  assert.equal(w.place(), 'over');
  assert.equal(w.current(first), false);
  assert.equal(w.finish(first), false); // the older answer: dropped, the loader stays
  assert.equal(w.place(), 'over');
  assert.equal(w.current(second), true);
  assert.equal(w.finish(second), true);
  assert.equal(w.place(), 'none');
  assert.equal(w.finish(second), false); // only once
});

test('a new choice while a next page is waited for: over the part, the page dropped', () => {
  const c = clock();
  const w = new L.Wait(null, c);
  const more = w.begin(L.MORE);
  c.advance(150);
  assert.equal(w.place(), 'below');
  const fresh = w.begin(L.REPLACE);
  assert.equal(w.place(), 'over');
  assert.equal(w.finish(more), false);
  // a next page asked meanwhile does not make the new content "more"
  const page = w.begin(L.MORE);
  assert.equal(w.place(), 'over');
  assert.equal(w.finish(fresh), false);
  assert.equal(w.finish(page), true);
});

test('cancel: the part closed, the loader goes and late answers are dropped', () => {
  const c = clock();
  let shown = 0;
  const w = new L.Wait(() => shown++, c);
  const t = w.begin(L.REPLACE);
  w.cancel();
  c.advance(500);
  assert.equal(shown, 0);
  assert.equal(w.place(), 'none');
  assert.equal(w.finish(t), false);
  assert.equal(c.pending(), 0);
});
