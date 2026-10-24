'use strict';
// "Kanály" household default (spec/apps.md "Household default (T11)") against
// spec/fixtures/household_default_cases.json, plus the gateway answer, the stored state and the profile around it.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT, json } = require('./load');

const cases = json('household_default_cases.json');
const H = CT.Household;
const LABELS = { empty: 'empty', builtin: 'builtin' };

test('migrate: golden cases', () => {
  assert.ok(cases.migrate.length >= 5);
  for (const c of cases.migrate) assert.equal(H.migrate(c.stored), c.own, c.name);
  assert.equal(H.migrate(null), false, 'nothing stored follows the default');
});

test('sequences: golden cases', () => {
  assert.ok(cases.sequences.length >= 3);
  for (const c of cases.sequences) {
    let state = c.start;
    c.steps.forEach((step, i) => {
      const before = JSON.stringify(state);
      const next = H.reduce(state, step.event, LABELS);
      assert.deepEqual(next, step.state, c.name + ' step ' + i + ' ' + step.event.type);
      assert.equal(H.effective(next, LABELS), step.effective, c.name + ' step ' + i + ' effective');
      assert.equal(JSON.stringify(state), before, c.name + ' step ' + i + ' (input not mutated)');
      state = next;
    });
  }
});

test('parse: prefs or null, the version required', () => {
  const ok = H.parse(200, JSON.stringify({ channelPrefs: { order: ['ct:CH_24', 7], hidden: ['ct:CH_5'] }, version: 3, updated: 'x' }));
  assert.deepEqual(ok, { channelPrefs: { order: ['ct:CH_24'], hidden: ['ct:CH_5'], sourcesOff: [] }, version: 3 });
  assert.deepEqual(H.parse(200, '{"channelPrefs": null, "version": 0, "updated": null}'), { channelPrefs: null, version: 0 });
  for (const body of ['{"channelPrefs": null}', '{"channelPrefs": null, "version": "2"}', '{"channelPrefs": null, "version": -1}',
    '{"channelPrefs": null, "version": 1.5}', '{"channelPrefs": [], "version": 2}', '{"channelPrefs": "x", "version": 2}',
    '{"version": 2}', 'nope']) {
    assert.throws(() => H.parse(200, body), CT.CtError, body);
  }
  assert.throws(() => H.parse(401, '{"error": "x"}'), (e) => e.status === '401');
});

test('list: a malformed answer keeps the cached default (the app ignores the failed parse)', () => {
  const list = new H.HouseholdList({ store: memoryStore(), legacy: () => null });
  list.defaults(H.parse(200, JSON.stringify({ channelPrefs: D1, version: 1 })));
  assert.throws(() => list.defaults(H.parse(200, '{"channelPrefs": [], "version": 2}')), CT.CtError);
  assert.deepEqual(list.effective(), D1);
  assert.equal(list.state.version, 1);
  list.defaults(H.parse(200, '{"channelPrefs": null, "version": 2}'));
  assert.deepEqual(list.effective(), prefs(), 'an explicit null clears it');
});

test('fetchDefaults: GET /dvb/defaults with the app key as the Authorization header', async () => {
  const calls = [];
  const config = new CT.DvbConfig('http://pi.local:8098', 'k'.repeat(32));
  const client = new CT.DvbClient(config, (r) => {
    calls.push(r);
    return Promise.resolve({ status: 200, body: '{"channelPrefs": {"order": ["ct:CH_2"], "hidden": [], "sourcesOff": []}, "version": 5}' });
  });
  const d = await H.fetchDefaults(client);
  assert.equal(calls[0].method, 'GET');
  assert.equal(calls[0].url, 'http://pi.local:8098/dvb/defaults');
  assert.equal(calls[0].headers.Authorization, 'Bearer ' + 'k'.repeat(32));
  assert.equal(d.version, 5);
  assert.deepEqual(d.channelPrefs.order, ['ct:CH_2']);
});

// --- on the TV ------------------------------------------------------------------------------------

