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
  var THUMBS_KEY = 'cztvhub.thumbs'; // only this TV's switch; the images themselves are never stored
  var PROFILE_KEY = 'cztvhub.profile'; // "Profil": name, session token, settings documents; never the password
  var HOUSEHOLD_KEY = 'cztvhub.household'; // "Kanály" household default: own flag, own list, cached default

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

  /** "Náhledy anténních pořadů" on this TV (Nastavení → Server), default on. */
  CT.ThumbsSwitch = {
    on: function () {
      try {
        return root.localStorage.getItem(THUMBS_KEY) !== 'off';
      } catch (e) {
        return true;
      }
    },

    set: function (on) {
      try {
        root.localStorage.setItem(THUMBS_KEY, on ? 'on' : 'off');
      } catch (e) {
        // See SettingsStore.save().
      }
    }
  };

  /**
   * "Profil" next to the gateway key (spec/apps.md): {name, token, version, pending, active, local} while signed in
   * (ProfileSync), nothing when signed out. The channel prefs and the thumbnails switch above always hold the active
   * settings; `local` keeps this TV's own ones aside while signed in.
   */
  CT.ProfileStore = {
    load: function () {
      try {
        var raw = root.localStorage.getItem(PROFILE_KEY);
        return raw ? JSON.parse(raw) : null;
      } catch (e) {
        return null;
      }
    },

    save: function (saved) {
      try {
        if (saved) root.localStorage.setItem(PROFILE_KEY, JSON.stringify(saved));
        else root.localStorage.removeItem(PROFILE_KEY);
      } catch (e) {
        // See SettingsStore.save(): signed in until the app closes.
      }
    }
  };

  /**
   * "Kanály" household default (spec/apps.md, T11): {own, local, default, version} (CT.Household.HouseholdList).
   * The channel prefs below always hold what the TV shows; this keeps whether that is the TV's own list.
   */
  CT.HouseholdStore = {
    load: function () {
      try {
        var raw = root.localStorage.getItem(HOUSEHOLD_KEY);
        return raw ? JSON.parse(raw) : null;
      } catch (e) {
        return null;
      }
    },

    save: function (saved) {
      try {
        root.localStorage.setItem(HOUSEHOLD_KEY, JSON.stringify(saved));
      } catch (e) {
        // See SettingsStore.save(): the default is fetched again on the next start.
      }
    }
  };

  /** "Kanály": hidden channels and their order on this TV (the profile's while signed in; spec/apps.md). */
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
