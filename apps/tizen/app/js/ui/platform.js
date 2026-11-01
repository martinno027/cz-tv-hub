/*
 * Platform parts (spec/web.md "Where it lives"): what differs between the Samsung TV and the household web app
 * (apps/web, the same files in a browser). Samsung fills nothing in: every `null` keeps the TV's own code path
 * (AVPlay, the remote's key codes, the typed gateway address, …). The web layer replaces these after this script
 * and before the screens are created; a feature touching one of these parts changes the web adapter too.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  CT.Platform = {
    name: 'tizen',
    /** App start on window `load` (the web starts the app itself once its loader has run every script). */
    startOnLoad: true,
    /** Synthetic fixtures of `?demo`, relative to the page (Samsung: the repository's spec/fixtures). */
    fixturesBase: '../../../spec/fixtures/',
    /** `transport` → the transport for CT's API (Samsung: the same XHR transport, Tizen allows CT's origin). */
    ctTransport: function (transport) {
      return transport;
    },
    /** function (cb) → a player backend; null = AVPlay on the TV, <video> in a desktop browser. */
    playerBackend: null,
    /** function (target) → a Czech text instead of playing (e.g. a codec the device lacks), or null. */
    refusePlay: null,
    /** function (request, error) → {label, run} offered under a player error, or null. */
    playErrorAction: null,
    /** function (event, codes) → key name or null; null = the remote's codes (keys.js). */
    keyName: null,
    /** function () that opens the official app ("Otevřít v iVysílání"); null = not offered (Samsung). */
    officialApp: null,
    /** function () → the fixed gateway address; null = typed in Nastavení → Server. */
    gatewayAddress: null,
    /** Whether a source ('ct', 'dvb', 'psp') may be shown on this platform at all. */
    sourceAllowed: function () {
      return true;
    },
    /** function (stage) that fits the 1920×1080 stage into the window; null = the TV's own (app.js). */
    fitStage: null,
    /** function (config | null) after the gateway setting changed (also at start). */
    onGatewayConfig: null,
    /** Appended to the version line in Nastavení. */
    versionSuffix: ''
  };
})(window);