const prefs = (order, hidden, sourcesOff) => ({ order: order || [], hidden: hidden || [], sourcesOff: sourcesOff || [] });
const D1 = prefs(['ct:CH_24'], ['ct:CH_5']);
const D2 = prefs(['ct:CH_4'], [], ['psp']);

function memoryStore(value) {
  const store = { value: value === undefined ? null : value };
  store.load = () => store.value;
  store.save = (v) => { store.value = JSON.parse(JSON.stringify(v)); };
  return store;
}

test('list: a fresh TV follows the default, which survives a restart (offline start)', () => {
  const store = memoryStore();
  const list = new H.HouseholdList({ store, legacy: () => null });
  assert.deepEqual(list.effective(), prefs(), 'built-in order without a default');
  assert.equal(list.defaults({ channelPrefs: D1, version: 1 }), true);
  assert.deepEqual(list.effective(), D1);
  assert.equal(list.defaults({ channelPrefs: D1, version: 1 }), false, 'the same version changes nothing');
  const again = new H.HouseholdList({ store, legacy: () => { throw new Error('legacy read again'); } });
  assert.deepEqual(again.effective(), D1);
  assert.equal(again.state.version, 1);
});

test('list: prefs stored before T11 make the list the TV\'s own, once', () => {
  const store = memoryStore();
  const list = new H.HouseholdList({ store, legacy: () => JSON.stringify(prefs([], ['ct:CH_6'])) });
  assert.equal(list.state.own, true);
  list.defaults({ channelPrefs: D1, version: 1 });
  assert.deepEqual(list.effective(), prefs([], ['ct:CH_6']), 'the default does not apply to an own list');
  assert.equal(store.value.own, true);
  const empty = new H.HouseholdList({ store: memoryStore(), legacy: () => '{"order": [], "hidden": []}' });
  assert.equal(empty.state.own, false);
});

test('list: change, reset, another gateway', () => {
  const list = new H.HouseholdList({ store: memoryStore(), legacy: () => null });
  list.defaults({ channelPrefs: D1, version: 4 });
  const changed = CT.ChannelPrefs.toggle(list.effective(), 'ct:CH_1');
  list.change(changed);
  assert.deepEqual(list.effective(), prefs(['ct:CH_24'], ['ct:CH_5', 'ct:CH_1']), 'starts from what the TV showed');
  list.defaults({ channelPrefs: D2, version: 5 });
  assert.deepEqual(list.effective(), changed, 'own: a newer default is only cached');
  list.reset();
  assert.deepEqual(list.effective(), D2, 'Obnovit výchozí follows the default again');
  assert.deepEqual(list.state.local, prefs());
  list.forget();
  assert.deepEqual(list.effective(), prefs(), 'another gateway: built-in order until its default arrives');
  assert.equal(list.defaults({ channelPrefs: D1, version: 1 }), true, 'its version 1 is new');
});

// --- with the profile (T08) -----------------------------------------------------------------------

const P = CT.Profile;
const config = new CT.DvbConfig('http://pi.local:8098', 'k'.repeat(32));

/** The app's wiring: the TV shows household.shown(...) after ProfileSync applies settings, changes go to both. */
function rig() {
  const gw = { settings: { v: 1, channelPrefs: prefs([], ['ct:CH_2']), thumbnails: true }, version: 1, created: null };
  const transport = (r) => {
    const body = r.body ? JSON.parse(r.body) : null;
    if (r.url.endsWith('/dvb/profiles')) gw.created = body.settings;
    if (r.url.endsWith('/logout')) return Promise.resolve({ status: 204, body: '' });
    return Promise.resolve({ status: 200, body: JSON.stringify({ token: 'tok', name: 'Jana', settings: gw.created || gw.settings, version: gw.version }) });
  };
  const household = new H.HouseholdList({ store: memoryStore(), legacy: () => null });
  const tv = { channelPrefs: household.effective(), thumbnails: true };
  const sync = new P.ProfileSync({
    store: memoryStore(),
    client: () => new P.ProfileClient(config, transport),
    current: () => tv,
    apply: (s) => {
      tv.channelPrefs = household.shown(s, sync.signedIn());
      tv.thumbnails = s.thumbnails;
    },
    timers: { set: () => 0, clear: () => {} }
  });
  const reset = () => {
    // the app's resetChannelPrefs()
    if (sync.signedIn()) return change(household.defaultCopy());
    household.reset();
    tv.channelPrefs = household.effective();
    sync.changed();
  };
  const change = (p) => {
    if (!sync.signedIn()) household.change(p);
    tv.channelPrefs = p;
    sync.changed();
  };
  const defaults = (answer) => {
    if (household.defaults(answer) && !sync.signedIn()) tv.channelPrefs = household.effective();
  };
  return { gw, tv, sync, household, change, reset, defaults };
}

