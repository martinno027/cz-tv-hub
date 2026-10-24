'use strict';
// "Profil" (spec/apps.md) against spec/fixtures/profile_cases.json, plus the gateway client and ProfileSync.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT, json } = require('./load');

const cases = json('profile_cases.json');
const P = CT.Profile;

test('names: golden cases', () => {
  assert.ok(cases.names.length >= 10);
  for (const c of cases.names) {
    const label = JSON.stringify(c.name);
    if (!c.valid) {
      assert.equal(P.normalizeName(c.name), null, label);
      assert.equal(P.nameKey(c.name), null, label);
      continue;
    }
    assert.equal(P.normalizeName(c.name), c.normalized, label);
    assert.equal(P.nameKey(c.name), c.key, label);
  }
});

test('names: NFC, so a decomposed "á" is the same name', () => {
  assert.equal(P.normalizeName('ja\u0301na'), 'jána');
  assert.equal(P.nameKey('ja\u0301na'), P.nameKey('Jána'));
  assert.equal(P.normalizeName('\u{1F4FA}'.repeat(32)), '\u{1F4FA}'.repeat(32), '32 characters, not 32 UTF-16 units');
  assert.equal(P.normalizeName('\u{1F4FA}'.repeat(33)), null);
});

test('passwords: golden cases', () => {
  for (const c of cases.passwords) assert.equal(P.validPassword(c.password), c.valid, c.password.length + ' chars');
  assert.equal(P.validPassword(undefined), false);
});

test('document: golden cases', () => {
  for (const c of cases.document) {
    const before = JSON.stringify(c.incoming);
    const applied = P.Document.apply(c.incoming);
    assert.deepEqual(applied, c.applied, c.name + ' (applied)');
    const exported = P.Document.exportDoc(c.incoming, { channelPrefs: applied.channelPrefs, thumbnails: c.changeThumbnails });
    assert.deepEqual(exported, c.exported, c.name + ' (exported)');
    assert.equal(JSON.stringify(c.incoming), before, c.name + ' (input not mutated)');
  }
});

test('document: anything that is not a document gives the defaults', () => {
  for (const doc of [null, undefined, 'x', [], { channelPrefs: 'x', thumbnails: 'no' }]) {
    assert.deepEqual(P.Document.apply(doc), { channelPrefs: { order: [], hidden: [], sourcesOff: [] }, thumbnails: true });
  }
});

test('sync: golden cases', () => {
  for (const c of cases.sync) {
    let state = c.start;
    c.steps.forEach((step, i) => {
      const r = P.reduce(state, step.event);
      assert.deepEqual(r.state, step.state, c.name + ' step ' + i + ' ' + step.event.type);
      assert.equal(r.message, step.message || null, c.name + ' step ' + i + ' message');
      state = r.state;
    });
  }
});

// --- gateway client -------------------------------------------------------------------------------

const KEY = 'k'.repeat(32);
const config = new CT.DvbConfig('http://pi.local:8098', KEY);

function fakeTransport(answer) {
  const calls = [];
  const transport = (request) => {
    calls.push(request);
    const r = answer(request);
    return r instanceof Error ? Promise.reject(r) : Promise.resolve(r);
  };
  return { calls, transport };
}

