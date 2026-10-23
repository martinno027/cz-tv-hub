/*
 * "Anténa" settings in the app's private localStorage (Tizen keeps it inside the app's data directory).
 * A build may preset the gateway via js/local-config.js (window.CT_LOCAL_CONFIG), so the long key does
 * not have to be typed on the remote; settings saved in the app win over the preset.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var KEY = 'cztvhub.dvb';
  var PREFS_KEY = 'cztvhub.channelPrefs';

  function read() {
    try {
      var raw = root.localStorage.getItem(KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  CT.SettingsStore = {
    /** {address, key} as typed, or null. A cleared setting stays cleared even with a preset. */
    load: function () {
      var saved = read();
      if (saved) return saved.cleared ? null : saved;
      var preset = root.CT_LOCAL_CONFIG;
      if (preset && preset.gatewayAddress && preset.gatewayKey) {
        return { address: preset.gatewayAddress, key: preset.gatewayKey };
      }
      return null;
    },

    /** The DvbConfig to use, or null when not set up (or the saved values are no longer valid). */
    config: function () {
      var s = CT.SettingsStore.load();
      if (!s) return null;
      try {
        return CT.DvbConfig.fromSettings(s.address, s.key);
      } catch (e) {
        return null;
      }
    },

    save: function (address, key) {
      try {
        root.localStorage.setItem(KEY, JSON.stringify({ address: address, key: key }));
      } catch (e) {
        // Storage full or unavailable: the setting just lasts until the app closes.
      }
    },

    clear: function () {
      try {
        root.localStorage.setItem(KEY, JSON.stringify({ cleared: true }));
      } catch (e) {
        // See save().
      }
    }
  };

  /** "Kanály": hidden channels and their order, on this TV only (spec/apps.md). */
  CT.ChannelPrefsStore = {
    load: function () {
      try {
        return CT.ChannelPrefs.parse(root.localStorage.getItem(PREFS_KEY));
      } catch (e) {
        return CT.ChannelPrefs.empty();
      }
    },

    save: function (prefs) {
      try {
        root.localStorage.setItem(PREFS_KEY, CT.ChannelPrefs.serialise(prefs));
      } catch (e) {
        // See save() above: the choice lasts until the app closes.
      }
    }
  };
})(window);
