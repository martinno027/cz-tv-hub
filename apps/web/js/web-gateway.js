/*
 * The gateway on the web (spec/web.md "Nastavení → Server"): the address is the page's own origin (no field, no
 * discovery); the key is stored like on the TV (localStorage, settings-store.js) and also as the cookie
 * `cztvhub_key` (Path=/dvb; SameSite=Strict) for the media requests a browser makes itself (Safari's native HLS
 * cannot add a header). "Odebrat bránu" deletes both. The cookie never goes anywhere but the gateway's /dvb/ paths.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var COOKIE = 'cztvhub_key';
  var MAX_AGE_S = 400 * 24 * 3600; // browsers cap a cookie's life at 400 days; it is set again at every start

  /** The page's gateway as the address setting: scheme://host:port with the port always written out. */
  function address(location) {
    var https = location.protocol === 'https:';
    var port = location.port || (https ? '443' : '80');
    return location.protocol + '//' + location.hostname + ':' + port;
  }

  /** Only characters a cookie value may hold (the gateway's keys are URL-safe base64). */
  function cookieSafe(key) {
    return /^[A-Za-z0-9._~+\/=-]+$/.test(String(key || ''));
  }

  /** The `document.cookie` assignment that sets the key (null when it cannot be a cookie value). */
  function setCookie(key, secure) {
    if (!cookieSafe(key)) return null;
    return COOKIE + '=' + key + '; Path=/dvb; SameSite=Strict; Max-Age=' + MAX_AGE_S + (secure ? '; Secure' : '');
  }

  /** The assignment that deletes it. */
  function clearCookie(secure) {
    return COOKIE + '=; Path=/dvb; SameSite=Strict; Max-Age=0' + (secure ? '; Secure' : '');
  }

  /** After the gateway setting changed (also at start): the cookie follows the key; `doc` = document. */
  function syncCookie(doc, config, secure) {
    var line = config ? setCookie(config.key, secure) : null;
    doc.cookie = line || clearCookie(secure);
    return line;
  }

  CT.WebGateway = {
    COOKIE: COOKIE,
    address: address,
    cookieSafe: cookieSafe,
    setCookie: setCookie,
    clearCookie: clearCookie,
    syncCookie: syncCookie
  };
})(typeof window !== 'undefined' ? window : global);