test('client: headers, bodies and parsed answers', async () => {
  const t = fakeTransport((r) => {
    if (r.url.endsWith('/dvb/profiles/login')) {
      return { status: 200, body: JSON.stringify({ token: 'tok', name: 'Jana', settings: { v: 1, thumbnails: false }, version: 3 }) };
    }
    if (r.method === 'PUT') return { status: 200, body: '{"version": 4, "updated": "2026-10-24T10:00:00Z"}' };
    if (r.method === 'GET') return { status: 200, body: '{"name": "Jana", "settings": "broken", "version": "7"}' };
    return { status: 204, body: '' };
  });
  const client = new P.ProfileClient(config, t.transport);
  const p = await client.login('Jana', 'heslo');
  assert.deepEqual(p, { token: 'tok', name: 'Jana', settings: { v: 1, thumbnails: false }, version: 3 });
  assert.equal(t.calls[0].method, 'POST');
  assert.equal(t.calls[0].headers.Authorization, 'Bearer ' + KEY);
  assert.equal(t.calls[0].headers['Content-Type'], 'application/json');
  assert.equal(t.calls[0].headers['X-Cztvhub-Profile'], undefined, 'no token before signing in');
  assert.deepEqual(JSON.parse(t.calls[0].body), { name: 'Jana', password: 'heslo' });

  assert.equal(await client.put('tok', { v: 1 }), 4);
  assert.equal(t.calls[1].headers['X-Cztvhub-Profile'], 'tok');
  assert.deepEqual(JSON.parse(t.calls[1].body), { settings: { v: 1 } });

  const g = await client.get('tok');
  assert.deepEqual(g.settings, { v: 1 }, 'a settings value that is not an object = an empty document');
  assert.equal(g.version, null, 'a version that is not a number stays unknown');
  assert.equal(t.calls[2].body, null);

  await client.logout('tok');
  assert.equal(t.calls[3].url, 'http://pi.local:8098/dvb/profile/logout');
  assert.equal(t.calls[3].headers['X-Cztvhub-Profile'], 'tok');
});

test('client: the gateway\'s Czech error text and status, network errors as unreachable', async () => {
  for (const [status, text] of [[409, 'Profil s tímto jménem už existuje'], [429, 'Příliš mnoho pokusů, zkuste to za minutu'],
    [401, 'Nesprávné jméno nebo heslo'], [507, 'Už je 20 profilů'], [400, 'Jméno má 1–32 znaků']]) {
    const client = new P.ProfileClient(config, fakeTransport(() => ({ status, body: JSON.stringify({ error: text }) })).transport);
    await assert.rejects(client.create('Jana', '1234', { v: 1 }), (e) => e.userMessage === text && e.status === String(status));
  }
  const html = new P.ProfileClient(config, fakeTransport(() => ({ status: 500, body: '<html>' })).transport);
  await assert.rejects(html.get('t'), (e) => e.userMessage === P.MESSAGES.GENERIC);
  const noToken = new P.ProfileClient(config, fakeTransport(() => ({ status: 200, body: '{"name": "Jana"}' })).transport);
  await assert.rejects(noToken.login('Jana', '1234'), (e) => e.userMessage === P.MESSAGES.GENERIC);
  const down = new P.ProfileClient(config, fakeTransport(() => new Error('timeout')).transport);
  await assert.rejects(down.put('t', {}), (e) => e.userMessage === CT.DvbMessages.UNREACHABLE && e.status === 'network');
});

// --- ProfileSync ----------------------------------------------------------------------------------

/** A TV (its settings and storage), a fake gateway and manual timers. */
function rig(saved) {
  const tv = { settings: { channelPrefs: CT.ChannelPrefs.empty(), thumbnails: true } };
  const stored = { value: saved || null };
  const timers = [];
  const gw = { version: 1, settings: null, down: false, unauthorized: false, badKey: false, calls: [], hold: null };
  const transport = (r) => {
    gw.calls.push(r);
    if (gw.down) return Promise.reject(new Error('network'));
    if (gw.unauthorized) return Promise.resolve({ status: 401, body: '{"error": "Profil byl odhlášen", "code": "profileSignedOut"}' });
    // the app key's 401 (sent with WWW-Authenticate: Bearer): no code, the session is fine
    if (gw.badKey) return Promise.resolve({ status: 401, body: '{"error": "Chybí nebo nesedí klíč"}' });
    const body = r.body ? JSON.parse(r.body) : null;
    let answer;
    if (r.url.endsWith('/dvb/profiles') || r.url.endsWith('/login')) {
      if (body.password === 'spatne') return Promise.resolve({ status: 401, body: '{"error": "Nesprávné jméno nebo heslo"}' });
      if (r.url.endsWith('/dvb/profiles')) gw.settings = body.settings;
      answer = { status: 200, body: JSON.stringify({ token: 'tok-' + body.name, name: body.name, settings: gw.settings, version: gw.version }) };
    } else if (r.method === 'PUT') {
      gw.settings = body.settings;
      gw.version += 1;
      answer = { status: 200, body: JSON.stringify({ version: gw.version }) };
    } else if (r.method === 'GET') {
      answer = { status: 200, body: JSON.stringify({ name: 'Jana', settings: gw.settings, version: gw.version }) };
    } else {
      answer = { status: 204, body: '' };
    }
    if (gw.hold) return new Promise((resolve) => { gw.hold = () => resolve(answer); });
    return Promise.resolve(answer);
  };
  const events = { applied: 0, signedOut: 0 };
  const sync = new P.ProfileSync({
    store: { load: () => stored.value, save: (v) => { stored.value = v ? JSON.parse(JSON.stringify(v)) : null; } },
    client: () => new P.ProfileClient(config, transport),
    current: () => tv.settings,
    apply: (s) => { tv.settings = s; events.applied += 1; },
    onSignedOut: () => { events.signedOut += 1; },
    timers: { set: (fn, ms) => timers.push({ fn, ms }) - 1, clear: (id) => { if (id !== null && timers[id]) timers[id] = null; } }
  });
  const fire = async () => {
    const pending = timers.map((t, i) => (t ? i : -1)).filter((i) => i >= 0);
    pending.forEach((i) => { const t = timers[i]; timers[i] = null; t.fn(); });
    await flush();
    return pending.length;
  };
  return { tv, stored, timers, gw, sync, events, fire, change: (s) => { tv.settings = s; sync.changed(); } };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));
