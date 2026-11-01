'use strict';
// Back = the browser's Back (spec/web.md "Input (T29)"): the programme page, a Nastavení section, the Archiv
// sub-menu and show page and the player are history entries; popstate closes them through the app's Back, the app's
// own Back moves the history with it, Forward reopens what can be reopened.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

const H = CT.WebHistory;

/** A fake app with the layers the stack reads; back() closes the top one as the app's Back would. */
function fakeApp() {
  const app = {
    tab: 'guide',
    screens: { settings: { zone: 'choice', section: 'channels' }, archive: { menu: { level: 'main' }, page: { card: null, isOpen() { return !!this.card; } } } },
    programme: { entry: null, day: null, isOpen() { return !!this.entry; } },
    player: { request: null, isOpen() { return !!this.request; } },
    popup: false, // a long-OK menu: Back closes it first, it is no history entry
    backs: 0
  };
  app.back = () => {
    app.backs++;
    if (app.popup) app.popup = false;
    else if (app.player.request) app.player.request = null;
    else if (app.programme.entry) app.programme.entry = null;
    else if (app.screens.archive.page.card) app.screens.archive.page.card = null;
    else if (app.screens.archive.menu.level === 'sub') app.screens.archive.menu = { level: 'main' };
    else if (app.screens.settings.zone !== 'choice') app.screens.settings.zone = 'choice';
  };
  return app;
}

/** A fake window.history: records pushes and go(); the test plays the browser's popstate. */
function fakeHistory() {
  const h = { states: [null], index: 0, gone: [] };
  h.replaceState = (s) => { h.states[h.index] = s; };
  h.pushState = (s) => { h.states = h.states.slice(0, h.index + 1); h.states.push(s); h.index++; };
  h.go = (n) => { h.gone.push(n); h.index += n; };
  h.state = () => h.states[h.index];
  return h;
}

function setup() {
  const app = fakeApp();
  const history = fakeHistory();
  const reopened = [];
  const tracker = new H.Tracker(app, history, app.back, (e) => {
    reopened.push(e.type);
    if (e.type === 'programme') app.programme.entry = e.data.entry;
    if (e.type === 'player') app.player.request = e.data;
    if (e.type === 'section') app.screens.settings.zone = 'channels';
  });
  return { app, history, tracker, reopened };
}

const ENTRY = { title: 'Zprávy', start: 1000 };

test('the stack: section, Archiv sub-menu, show page, programme page, player, outermost first', () => {
  const app = fakeApp();
  assert.deepEqual(H.stack(app), []);
  app.tab = 'settings';
  app.screens.settings.zone = 'profile';
  app.screens.settings.section = 'profile';
  assert.deepEqual(H.stack(app).map((e) => e.key), ['section:profile']);
  app.tab = 'archive';
  app.screens.archive.menu = { level: 'sub', category: '3947-filmy' };
  app.screens.archive.page.card = { id: 'ct:1' };
  app.player.request = { kind: 'archive' };
  assert.deepEqual(H.stack(app).map((e) => e.type), ['sub', 'show', 'player']); // the section is another tab's
  app.tab = 'guide';
  app.programme.entry = ENTRY;
  assert.deepEqual(H.stack(app).map((e) => e.type), ['programme', 'player']);
});

test('opening the programme page pushes an entry; the browser\'s Back closes it through the app\'s Back', () => {
  const { app, history, tracker } = setup();
  assert.deepEqual(history.states, [{ ctWeb: 0 }]);
  app.programme.entry = ENTRY;
  tracker.sync();
  assert.deepEqual(history.states, [{ ctWeb: 0 }, { ctWeb: 1 }]);
  tracker.sync(); // nothing changed: nothing pushed
  assert.equal(history.states.length, 2);
  history.index = 0; // the browser's Back
  tracker.onPop(history.state());
  assert.equal(app.programme.entry, null);
  assert.equal(app.backs, 1);
  assert.deepEqual(tracker.shown, []);
});

test('player over the programme page: two entries, Back closes only the player', () => {
  const { app, history, tracker } = setup();
  app.programme.entry = ENTRY;
  tracker.sync();
  app.player.request = { kind: 'programme' };
  tracker.sync();
  assert.equal(history.index, 2);
  history.index = 1;
  tracker.onPop(history.state());
  assert.equal(app.player.request, null);
  assert.equal(app.programme.entry, ENTRY);
  assert.equal(app.backs, 1);
});

test('the app\'s own Back (Esc, a Zpět button) moves the history with it, without a second Back', () => {
  const { app, history, tracker } = setup();
  app.programme.entry = ENTRY;
  tracker.sync();
  app.back(); // Esc on the page
  tracker.sync();
  assert.deepEqual(history.gone, [-1]);
  tracker.onPop(history.state()); // the browser confirms our go(-1)
  assert.equal(app.backs, 1); // no extra Back
  assert.deepEqual(tracker.shown, []);
});

test('closing one layer and opening another in one step: go back, then push the new one', () => {
  const { app, history, tracker } = setup();
  app.programme.entry = ENTRY;
  tracker.sync();
  app.programme.entry = { title: 'Jiný', start: 2000 };
  tracker.sync();
  assert.deepEqual(history.gone, [-1]);
  tracker.onPop(history.state());
  assert.deepEqual(history.states, [{ ctWeb: 0 }, { ctWeb: 1 }]);
  assert.equal(tracker.shown[0].key, 'programme:Jiný@2000');
});

test('a popup over the show page takes the first Back; the entry still closes', () => {
  const { app, history, tracker } = setup();
  app.tab = 'archive';
  app.screens.archive.page.card = { id: 'ct:1' };
  tracker.sync();
  app.popup = true;
  history.index = 0;
  tracker.onPop(history.state());
  assert.equal(app.screens.archive.page.card, null);
  assert.equal(app.backs, 2);
});

test('Forward reopens the programme page and the player; not the Archiv show page', () => {
  const { app, history, tracker, reopened } = setup();
  app.programme.entry = ENTRY;
  tracker.sync();
  app.player.request = { kind: 'live' };
  tracker.sync();
  history.index = 0;
  tracker.onPop(history.state());
  assert.deepEqual(H.stack(app), []);
  history.index = 2; // Forward twice (the browser's history menu)
  tracker.onPop(history.state());
  assert.deepEqual(reopened, ['programme', 'player']);
  assert.equal(tracker.shown.length, 2);

  const s = setup();
  s.app.tab = 'archive';
  s.app.screens.archive.page.card = { id: 'ct:1' };
  s.tracker.sync();
  s.history.index = 0;
  s.tracker.onPop(s.history.state());
  s.history.index = 1;
  s.tracker.onPop(s.history.state());
  assert.deepEqual(s.reopened, []);
  assert.deepEqual(s.history.gone, [-1]); // the dead Forward step is undone
});
