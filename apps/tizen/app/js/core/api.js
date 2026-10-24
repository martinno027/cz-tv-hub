/*
 * Client for CT's public stream and programme APIs (the ones the iVysílání web player uses).
 * The API is undocumented: every parser ends in a CtError on unknown data, never a crash.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var J = CT.Json;
  var M = CT.Messages;

  var API_BASE = 'https://api.ceskatelevize.cz';
  var STREAM_PARAMS = 'canPlayDrm=false&quality=web&streamType=hls&origin=ivysilaniweb' +
    '&usePlayability=true&clientVersion=0.0.0';
  var GRAPHQL_URL = API_BASE + '/graphql/?client=iVysilaniWeb&version=1.131.1&use-new-playability=true';
  var SCHEDULE_QUERY = [
    'query($channels: [String!]!, $date: Date!) {',
    '  TVProgramDailyChannelsPlanV2(channels: $channels, date: $date) {',
    '    channel',
    '    program { idec title episodeTitle description genre imageUrl start end isPlayableNow liveOnly }',
    '  }',
    '}'
  ].join('\n');

  function ok(status) {
    return status >= 200 && status <= 299;
  }

  /** Keep CT's Czech explanation (`message.labels.textShort`) when the error body has one. */
  function httpError(status, root) {
    var message = J.obj(root, 'message');
    var name = J.str(J.obj(message, 'status'), 'name');
    var text = J.str(J.obj(message, 'labels'), 'textShort');
    return new CT.CtError(('HTTP ' + status + ' ' + (name || '')).trim(), text || M.VIDEO_UNAVAILABLE, name);
  }

  function parseProgramme(item) {
    if (item === null || typeof item !== 'object' || Array.isArray(item)) return null;
    var idec = J.str(item, 'idec');
    var start = J.instant(item.start);
    var end = J.instant(item.end);
    if (!idec || start === null || end === null) return null;
    return {
      idec: idec,
      title: J.str(item, 'title') || '',
      start: start,
      end: end,
      isPlayableNow: J.bool(item, 'isPlayableNow'),
      liveOnly: J.bool(item, 'liveOnly'),
      episodeTitle: J.str(item, 'episodeTitle') || '',
      description: J.str(item, 'description') || '',
      genre: J.str(item, 'genre') || '',
      imageUrl: J.str(item, 'imageUrl') || ''
    };
  }

  var CtParser = {
    /** {main, timeshift|null}. Signed, IP-bound, short-lived: never persist them. */
    parseLive: function (channelId, status, body) {
      var root = J.parse(body);
      if (!ok(status)) throw httpError(status, root);
      var urls = J.obj(root, 'streamUrls');
      if (J.bool(urls, 'isBlocked')) {
        throw new CT.CtError(channelId + ' is blocked (geo restriction?)', M.BLOCKED);
      }
      var main = J.str(urls, 'main');
      if (!main) throw new CT.CtError(channelId + ': no stream URL in API response');
      return { main: main, timeshift: J.str(urls, 'timeshift') };
    },

    /** HLS URL of an archive item; throws StillLiveError when it is airing right now. */
    parseVod: function (idec, status, body) {
      var root = J.parse(body);
      if (!ok(status)) {
        var error = httpError(status, root);
        // DRM-only items are never retried through another stream; point to the official app.
        if (error.status === 'UNSUPPORTED_DRM') {
          throw new CT.CtError(error.message, error.userMessage + '\n' + M.DRM_HINT, error.status);
        }
        throw error;
      }
      var streams = J.arr(root, 'streams');
      for (var i = 0; i < streams.length; i++) {
        var url = J.str(streams[i] && typeof streams[i] === 'object' ? streams[i] : null, 'url');
        if (url) return url;
      }
      var channel = J.str(root, 'platformChannel');
      if (channel) throw new CT.StillLiveError(idec, channel);
      throw new CT.CtError(idec + ': no stream URL in API response', M.VIDEO_UNAVAILABLE);
    },

    /** Programmes of one broadcast day keyed by channel slug; malformed entries are skipped. */
    parseSchedule: function (status, body) {
      var root = J.parse(body);
      if (!ok(status)) throw new CT.CtError('schedule HTTP ' + status, M.SCHEDULE_FAILED);
      var data = J.obj(root, 'data');
      var plans = data ? data.TVProgramDailyChannelsPlanV2 : null;
      if (!Array.isArray(plans)) {
        throw new CT.CtError('schedule query failed: ' + JSON.stringify(root ? root.errors : null), M.SCHEDULE_FAILED);
      }
      var result = {};
      plans.forEach(function (plan) {
        if (plan === null || typeof plan !== 'object') return;
        var slug = J.str(plan, 'channel');
        if (!slug) return;
        result[slug] = J.arr(plan, 'program').map(parseProgramme).filter(function (p) { return p !== null; });
      });
      return result;
    }
  };

  /**
   * `transport(request)` → Promise<{status, body}>, rejecting on network failure;
   * request = {method, url, headers, body}. Tests pass a fixture transport.
   */
  function CtApi(transport) {
    this.transport = transport;
  }

  CtApi.prototype.call = function (url, jsonBody) {
    var request = {
      method: jsonBody === undefined ? 'GET' : 'POST',
      url: url,
      headers: { 'Accept': 'application/json' },
      body: jsonBody === undefined ? null : JSON.stringify(jsonBody)
    };
    if (request.body !== null) request.headers['Content-Type'] = 'application/json';
    return this.transport(request).catch(function (e) {
      throw new CT.CtError('request to ' + url.split('?')[0] + ' failed: ' + e);
    });
  };

  CtApi.prototype.liveStreams = function (channelId) {
    var url = API_BASE + '/video/v1/playlist-live/v1/stream-data/channel/' + channelId + '?' + STREAM_PARAMS;
    return this.call(url).then(function (r) { return CtParser.parseLive(channelId, r.status, r.body); });
  };

  CtApi.prototype.vodUrl = function (idec) {
    var url = API_BASE + '/video/v1/playlist-vod/v1/stream-data/media/external/' + idec + '?' + STREAM_PARAMS;
    return this.call(url).then(function (r) { return CtParser.parseVod(idec, r.status, r.body); });
  };

  CtApi.prototype.schedules = function (slugs, day) {
    var body = { query: SCHEDULE_QUERY, variables: { channels: slugs, date: CT.Days.graphqlDate(day) } };
    return this.call(GRAPHQL_URL, body).then(function (r) { return CtParser.parseSchedule(r.status, r.body); });
  };

  /**
   * XMLHttpRequest transport for the TV. Tizen's web runtime allows these cross-origin requests
   * through the `<access>` policy in config.xml (CT sends no CORS headers for the app's origin).
   */
  function xhrTransport(timeoutMs) {
    return function (request) {
      return new Promise(function (resolve, reject) {
        var xhr = new XMLHttpRequest();
        xhr.open(request.method, request.url, true);
        xhr.timeout = timeoutMs || 10000;
        Object.keys(request.headers || {}).forEach(function (k) { xhr.setRequestHeader(k, request.headers[k]); });
        xhr.onload = function () { resolve({ status: xhr.status, body: xhr.responseText || '' }); };
        xhr.onerror = function () { reject(new Error('network error')); };
        xhr.ontimeout = function () { reject(new Error('timeout')); };
        xhr.send(request.body || null);
      });
    };
  }

  /**
   * Like xhrTransport, but resolves {status, blob, type} (gateway thumbnails). The blob only lives in memory;
   * nothing is written to any storage (spec/dvb-gateway.md "Thumbnails").
   */
  function xhrBlobTransport(timeoutMs) {
    return function (request) {
      return new Promise(function (resolve, reject) {
        var xhr = new XMLHttpRequest();
        xhr.open('GET', request.url, true);
        xhr.timeout = timeoutMs || 10000;
        xhr.responseType = 'blob';
        Object.keys(request.headers || {}).forEach(function (k) { xhr.setRequestHeader(k, request.headers[k]); });
        xhr.onload = function () {
          resolve({ status: xhr.status, blob: xhr.response, type: xhr.getResponseHeader('Content-Type') || '',
            retryAfter: xhr.getResponseHeader('Retry-After') || '' });
        };
        xhr.onerror = function () { reject(new Error('network error')); };
        xhr.ontimeout = function () { reject(new Error('timeout')); };
        xhr.send(null);
      });
    };
  }

  CT.CtParser = CtParser;
  CT.xhrBlobTransport = xhrBlobTransport;
  CT.CtApi = CtApi;
  CT.xhrTransport = xhrTransport;
})(typeof window !== 'undefined' ? window : global);