const hidden = (keys) => ({ order: [], hidden: keys, sourcesOff: [] });

test('sync: sign in, a change is sent after 1 s, sign out brings back the TV\'s own settings', async () => {
  const r = rig();
  r.gw.settings = { v: 1, channelPrefs: hidden(['ct:CH_2']), thumbnails: false, future: 1 };
  r.tv.settings = { channelPrefs: hidden(['ct:CH_4']), thumbnails: true };
  await r.sync.login(' Jana ', 'heslo');
  assert.equal(r.sync.name(), 'Jana');
  assert.deepEqual(r.tv.settings, { channelPrefs: hidden(['ct:CH_2']), thumbnails: false }, 'the profile is applied');
  assert.equal(JSON.stringify(r.stored.value).indexOf('heslo'), -1, 'the password is never stored');
  assert.equal(r.stored.value.token, 'tok-Jana');

  r.change({ channelPrefs: hidden(['ct:CH_2', 'ct:CH_5']), thumbnails: false });
  r.change({ channelPrefs: hidden(['ct:CH_5']), thumbnails: false });
  assert.equal(r.timers.filter(Boolean).length, 1, 'one debounce for both changes');
  assert.equal(r.timers.filter(Boolean)[0].ms, 1000);
  assert.ok(r.sync.state.pending);
  assert.equal(r.sync.stateText(), P.MESSAGES.SAVED, 'no "Čeká na uložení" during the 1 s wait');
  assert.equal(r.gw.calls.length, 1, 'nothing sent before the second without changes');
  await r.fire();
  const put = r.gw.calls[1];
  assert.equal(put.method, 'PUT');
  assert.deepEqual(JSON.parse(put.body).settings, { v: 1, channelPrefs: hidden(['ct:CH_5']), thumbnails: false, future: 1 },
    'the last state, unknown fields kept');
  assert.equal(r.sync.state.pending, false);
  assert.equal(r.sync.state.version, 2);

  r.sync.logout();
  await flush();
  assert.equal(r.gw.calls[2].url, 'http://pi.local:8098/dvb/profile/logout');
  assert.deepEqual(r.tv.settings, { channelPrefs: hidden(['ct:CH_4']), thumbnails: true });
  assert.equal(r.stored.value, null, 'token and name deleted');
  assert.equal(r.sync.signedIn(), false);
});

