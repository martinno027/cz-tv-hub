/*
 * "Kanály" household default (spec/apps.md "Household default (T11)", spec/dvb-gateway.md "Household default channel
 * list"): a TV that is not signed in and whose list the viewer never changed shows the gateway's default list.
 * State {own, local, default, version}: own = the viewer changed the list on this TV (local = that list), default =
 * the cached household default (prefs or null), version = its gateway version. Golden cases in
 * spec/fixtures/household_default_cases.json. Signed in, the profile applies instead (ProfileSync).
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  function isObject(v) {
    return v !== null && typeof v === 'object' && !Array.isArray(v);
  }

  /** Prefs stored before T11: any order, hidden channel or source switched off made the list the TV's own. */
  function migrate(stored) {
    var p = CT.ChannelPrefs.parse(stored);
    return p.order.length > 0 || p.hidden.length > 0 || p.sourcesOff.length > 0;
  }

  /** `values` = {empty, builtin}: the prefs a reset leaves and the list without a default (labels in the golden cases). */
  function effective(state, values) {
    if (state.own) return state.local;
    return state.default !== null ? state.default : values.builtin;
  }

  /** One event: 'defaults' (GET /dvb/defaults, only a newer version), 'change' (settings = the new list), 'reset'. */
  function reduce(state, event, values) {
    var s = { own: state.own, local: state.local, default: state.default, version: state.version };
    switch (event.type) {
      case 'defaults':
        if (event.version > state.version) {
          s.default = event.settings;
          s.version = event.version;
        }
        break;
      case 'change':
        s.own = true;
        s.local = event.settings;
        break;
      case 'reset':
        s.own = false;
        s.local = values.empty;
        break;
      default:
        break;
    }
    return s;
  }

  /**
   * GET /dvb/defaults → {channelPrefs: prefs | null, version}; only an explicit null is "no default". Anything else
   * malformed (channelPrefs neither an object nor null, a missing or bad version) is an error: the cache stays.
   */
  function parse(status, body) {
    var o = CT.DvbParser.ensureOk(status, body);
    var version = o.version;
    if (typeof version !== 'number' || !isFinite(version) || version < 0 || Math.floor(version) !== version) {
      throw new CT.CtError('defaults: no version', CT.DvbMessages.GENERIC);
    }
    if (o.channelPrefs !== null && !isObject(o.channelPrefs)) {
      throw new CT.CtError('defaults: channelPrefs not an object', CT.DvbMessages.GENERIC);
    }
    return { channelPrefs: o.channelPrefs === null ? null : CT.ChannelPrefs.parse(o.channelPrefs), version: version };
  }

  /** The household default from the gateway (`client` = a DvbClient; the app key goes as the Authorization header). */
  function fetchDefaults(client) {
    return client.get('/dvb/defaults').then(function (r) { return parse(r.status, r.body); });
  }

  function values() {
    return { empty: CT.ChannelPrefs.empty(), builtin: CT.ChannelPrefs.empty() };
  }

  function fresh(legacy) {
    var local = CT.ChannelPrefs.parse(legacy);
    return { own: migrate(local), local: local, default: null, version: 0 };
  }

  /**
   * The household default on the TV. `opts`:
   * - store: {load() → saved | null, save(saved)}; saved = {own, local, default, version} (prefs as stored)
   * - legacy(): this TV's own prefs from before T11 (used once, when nothing is saved yet)
   */
  function HouseholdList(opts) {
    this.store = opts.store;
    var saved = opts.store.load();
    if (isObject(saved) && typeof saved.own === 'boolean' && typeof saved.version === 'number') {
      this.state = { own: saved.own, local: CT.ChannelPrefs.parse(saved.local),
        default: isObject(saved.default) ? CT.ChannelPrefs.parse(saved.default) : null, version: saved.version };
    } else {
      this.state = fresh(opts.legacy());
      this.persist();
    }
  }

  HouseholdList.prototype.persist = function () {
    this.store.save(this.state);
  };

  HouseholdList.prototype.dispatch = function (event) {
    this.state = reduce(this.state, event, values());
    this.persist();
  };

  /** The list a signed-out TV shows. */
  HouseholdList.prototype.effective = function () {
    return effective(this.state, values());
  };

  /** What the TV shows after other settings were applied (sign in/out, another TV's change of the profile). */
  HouseholdList.prototype.shown = function (settings, signedIn) {
    return signedIn ? settings.channelPrefs : this.effective();
  };

  /** The viewer changed the list (signed out): from now on it is this TV's own. */
  HouseholdList.prototype.change = function (prefs) {
    this.dispatch({ type: 'change', settings: CT.ChannelPrefs.parse(prefs) });
  };

  /** "Obnovit výchozí" (signed out): follow the household default again. */
  HouseholdList.prototype.reset = function () {
    this.dispatch({ type: 'reset' });
  };

  /** "Obnovit výchozí" signed in: a copy of the household default (the built-in order without one) for the profile. */
  HouseholdList.prototype.defaultCopy = function () {
    return CT.ChannelPrefs.parse(this.state.default !== null ? this.state.default : values().builtin);
  };

  /** An answer of GET /dvb/defaults; true when the cached default changed. */
  HouseholdList.prototype.defaults = function (answer) {
    var before = this.state.version;
    this.dispatch({ type: 'defaults', settings: answer.channelPrefs, version: answer.version });
    return this.state.version !== before;
  };

  /** Another gateway (or none): its versions start anew, so the cached default is dropped. */
  HouseholdList.prototype.forget = function () {
    if (this.state.default === null && this.state.version === 0) return;
    this.state = { own: this.state.own, local: this.state.local, default: null, version: 0 };
    this.persist();
  };

  CT.Household = {
    migrate: migrate,
    effective: effective,
    reduce: reduce,
    parse: parse,
    fetchDefaults: fetchDefaults,
    HouseholdList: HouseholdList
  };
})(typeof window !== 'undefined' ? window : global);
