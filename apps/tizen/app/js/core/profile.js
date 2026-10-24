/*
 * "Profil" (spec/apps.md, spec/dvb-gateway.md "Profiles"): this TV's settings kept in a profile on the gateway.
 * Name/password rules, the settings document (unknown fields kept), the sync reducer (golden cases in
 * spec/fixtures/profile_cases.json), the gateway client and ProfileSync, which ties them to the TV's storage.
 * The password only passes through login()/create() into the request body: it is never stored or logged.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var J = CT.Json;

  var NAME_MAX = 32;
  var PASSWORD_MIN = 4;
  var PASSWORD_MAX = 64;
  var DEBOUNCE_MS = 1000;
  var TOKEN_HEADER = 'X-Cztvhub-Profile';
  var SIGNED_OUT_CODE = 'profileSignedOut'; // only this 401 ends the session; the app key's 401 has no code

  var MESSAGES = {
    NEEDS_GATEWAY: 'Profily se ukládají na bránu Raspberry. Nejdřív ji nastavte v Nastavení → Server.',
    SAVED: 'Nastavení je uložené v profilu',
    WAITING: 'Čeká na uložení — brána není dostupná',
    SIGNED_OUT: 'Profil byl odhlášen (smazaný nebo nové heslo). Platí zase nastavení této televize.',
    PASSWORDS_DIFFER: 'Hesla se neshodují',
    NAME_INVALID: 'Jméno musí mít 1 až 32 znaků.',
    PASSWORD_INVALID: 'Heslo musí mít 4 až 64 znaků.',
    GENERIC: 'Profil se nepodařilo načíst'
  };

  function codePoints(s) {
    return Array.from(s).length;
  }

  function isObject(v) {
    return v !== null && typeof v === 'object' && !Array.isArray(v);
  }

  function clone(v) {
    return JSON.parse(JSON.stringify(v));
  }

  /** Trimmed and NFC-normalised name, or null when not 1–32 characters or with a control character. */
  function normalizeName(name) {
    if (typeof name !== 'string') return null;
    var n = name.trim().normalize('NFC');
    var length = codePoints(n);
    if (length < 1 || length > NAME_MAX) return null;
    if (/[\u0000-\u001f\u007f-\u009f]/.test(n)) return null;
    return n;
  }

  /** What makes two names the same profile: lower case without diacritics ("Jana" = "jána"). */
  function nameKey(name) {
    var n = normalizeName(name);
    if (n === null) return null;
    return n.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().normalize('NFC');
  }

  function validPassword(password) {
    if (typeof password !== 'string') return false;
    var length = codePoints(password);
    return length >= PASSWORD_MIN && length <= PASSWORD_MAX;
  }

  /**
   * The settings document {"v", "channelPrefs", "thumbnails", …}. apply() gives the TV's settings
   * {channelPrefs, thumbnails} (missing fields = defaults); exportDoc() writes them back into the document,
   * keeping every field the app does not know (also inside channelPrefs).
   */
  var Document = {
    apply: function (doc) {
      var d = isObject(doc) ? doc : {};
      return {
        channelPrefs: CT.ChannelPrefs.parse(isObject(d.channelPrefs) ? d.channelPrefs : null),
        thumbnails: typeof d.thumbnails === 'boolean' ? d.thumbnails : true
      };
    },

    exportDoc: function (doc, settings) {
      var out = isObject(doc) ? clone(doc) : {};
      if (typeof out.v !== 'number') out.v = 1;
      var prefs = isObject(out.channelPrefs) ? out.channelPrefs : {};
      var p = CT.ChannelPrefs.parse(settings.channelPrefs);
      prefs.order = p.order;
      prefs.hidden = p.hidden;
      prefs.sourcesOff = p.sourcesOff;
      out.channelPrefs = prefs;
      out.thumbnails = settings.thumbnails !== false;
      return out;
    }
  };

  function signedOut(state) {
    return { profile: null, active: state.local !== null ? state.local : state.active, local: null, pending: false, version: null };
  }

  /**
   * One step of the sync (spec/apps.md "Sync"): state {profile, active, local, pending, version}, settings opaque.
   * Returns {state, message}; message 'signedOut' = say once that the gateway ended the session.
   */
  function reduce(state, event) {
    var s = { profile: state.profile, active: state.active, local: state.local, pending: state.pending, version: state.version };
    var message = null;
    switch (event.type) {
      case 'signIn':
        s = { profile: event.name, active: event.settings, local: state.profile !== null ? state.local : state.active,
          pending: false, version: event.version };
        break;
      case 'create':
        s = { profile: event.name, active: state.active, local: state.profile !== null ? state.local : state.active,
          pending: false, version: event.version };
        break;
      case 'change':
        s.active = event.settings;
        if (state.profile !== null) s.pending = true;
        break;
      case 'pushed':
        if (state.profile === null) break;
        s.pending = false;
        s.version = event.version;
        break;
      case 'pushFailed':
        break;
      case 'remote':
        if (state.profile === null || state.pending || event.version === state.version) break;
        s.active = event.settings;
        s.version = event.version;
        break;
      case 'signOut':
        if (state.profile !== null) s = signedOut(state);
        break;
      case 'unauthorized':
        if (state.profile !== null) {
          s = signedOut(state);
          message = 'signedOut';
        }
        break;
      default:
        break;
    }
    return { state: s, message: message };
  }

  /** {token, name, settings, version}: a login/create answer or GET /dvb/profile (no token there). */
  function parseProfile(status, body, needToken) {
    var o = CT.DvbParser.ensureOk(status, body);
    var token = J.str(o, 'token');
    if (needToken && !token) throw new CT.CtError('profile: no token', MESSAGES.GENERIC);
    var version = typeof o.version === 'number' && isFinite(o.version) ? o.version : null;
    return { token: token, name: J.str(o, 'name'), settings: isObject(o.settings) ? o.settings : { v: 1 }, version: version };
  }

  /**
   * Profile API of the gateway (`transport` as for DvbClient); every call resolves parsed data or rejects CtError.
   * The app key goes as the Authorization header (the gateway refuses the cookie on profile calls).
   */
  function ProfileClient(config, transport) {
    this.config = config;
    this.transport = transport;
  }

  ProfileClient.prototype.call = function (method, path, token, payload) {
    var headers = this.config.authHeaders();
    headers.Accept = 'application/json';
    if (token) headers[TOKEN_HEADER] = token;
    if (payload !== undefined) headers['Content-Type'] = 'application/json';
    var request = { method: method, url: this.config.url(path), headers: headers,
      body: payload !== undefined ? JSON.stringify(payload) : null };
    return this.transport(request).then(function (r) {
      // The gateway's own Czech text for 400/401/409/429/507; `signedOut` only for a 401 with the session code.
      if (r.status < 200 || r.status > 299) {
        var error = new CT.CtError('profile ' + method + ' ' + path + ': HTTP ' + r.status,
          CT.DvbParser.errorText(r.body) || MESSAGES.GENERIC, String(r.status));
        error.signedOut = r.status === 401 && J.str(J.parse(r.body), 'code') === SIGNED_OUT_CODE;
        throw error;
      }
      return r;
    }, function (e) {
      throw new CT.CtError('profile ' + method + ' ' + path + ' failed: ' + e, CT.DvbMessages.UNREACHABLE, 'network');
    });
  };

  ProfileClient.prototype.create = function (name, password, settings) {
    return this.call('POST', '/dvb/profiles', null, { name: name, password: password, settings: settings })
      .then(function (r) { return parseProfile(r.status, r.body, true); });
  };

  ProfileClient.prototype.login = function (name, password) {
    return this.call('POST', '/dvb/profiles/login', null, { name: name, password: password })
      .then(function (r) { return parseProfile(r.status, r.body, true); });
  };

  ProfileClient.prototype.get = function (token) {
    return this.call('GET', '/dvb/profile', token).then(function (r) { return parseProfile(r.status, r.body, false); });
  };

  /** → the new version (null when the answer has none). */
  ProfileClient.prototype.put = function (token, settings) {
    return this.call('PUT', '/dvb/profile', token, { settings: settings }).then(function (r) {
      var o = CT.DvbParser.ensureOk(r.status, r.body);
      return typeof o.version === 'number' && isFinite(o.version) ? o.version : null;
    });
  };

  ProfileClient.prototype.logout = function (token) {
    return this.call('POST', '/dvb/profile/logout', token, {});
  };

  function emptyState(active) {
    return { profile: null, active: active, local: null, pending: false, version: null };
  }

  /**
   * The sync on the TV. `opts`:
   * - store: {load() → saved | null, save(saved | null)}; saved = {name, token, version, pending, active, local}
   *   (documents; never a password)
   * - client(): a ProfileClient for the configured gateway, or null without one
   * - current(): the TV's settings now {channelPrefs, thumbnails}
   * - apply(settings): makes other settings the TV's current ones (sign in, sign out, another TV's change)
   * - onChange(): the state or the state line changed (redraw)
   * - onSignedOut(): the gateway ended the session (the app says MESSAGES.SIGNED_OUT once)
   * - timers: {set(fn, ms) → id, clear(id)} (setTimeout by default)
   */
  function ProfileSync(opts) {
    this.opts = opts;
    this.timers = opts.timers || { set: function (fn, ms) { return setTimeout(fn, ms); }, clear: function (id) { clearTimeout(id); } };
    this.busy = false;
    this.failed = false; // the last send did not reach the gateway (or was still pending at the last app run)
    this.debounce = null;
    this.seq = 0; // +1 per change, so a change made while a send runs is sent again
    var saved = opts.store.load();
    if (isObject(saved) && typeof saved.token === 'string' && saved.token && typeof saved.name === 'string') {
      this.token = saved.token;
      this.state = { profile: saved.name, active: isObject(saved.active) ? saved.active : this.currentDoc({ v: 1 }),
        local: isObject(saved.local) ? saved.local : null, pending: saved.pending === true,
        version: typeof saved.version === 'number' ? saved.version : null };
      this.failed = this.state.pending;
    } else {
      this.token = null;
      this.state = emptyState(this.currentDoc({ v: 1 }));
    }
  }

  ProfileSync.prototype.currentDoc = function (base) {
    return Document.exportDoc(base, this.opts.current());
  };

  ProfileSync.prototype.signedIn = function () {
    return this.state.profile !== null;
  };

  ProfileSync.prototype.name = function () {
    return this.state.profile;
  };

  /** The state line while signed in. */
  ProfileSync.prototype.stateText = function () {
    return this.state.pending && this.failed ? MESSAGES.WAITING : MESSAGES.SAVED;
  };

  ProfileSync.prototype.persist = function () {
    var s = this.state;
    this.opts.store.save(s.profile === null ? null : { name: s.profile, token: this.token, version: s.version,
      pending: s.pending, active: s.active, local: s.local });
  };

  ProfileSync.prototype.dispatch = function (event) {
    var before = this.state.active;
    var r = reduce(this.state, event);
    this.state = r.state;
    if (this.state.profile === null) {
      this.token = null;
      this.failed = false;
      this.timers.clear(this.debounce);
      this.debounce = null;
    }
    this.persist();
    if (this.state.active !== before && event.type !== 'change') this.opts.apply(Document.apply(this.state.active));
    if (r.message === 'signedOut' && this.opts.onSignedOut) this.opts.onSignedOut();
    if (this.opts.onChange) this.opts.onChange();
  };

  /** A profile call failed: only the gateway's "session gone" 401 ends the session (not a wrong app key). */
  ProfileSync.prototype.failure = function (e) {
    if (e && e.signedOut) this.dispatch({ type: 'unauthorized' });
    return e;
  };

  ProfileSync.prototype.check = function () {
    var client = this.opts.client();
    if (!client) throw new CT.CtError('no gateway', MESSAGES.NEEDS_GATEWAY);
    return client;
  };

  /** Přihlásit: resolves when signed in; rejects CtError with the Czech reason (the password is not kept). */
  ProfileSync.prototype.login = function (name, password) {
    var self = this;
    var n = normalizeName(name);
    if (n === null) return Promise.reject(new CT.CtError('bad name', MESSAGES.NAME_INVALID));
    if (!validPassword(password)) return Promise.reject(new CT.CtError('bad password', MESSAGES.PASSWORD_INVALID));
    var client;
    try {
      client = this.check();
    } catch (e) {
      return Promise.reject(e);
    }
    return client.login(n, password).then(function (p) {
      if (self.state.profile === null) self.state.active = self.currentDoc(self.state.active);
      self.token = p.token;
      self.failed = false;
      self.dispatch({ type: 'signIn', name: p.name || n, settings: p.settings, version: p.version });
    });
  };

  /** Vytvořit: the new profile starts with this TV's current settings. */
  ProfileSync.prototype.create = function (name, password, again) {
    var self = this;
    var n = normalizeName(name);
    if (n === null) return Promise.reject(new CT.CtError('bad name', MESSAGES.NAME_INVALID));
    if (!validPassword(password)) return Promise.reject(new CT.CtError('bad password', MESSAGES.PASSWORD_INVALID));
    if (password !== again) return Promise.reject(new CT.CtError('passwords differ', MESSAGES.PASSWORDS_DIFFER));
    var client;
    try {
      client = this.check();
    } catch (e) {
      return Promise.reject(e);
    }
    var doc = this.currentDoc(this.state.profile === null ? { v: 1 } : this.state.active);
    return client.create(n, password, doc).then(function (p) {
      self.state.active = doc;
      self.token = p.token;
      self.failed = false;
      self.dispatch({ type: 'create', name: p.name || n, version: p.version });
    });
  };

  /** Odhlásit (also before "Odebrat bránu"): the gateway is told (failure ignored), the TV's own settings come back. */
  ProfileSync.prototype.logout = function () {
    if (!this.signedIn()) return;
    var client = this.opts.client();
    if (client && this.token) client.logout(this.token).catch(function () {});
    this.dispatch({ type: 'signOut' });
  };

  /** The TV's settings changed (Kanály, the thumbnails switch): signed in, sent after 1 s without another change. */
  ProfileSync.prototype.changed = function () {
    var signed = this.signedIn();
    this.seq += 1;
    this.dispatch({ type: 'change', settings: this.currentDoc(this.state.active) });
    if (!signed) return;
    var self = this;
    this.timers.clear(this.debounce);
    this.debounce = this.timers.set(function () {
      self.debounce = null;
      self.push();
    }, DEBOUNCE_MS);
  };

  /** Sends the active settings (PUT /dvb/profile); a change made meanwhile is sent again after it. */
  ProfileSync.prototype.push = function () {
    var self = this;
    var client = this.opts.client();
    if (!this.signedIn() || !this.state.pending || this.busy) return Promise.resolve();
    if (!client) {
      this.failed = true;
      this.dispatch({ type: 'pushFailed' });
      return Promise.resolve();
    }
    var token = this.token;
    var seq = this.seq;
    this.busy = true;
    return client.put(token, this.state.active).then(function (version) {
      self.busy = false;
      if (self.token !== token) return;
      self.failed = false;
      self.dispatch({ type: 'pushed', version: version });
      if (self.seq !== seq) {
        // changed while this send ran: pending again; the debounce sends it, or now when it already fired
        self.dispatch({ type: 'change', settings: self.state.active });
        if (self.debounce === null) self.push();
      }
    }, function (e) {
      self.busy = false;
      if (self.token !== token) return;
      if (e && e.signedOut) return self.failure(e);
      self.failed = true;
      self.dispatch({ type: 'pushFailed' });
    });
  };

  /** App start and Nastavení open: with pending changes send them, otherwise read the profile. */
  ProfileSync.prototype.sync = function () {
    var self = this;
    if (!this.signedIn() || this.busy || this.debounce !== null) return Promise.resolve();
    if (this.state.pending) return this.push();
    var client = this.opts.client();
    if (!client) return Promise.resolve();
    var token = this.token;
    this.busy = true;
    return client.get(token).then(function (p) {
      self.busy = false;
      if (self.token !== token) return;
      self.dispatch({ type: 'remote', settings: p.settings, version: p.version });
    }, function (e) {
      self.busy = false;
      if (self.token === token) self.failure(e);
    });
  };

  /** Every 60 s: only a failed send is retried. */
  ProfileSync.prototype.retry = function () {
    if (this.signedIn() && this.state.pending && this.debounce === null) return this.push();
    return Promise.resolve();
  };

  CT.Profile = {
    MESSAGES: MESSAGES,
    DEBOUNCE_MS: DEBOUNCE_MS,
    TOKEN_HEADER: TOKEN_HEADER,
    normalizeName: normalizeName,
    nameKey: nameKey,
    validPassword: validPassword,
    Document: Document,
    reduce: reduce,
    ProfileClient: ProfileClient,
    ProfileSync: ProfileSync
  };
})(typeof window !== 'undefined' ? window : global);