test('sync: create starts the profile with this TV\'s settings; a failed send stays pending and is retried', async () => {
  const r = rig();
  r.tv.settings = { channelPrefs: hidden(['ct:CH_6']), thumbnails: false };
  await assert.rejects(r.sync.create('Petr', '1234', '1235'), (e) => e.userMessage === P.MESSAGES.PASSWORDS_DIFFER);
  await assert.rejects(r.sync.create('Petr', '123', '123'), (e) => e.userMessage === P.MESSAGES.PASSWORD_INVALID);
  assert.equal(r.gw.calls.length, 0, 'checked on the TV first');
  await r.sync.create('Petr', '1234', '1234');
  assert.deepEqual(JSON.parse(r.gw.calls[0].body).settings, { v: 1, channelPrefs: hidden(['ct:CH_6']), thumbnails: false });
  assert.equal(r.events.applied, 0, 'nothing to apply: the TV keeps its settings');
  assert.equal(r.sync.stateText(), P.MESSAGES.SAVED);

  r.gw.down = true;
  r.change({ channelPrefs: hidden([]), thumbnails: false });
  await r.fire();
  assert.ok(r.sync.state.pending);
  assert.equal(r.sync.stateText(), P.MESSAGES.WAITING);
  assert.ok(r.stored.value.pending, 'pending survives an app restart');

  r.gw.down = false;
  r.gw.settings = { v: 1, channelPrefs: hidden(['ct:CH_1']), thumbnails: true };
  r.gw.version = 9;
  await r.sync.sync(); // app start / Nastavení open with pending: send, do not read
  assert.equal(r.gw.calls[r.gw.calls.length - 1].method, 'PUT');
  assert.deepEqual(r.gw.settings.channelPrefs, hidden([]));
  assert.equal(r.sync.state.pending, false);
  assert.equal(r.sync.stateText(), P.MESSAGES.SAVED);
});

test('sync: the 60 s retry sends only pending changes', async () => {
  const r = rig();
  await r.sync.create('Petr', '1234', '1234');
  await r.sync.retry();
  assert.equal(r.gw.calls.length, 1, 'nothing pending, nothing sent');
  r.gw.down = true;
  r.change({ channelPrefs: hidden(['ct:CH_1']), thumbnails: true });
  await r.fire();
  r.gw.down = false;
  await r.sync.retry();
  assert.equal(r.gw.calls[r.gw.calls.length - 1].method, 'PUT');
  assert.equal(r.sync.state.pending, false);
});

test('sync: another TV\'s change is applied on start / Nastavení open, the same version is not', async () => {
  const r = rig();
  r.gw.settings = { v: 1, channelPrefs: hidden(['ct:CH_2']), thumbnails: true };
  await r.sync.login('Jana', 'heslo');
  const applied = r.events.applied;
  await r.sync.sync();
  assert.equal(r.events.applied, applied, 'same version: nothing applied');
  r.gw.settings = { v: 1, channelPrefs: hidden(['dvb:x']), thumbnails: false };
  r.gw.version = 5;
  await r.sync.sync();
  assert.equal(r.events.applied, applied + 1);
  assert.deepEqual(r.tv.settings, { channelPrefs: hidden(['dvb:x']), thumbnails: false });
  assert.equal(r.stored.value.version, 5);
});

test('sync: a change made while a send runs is sent after it', async () => {
  const r = rig();
  await r.sync.create('Petr', '1234', '1234');
  r.gw.hold = true;
  r.change({ channelPrefs: hidden(['ct:CH_1']), thumbnails: true });
  await r.fire(); // PUT 1 in flight
  r.change({ channelPrefs: hidden(['ct:CH_2']), thumbnails: true });
  await r.fire(); // its debounce fires while PUT 1 runs: nothing sent yet
  assert.equal(r.gw.calls.length, 2);
  r.gw.hold();
  r.gw.hold = null;
  await flush();
  await flush();
  assert.equal(r.gw.calls.length, 3, 'the later change is sent too');
  assert.deepEqual(r.gw.settings.channelPrefs, hidden(['ct:CH_2']));
  assert.equal(r.sync.state.pending, false);
});

test('sync: 401 signs out once with the TV\'s own settings back; a wrong password at login does not', async () => {
  const r = rig();
  r.tv.settings = { channelPrefs: hidden(['ct:CH_4']), thumbnails: true };
  await assert.rejects(r.sync.login('Jana', 'spatne'), (e) => e.userMessage === 'Nesprávné jméno nebo heslo');
  assert.equal(r.events.signedOut, 0);
  r.gw.settings = { v: 1, channelPrefs: hidden([]), thumbnails: false };
  await r.sync.login('Jana', 'heslo');
  r.gw.unauthorized = true;
  await r.sync.sync();
  assert.equal(r.events.signedOut, 1);
  assert.equal(r.sync.signedIn(), false);
  assert.equal(r.stored.value, null);
  assert.deepEqual(r.tv.settings, { channelPrefs: hidden(['ct:CH_4']), thumbnails: true });
  await r.sync.sync();
  assert.equal(r.events.signedOut, 1, 'said once');
});

