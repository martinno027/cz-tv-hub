/*
 * ČT on the web (spec/web.md "ČT (internet channels) on the web"): the browser cannot ask ČT's API itself (no CORS for
 * other origins), so the CT API calls of the shared code go to the household gateway, which asks ČT and answers ČT's
 * status and JSON unchanged (spec/dvb-gateway.md "ČT for the web"). The shared parsers (api.js) then read them as on
 * the TV. Only the three calls the apps make are mapped; anything else is refused here and never sent anywhere.
 *
 *   GET  …/playlist-live/v1/stream-data/channel/<CH_n>?…   → GET /dvb/ct/live/<CH_n>
 *   GET  …/playlist-vod/v1/stream-data/media/external/<idec>?… → GET /dvb/ct/vod/<idec>
 *   POST …/graphql/… {variables: {channels, date: MM.DD.YYYY}} → GET /dvb/ct/schedule?channels=a,b&date=YYYY-MM-DD
 *     (the gateway uses its own fixed query: the page's query text is never sent)
 *
 * The gateway's own refusals ({"error": "…"}: ČT unreachable, a bad key) are given to the parsers in
 * ČT's error shape, so the viewer reads the gateway's Czech text instead of a generic one.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var API = 'https://api.ceskatelevize.cz';
  var LIVE = /^https:\/\/api\.ceskatelevize\.cz\/video\/v1\/playlist-live\/v1\/stream-data\/channel\/([A-Za-z0-9_]+)(\?|$)/;
  var VOD = /^https:\/\/api\.ceskatelevize\.cz\/video\/v1\/playlist-vod\/v1\/stream-data\/media\/external\/([A-Za-z0-9_-]+)(\?|$)/;
  var GRAPHQL = /^https:\/\/api\.ceskatelevize\.cz\/graphql\/(\?|$)/;

  var NO_KEY = 'Zadejte klíč brány v Nastavení → Server.';

  /** 'MM.DD.YYYY' (api.js, CT's GraphQL) → 'YYYY-MM-DD' (the gateway), or null. */
  function isoDate(graphqlDate) {
    var m = /^(\d\d)\.(\d\d)\.(\d{4})$/.exec(String(graphqlDate || ''));
    return m ? m[3] + '-' + m[1] + '-' + m[2] : null;
  }

  /** The gateway path for a CT API request of the shared code, or null when it is not one the web may make. */
  function route(request) {
    var url = String(request.url || '');
    var method = request.method || 'GET';
    var m;
    if (method === 'GET' && (m = LIVE.exec(url))) return '/dvb/ct/live/' + m[1];
    if (method === 'GET' && (m = VOD.exec(url))) return '/dvb/ct/vod/' + m[1];
    if (method === 'POST' && GRAPHQL.test(url)) {
      var body;
      try {
        body = JSON.parse(request.body || '');
      } catch (e) {
        return null;
      }
      var v = body && body.variables;
      var date = isoDate(v && v.date);
      var channels = v && Array.isArray(v.channels) ? v.channels : null;
      if (!date || !channels || !channels.length) return null;
      if (channels.some(function (c) { return typeof c !== 'string' || !/^[a-z0-9]+$/.test(c); })) return null;
      return '/dvb/ct/schedule?channels=' + channels.join(',') + '&date=' + date; // slugs checked above
    }
    return null;
  }

  /** A ČT-shaped error body (what api.js reads: message.status.name, message.labels.textShort). */
  function ctError(name, text) {
    return JSON.stringify({ message: { status: { name: name }, labels: { textShort: text } } });
  }

  /**
   * The gateway's answer for the parsers: ČT's answers (2xx, or an error with ČT's own `message`) unchanged; the
   * gateway's own `{"error": "…"}` refusals in ČT's error shape with the gateway's text (`GATEWAY_<status>`).
   */
  function answer(status, body) {
    if (status >= 200 && status <= 299) return { status: status, body: body };
    var parsed = null;
    try {
      parsed = JSON.parse(body || '');
    } catch (e) {
      parsed = null;
    }
    var own = parsed && typeof parsed === 'object' && !Array.isArray(parsed) && typeof parsed.error === 'string' &&
      !parsed.message;
    if (!own) return { status: status, body: body };
    var text = status === 401 ? CT.DvbMessages.BAD_KEY : parsed.error;
    return { status: status, body: ctError('GATEWAY_' + status, text) };
  }

  /**
   * The CT transport of the web: `base` = the app's XHR transport (or the demo's), `config()` = the gateway setting
   * (CT.DvbConfig) or null. Without a key nothing is sent ("Zadejte klíč…"); the key goes as the header.
   */
  function transport(base, config) {
    return function (request) {
      var path = route(request);
      if (!path) return Promise.reject(new Error('not a CT request the web makes: ' + String(request.url).split('?')[0]));
      var c = config();
      if (!c) return Promise.resolve({ status: 401, body: ctError('NO_KEY', NO_KEY) });
      var headers = { 'Accept': 'application/json' };
      var auth = c.authHeaders();
      Object.keys(auth).forEach(function (k) { headers[k] = auth[k]; });
      return base({ method: 'GET', url: c.url(path), headers: headers, body: null }).then(function (r) {
        return answer(r.status, r.body);
      });
    };
  }

  CT.WebCt = {
    API: API,
    NO_KEY: NO_KEY,
    isoDate: isoDate,
    route: route,
    answer: answer,
    transport: transport
  };
})(typeof window !== 'undefined' ? window : global);
