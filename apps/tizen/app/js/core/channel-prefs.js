/*
 * "Kanály" (spec/apps.md): channels hidden and reordered on this TV only. Prefs = {order: [keys], hidden: [keys]};
 * keys `ct:<API id>` and `dvb:<gateway channel id>`. The order applies within a group (Internet ČT, Anténa);
 * keys of other groups and unknown keys are kept. Pure functions: every change returns new prefs.
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

  var ChannelPrefs = {
    MAX_KEYS: MAX_KEYS,

    empty: function () {
      return { order: [], hidden: [] };
    },

    /** Defensive: anything that is not {order: [...], hidden: [...]} becomes empty lists. */
    parse: function (data) {
      if (typeof data === 'string') {
        try {
          data = JSON.parse(data);
        } catch (e) {
          data = null;
        }
      }
      if (!data || typeof data !== 'object' || Array.isArray(data)) return ChannelPrefs.empty();
      return { order: keyList(data.order), hidden: keyList(data.hidden) };
    },

    serialise: function (prefs) {
      var p = ChannelPrefs.parse(prefs);
      return JSON.stringify({ order: p.order, hidden: p.hidden });
    },

    /** `channels` = the group's keys in default order → {all, visible}. */
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
      return { order: all.concat(rest).slice(0, MAX_KEYS), hidden: p.hidden };
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
      return { order: p.order, hidden: hidden };
    },

    /** "Obnovit výchozí pořadí": both lists cleared. */
    reset: function () {
      return ChannelPrefs.empty();
    }
  };

  CT.ChannelPrefs = ChannelPrefs;
})(typeof window !== 'undefined' ? window : global);