test('profile: signing out returns to following the default, also a newer one that came meanwhile', async () => {
  const r = rig();
  r.defaults({ channelPrefs: D1, version: 1 });
  assert.deepEqual(r.tv.channelPrefs, D1);
  await r.sync.login('Jana', 'heslo');
  assert.deepEqual(r.tv.channelPrefs, prefs([], ['ct:CH_2']), 'signed in, the profile applies');
  r.defaults({ channelPrefs: D2, version: 2 });
  assert.deepEqual(r.tv.channelPrefs, prefs([], ['ct:CH_2']), 'the default does not touch a signed-in TV');
  r.change(prefs([], ['ct:CH_2', 'ct:CH_6']));
  assert.equal(r.household.state.own, false, 'a change in the profile does not make the TV\'s list its own');
  r.sync.logout();
  assert.deepEqual(r.tv.channelPrefs, D2);
  assert.notDeepEqual(D1, D2);
});

test('profile: signed in, Obnovit výchozí gives the profile a copy of the default; the TV\'s own list stays', async () => {
  const r = rig();
  r.defaults({ channelPrefs: D1, version: 1 });
  const own = CT.ChannelPrefs.toggle(r.tv.channelPrefs, 'ct:CH_1');
  r.change(own);
  await r.sync.login('Jana', 'heslo');
  assert.notDeepEqual(r.tv.channelPrefs, D1);
  r.reset();
  assert.deepEqual(r.tv.channelPrefs, D1, 'the profile shows the household default');
  assert.ok(r.sync.state.pending, 'sent to the profile like any change');
  assert.deepEqual(r.sync.state.active.channelPrefs, D1);
  r.tv.channelPrefs.hidden.push('ct:CH_4');
  assert.deepEqual(r.household.state.default, D1, 'a copy, not the cached default itself');
  assert.equal(r.household.state.own, true);
  assert.deepEqual(r.household.state.local, own, 'the TV\'s own list untouched');
  r.sync.logout();
  assert.deepEqual(r.tv.channelPrefs, own);

  const none = rig();
  await none.sync.login('Jana', 'heslo');
  none.reset();
  assert.deepEqual(none.tv.channelPrefs, prefs(), 'without a default: the built-in order');
});

test('profile: signing out returns to the TV\'s own list; a new profile starts with what the TV shows', async () => {
  const r = rig();
  r.defaults({ channelPrefs: D1, version: 1 });
  const own = CT.ChannelPrefs.move(['ct:CH_1', 'ct:CH_24'], r.tv.channelPrefs, 'ct:CH_1', -1);
  r.change(own);
  r.defaults({ channelPrefs: D2, version: 2 });
  await r.sync.create('Jana', 'heslo', 'heslo');
  assert.deepEqual(r.gw.created.channelPrefs, own, 'the profile starts with the TV\'s list');
  r.sync.logout();
  assert.deepEqual(r.tv.channelPrefs, own);

  const fresh = rig();
  fresh.defaults({ channelPrefs: D1, version: 1 });
  await fresh.sync.create('Petr', 'heslo', 'heslo');
  assert.deepEqual(fresh.gw.created.channelPrefs, D1, 'following the default: the profile starts with the default');
  assert.equal(fresh.gw.created.thumbnails, true, 'the thumbnails switch travels as before');
});
