/*
 * "Kanály" (spec/apps.md): channels hidden and reordered on this TV only.
 * Prefs = {order: [keys], hidden: [keys], sourcesOff: ['ct' | 'dvb']}; keys `ct:<API id>` and
 * `dvb:<gateway channel id>`. One mixed list (CT and Anténa channels in any order); the list passed in holds only
 * the channels of the sources that are on, and stored keys not in it (unknown, or of a source that is off) are
 * kept. Pure functions: every change returns new prefs.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var MAX_KEYS = 500;

  function keyList(value) {
    if (!Array.isArray(value)) return [];
    var seen = {};
    var out = [];
    value.forEach(function (k) {
      if (typeof k === 'string' && k && !seen[k] && out.length < MAX_KEYS) {
        seen[k] = true;
        out.push(k);
      }
    });
    return out;
  }

  function contains(list, key) {
    return list.indexOf(key) >= 0;
  }

  var SOURCES = ['ct', 'dvb'];

  function sourceList(value) {
    return keyList(value).filter(function (s) { return contains(SOURCES, s); });
  }

  function make(order, hidden, sourcesOff) {
    return { order: order, hidden: hidden, sourcesOff: sourcesOff };
  }

  var ChannelPrefs = {
    MAX_KEYS: MAX_KEYS,

    empty: function () {
      return make([], [], []);
    },

    /** Defensive: anything that is not {order: [...], hidden: [...], sourcesOff: [...]} becomes empty lists
     *  (a missing `sourcesOff` = both sources on). */
    parse: function (data) {
      if (typeof data === 'string') {
        try {
          data = JSON.parse(data);
        } catch (e) {
          data = null;
        }
      }
      if (!data || typeof data !== 'object' || Array.isArray(data)) return ChannelPrefs.empty();
      return make(keyList(data.order), keyList(data.hidden), sourceList(data.sourcesOff));
    },

    serialise: function (prefs) {
      var p = ChannelPrefs.parse(prefs);
      return JSON.stringify({ order: p.order, hidden: p.hidden, sourcesOff: p.sourcesOff });
    },

    /** `channels` = the keys of the sources that are on, in default order (CT, then Anténa) → {all, visible}. */
    arrange: function (channels, prefs) {
      var p = ChannelPrefs.parse(prefs);
      var all = [];
      p.order.forEach(function (k) {
        if (contains(channels, k) && !contains(all, k)) all.push(k);
      });
      channels.forEach(function (k) {
        if (!contains(all, k)) all.push(k);
      });
      var visible = all.filter(function (k) { return !contains(p.hidden, k); });
      return { all: all, visible: visible };
    },

    /** Swaps `key` with its neighbour (`by` = -1 up, +1 down); edges and unknown keys change nothing. */
    move: function (channels, prefs, key, by) {
      var p = ChannelPrefs.parse(prefs);
      var all = ChannelPrefs.arrange(channels, p).all;
      var i = all.indexOf(key);
      var j = i + by;
      if (i < 0 || j < 0 || j >= all.length) return p;
      all[i] = all[j];
      all[j] = key;
      var rest = p.order.filter(function (k) { return !contains(all, k); });
      return make(all.concat(rest).slice(0, MAX_KEYS), p.hidden, p.sourcesOff);
    },

    isHidden: function (prefs, key) {
      return contains(ChannelPrefs.parse(prefs).hidden, key);
    },

    /** Shows a hidden channel or hides a shown one; the order stays. */
    toggle: function (prefs, key) {
      var p = ChannelPrefs.parse(prefs);
      var hidden = contains(p.hidden, key)
        ? p.hidden.filter(function (k) { return k !== key; })
        : p.hidden.concat([key]).slice(0, MAX_KEYS);
      return make(p.order, hidden, p.sourcesOff);
    },

    /** Whole source switched off ('ct' = Internetová TV, 'dvb' = Anténa). */
    isSourceOff: function (prefs, source) {
      return contains(ChannelPrefs.parse(prefs).sourcesOff, source);
    },

    /** Switches a whole source on or off; the stored order and hidden channels stay. */
    setSource: function (prefs, source, on) {
      var p = ChannelPrefs.parse(prefs);
      var off = p.sourcesOff.filter(function (s) { return s !== source; });
      if (!on && contains(SOURCES, source)) off.push(source);
      return make(p.order, p.hidden, off);
    },

    /** "Obnovit výchozí pořadí": order and hidden cleared, both sources on. */
    reset: function () {
      return ChannelPrefs.empty();
    }
  };

  CT.ChannelPrefs = ChannelPrefs;
})(typeof window !== 'undefined' ? window : global);
