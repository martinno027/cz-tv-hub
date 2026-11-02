/*
 * `X-Cztvhub-Client` for the gateway's sign-in log (spec/dvb-gateway.md "Sign-in log (T37)"): which app and version
 * connected or signed in, "tizen/1.0.0 (412)" on Samsung, "web/1.0.0 (412)" on the web. Sent only on the profile
 * requests (`/dvb/profiles*`, `/dvb/profile*`), `GET /dvb/status` and the watch reports (`POST /dvb/watch`, T53,
 * core/watch.js); never on other gateway requests (never on playback), never to ČT or the Chamber's CDN (on the web
 * a custom header to a CDN would also need a CORS preflight).
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var MAX_LENGTH = 64;
  // What the gateway accepts (anything else it ignores); parentheses only around the build.
  var NOT_ALLOWED = /[^A-Za-z0-9 ._+\/-]/g;

  function clean(text) {
    return String(text === undefined || text === null ? '' : text).replace(NOT_ALLOWED, '').trim();
  }

  var ClientHeader = {
    NAME: 'X-Cztvhub-Client',

    /** The note under „Přihlásit se"/„Vytvořit profil" and in Nastavení → Server (spec/apps.md "Sign-in note (T37)"). */
    NOTE: 'Připojení a přihlášení se zaznamenává (kdy, odkud a z jakého zařízení). ' +
      'Záznam vidí jen správce a po 90 dnech se smaže.', // T53 dropped „Co sledujete, se nezaznamenává."

    /**
     * "<platform>/<version> (<build>)" from the build info (js/build-info.js: {version, build, …}); characters the
     * gateway would refuse are dropped, so a missing version or a build "?" leaves just "tizen" / "tizen/1.0.0" (the
     * platform is still known); cut to 64 characters. No platform → null (nothing is sent).
     */
    value: function (platform, info) {
      var p = clean(platform).replace(/\//g, '');
      if (!p) return null;
      var version = clean(info && info.version);
      var build = clean(info && info.build).replace(/\//g, '');
      var text = version ? p + '/' + version : p;
      if (build) text += ' (' + build + ')';
      return text.slice(0, MAX_LENGTH);
    },

    /** The header goes on this gateway request: `GET /dvb/status`, `POST /dvb/watch`, any method on /dvb/profile(s)[/…]. */
    applies: function (method, path) {
      var p = String(path || '').split('?')[0];
      if (p === '/dvb/status') return String(method).toUpperCase() === 'GET';
      if (p === '/dvb/watch') return String(method).toUpperCase() === 'POST';
      return /^\/dvb\/profiles?(\/|$)/.test(p);
    },

    /** This page's value: the platform (CT.Platform.name) and the build info written by the build / gateway install. */
    current: function () {
      return ClientHeader.value(CT.Platform ? CT.Platform.name : null, root.CT_BUILD_INFO);
    },

    /** Adds the header to `headers` when it belongs on `method` `path` (else leaves them as they are); → headers. */
    add: function (headers, method, path, value) {
      var v = value === undefined ? ClientHeader.current() : value;
      if (v && ClientHeader.applies(method, path)) headers[ClientHeader.NAME] = v;
      return headers;
    }
  };

  CT.ClientHeader = ClientHeader;
})(typeof window !== 'undefined' ? window : global);