test('sync: a 401 without the session code (wrong app key) keeps the profile and shows the gateway text', async () => {
  const r = rig();
  r.gw.settings = { v: 1, channelPrefs: hidden(['ct:CH_2']), thumbnails: true };
  await r.sync.login('Jana', 'heslo');
  r.gw.badKey = true;
  await r.sync.sync();
  r.change({ channelPrefs: hidden([]), thumbnails: true });
  await r.fire();
  assert.equal(r.events.signedOut, 0);
  assert.equal(r.sync.name(), 'Jana');
  assert.equal(r.stored.value.token, 'tok-Jana');
  assert.ok(r.sync.state.pending, 'the change waits for a working key');
  assert.equal(r.sync.stateText(), P.MESSAGES.WAITING);
  const client = new P.ProfileClient(config, (req) => Promise.resolve({ status: 401, body: '{"error": "Chybí nebo nesedí klíč"}' }));
  await assert.rejects(client.get('tok'), (e) => e.userMessage === 'Chybí nebo nesedí klíč' && e.signedOut === false);
  r.gw.badKey = false;
  await r.sync.retry();
  assert.equal(r.sync.state.pending, false, 'sent once the key works again');
});

test('sync: both forms check name and password before sending', async () => {
  const r = rig();
  for (const call of [() => r.sync.login('', '1234'), () => r.sync.create('  ', '1234', '1234'), () => r.sync.login('a'.repeat(33), '1234')]) {
    await assert.rejects(call(), (e) => e.userMessage === 'Jméno musí mít 1 až 32 znaků.');
  }
  for (const call of [() => r.sync.login('Jana', ''), () => r.sync.login('Jana', '123'), () => r.sync.create('Jana', 'p'.repeat(65), 'p'.repeat(65))]) {
    await assert.rejects(call(), (e) => e.userMessage === 'Heslo musí mít 4 až 64 znaků.');
  }
  assert.equal(r.gw.calls.length, 0);
  await r.sync.login('Jana', ' heslo ');
  assert.equal(JSON.parse(r.gw.calls[0].body).password, ' heslo ', 'the password goes as typed');
});

test('sync: restored after an app restart; signed out, changes stay on the TV', async () => {
  const r = rig({ name: 'Jana', token: 'tok', version: 3, pending: true,
    active: { v: 1, channelPrefs: hidden(['ct:CH_2']), thumbnails: true }, local: { v: 1, channelPrefs: hidden([]), thumbnails: true } });
  assert.equal(r.sync.name(), 'Jana');
  assert.equal(r.sync.stateText(), P.MESSAGES.WAITING, 'still pending from before the restart');
  await r.sync.sync();
  assert.equal(r.gw.calls[0].method, 'PUT');
  assert.equal(r.gw.calls[0].headers.Authorization, 'Bearer ' + KEY, 'the app key as the header, never a cookie');
  assert.equal(r.gw.calls[0].headers.Cookie, undefined);
  assert.equal(r.sync.stateText(), P.MESSAGES.SAVED);
  assert.equal(r.gw.calls[0].headers['X-Cztvhub-Profile'], 'tok');

  const out = rig();
  out.change({ channelPrefs: hidden(['ct:CH_1']), thumbnails: true });
  assert.equal(out.timers.length, 0, 'signed out: no send scheduled');
  await out.sync.sync();
  assert.equal(out.gw.calls.length, 0);
  assert.equal(out.stored.value, null);
});

test('sync: without a gateway sign-in says where to set it up', async () => {
  const sync = new P.ProfileSync({ store: { load: () => null, save: () => {} }, client: () => null,
    current: () => ({ channelPrefs: CT.ChannelPrefs.empty(), thumbnails: true }), apply: () => {} });
  await assert.rejects(sync.login('Jana', 'heslo'), (e) => e.userMessage === P.MESSAGES.NEEDS_GATEWAY);
});
