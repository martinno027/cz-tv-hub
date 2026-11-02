/*
 * `?demo` on the web: the shared demo (demo.js) answers the gateway and CT's API from spec/fixtures. The web asks
 * the gateway's /dvb/ct/… instead of CT (web-ct.js), so this turns those requests back into the CT requests the
 * demo knows, which also runs the web's mapping in the browser. Nothing leaves the PC.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var API = 'https://api.ceskatelevize.cz';

  /** The gateway's CT route → the CT request the shared demo answers (null for anything else). */
  function ctRequest(url) {
    var path = String(url).replace(/^https?:\/\/[^/]+/, '');
    var m = /^\/dvb\/ct\/live\/([A-Za-z0-9_]+)$/.exec(path);
    if (m) return { method: 'GET', url: API + '/video/v1/playlist-live/v1/stream-data/channel/' + m[1] + '?', headers: {}, body: null };
    m = /^\/dvb\/ct\/vod\/([A-Za-z0-9_-]+)$/.exec(path);
    if (m) return { method: 'GET', url: API + '/video/v1/playlist-vod/v1/stream-data/media/external/' + m[1] + '?', headers: {}, body: null };
    m = /^\/dvb\/ct\/schedule\?channels=([^&]+)&date=(\d{4})-(\d\d)-(\d\d)$/.exec(path);
    if (m) {
      var body = { variables: { channels: decodeURIComponent(m[1]).split(','), date: m[3] + '.' + m[4] + '.' + m[2] } };
      return { method: 'POST', url: API + '/graphql/?', headers: {}, body: JSON.stringify(body) };
    }
    return null;
  }

  /** `base` = CT.Demo.transport. */
  function wrap(base) {
    return function (request) {
      var ct = ctRequest(request.url);
      if (!ct) return base(request);
      return base(ct);
    };
  }

  CT.WebDemo = { ctRequest: ctRequest, wrap: wrap };
})(typeof window !== 'undefined' ? window : global);
