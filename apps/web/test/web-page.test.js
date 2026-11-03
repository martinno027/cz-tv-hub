'use strict';
// Forward in the history opens again what Back closed (web-page.js reopen), through the app's own calls.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

test('reopen: a Nastavení section (on its tab), the programme page, the player', () => {
  const calls = [];
  const app = {
    tab: 'live',
    showTab: (t) => { calls.push('tab:' + t); app.tab = t; },
    screens: { settings: { openSection: (s) => calls.push('section:' + s) } },
    openProgramme: (e, d) => calls.push('programme:' + e.title + ':' + d),
    play: (r) => calls.push('play:' + r.kind)
  };
  CT.WebPage.reopen(app, { type: 'section', data: 'server' });
  CT.WebPage.reopen(app, { type: 'programme', data: { entry: { title: 'Zprávy' }, day: '2026-11-01' } });
  CT.WebPage.reopen(app, { type: 'player', data: { kind: 'live' } });
  CT.WebPage.reopen(app, { type: 'show', data: null }); // cannot: nothing happens
  assert.deepEqual(calls, ['tab:settings', 'section:server', 'programme:Zprávy:2026-11-01', 'play:live']);
  assert.equal(app.zone, 'content');
});

test('player hint on the web: keyboard words; the audio part only with several audio tracks; M mutes (T34)', () => {
  const H = CT.WebPage.hint;
  assert.equal(H('live', 1), 'Mezerník pauza · ▲ ▼ kanál · 0–9 číslo kanálu · M ztlumení · F celá obrazovka · Esc zpět');
  assert.equal(H('live', 2), 'Mezerník pauza · ▲ ▼ kanál · 0–9 číslo kanálu · A zvuk · M ztlumení · F celá obrazovka · Esc zpět');
  assert.equal(H('other', 0), 'Mezerník pauza · ◀ ▶ posun o 10 s · M ztlumení · F celá obrazovka · Esc zpět');
  assert.equal(H('other', 3), 'Mezerník pauza · ◀ ▶ posun o 10 s · ▲ zvuk · M ztlumení · F celá obrazovka · Esc zpět');
  assert.equal(H('timeline', 1), 'Mezerník pauza · ◀ ▶ 10 s, podržet = převíjení · ▲ ▼ začátek pořadu · M ztlumení · F celá obrazovka · Esc zpět');
  assert.match(H('timeline', 2), / · A nebo podržet Enter zvuk · M ztlumení · F celá obrazovka · Esc zpět$/);
  for (const kind of ['live', 'other', 'timeline']) assert.ok(!/\bOK\b|AUDIO|Zpět konec/.test(H(kind, 2)), kind);
  // through the player: tracks only once a stream is there
  const player = (target, n) => ({ target, backend: { audioTracks: () => new Array(n).fill({}) } });
  assert.match(CT.WebPage.playerHint('live', player(null, 2)), /0–9 číslo kanálu · M/);
  assert.match(CT.WebPage.playerHint('live', player({}, 2)), /A zvuk/);
  assert.match(CT.WebPage.playerHint('live', { target: {}, backend: { audioTracks: () => { throw new Error('x'); } } }), /· M ztlumení · F/);
});

test('mute button in the player bar (T34): the state\'s icon, what a click does as its label', () => {
  const classes = new Set();
  const b = { attrs: {}, textContent: '', setAttribute(k, v) { this.attrs[k] = v; },
    classList: { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)) } };
  CT.WebPage.renderMute(b, false);
  assert.equal(b.textContent, '\u{1F50A}'); // 🔊 sound on
  assert.equal(b.attrs.title, 'Vypnout zvuk');
  assert.equal(b.attrs['aria-label'], 'Vypnout zvuk');
  assert.equal(classes.has('muted'), false);
  CT.WebPage.renderMute(b, true);
  assert.equal(b.textContent, '\u{1F507}'); // 🔇 muted
  assert.equal(b.attrs.title, 'Zapnout zvuk');
  assert.equal(classes.has('muted'), true);
});
