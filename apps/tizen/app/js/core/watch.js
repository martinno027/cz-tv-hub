/*
 * Watch reports (spec/apps.md "Watch reports (T53)", spec/dvb-gateway.md "Watch log"): while the gateway's
 * `GET /dvb/status` says `"watchLog": true`, playback is reported to the household gateway with `POST /dvb/watch`:
 * `start` when a channel or programme begins playing (a new random session id each time, the old session stopped
 * first), `beat` every 60 s while the player is open (paused included), `stop` when it closes, the channel changes or
 * the app goes to the background. Fire-and-forget: a 5 s transport, no retry, every error swallowed, nothing ever
 * waits for it. Only to the gateway (its own base URL), never to ČT, the Chamber or TMDb. Nothing is shown.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var PATH = '/dvb/watch';
  var BEAT_MS = 60 * 1000;
  var TIMEOUT_MS = 5000;
  var TITLE_MAX = 200;
  var ID_MAX = 128;
  var SESSION_LENGTH = 32;
  var SESSION_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'; // 64: a byte maps evenly
  var PROFILE_HEADER = 'X-Cztvhub-Profile';

  function text(value, max) {
    if (typeof value !== 'string') return '';
    return value.trim().slice(0, max);
  }

  /** A fresh session id: 32 of [A-Za-z0-9_-] from crypto.getRandomValues (Math.random where it is missing). */
  function sessionId(randomBytes) {
    var bytes = null;
    if (randomBytes) {
      bytes = randomBytes(SESSION_LENGTH);
    } else if (root.crypto && root.crypto.getRandomValues) {
      bytes = root.crypto.getRandomValues(new Uint8Array(SESSION_LENGTH));
    }
    var out = '';
    for (var i = 0; i < SESSION_LENGTH; i++) {
      var b = bytes ? bytes[i] : Math.floor(Math.random() * 256);
      out += SESSION_CHARS.charAt(b & 63);
    }
    return out;
  }

  /** The programme a timeline window is around (its `programme` times among its `programmes`), or null. */
  function windowProgramme(tl) {
    var p = tl.programme;
    var list = tl.programmes || [];
    for (var i = 0; p && i < list.length; i++) {
      if (list[i].start === p.start && list[i].end === p.end) return list[i];
    }
    return null;
  }

  /**
   * What plays, as the report's fields: {source, mode, channel?, title?, idec?, recording?}, or null for something
   * that is not one of the reported sources. `request` = the player's request (planner.js), `target` = its resolved
   * stream, `context` = {ctOnAir, dvbOnAir: {channelId: {current}}}: live titles come from the guide the app shows;
   * an archive item knows its own. Modes (spec/dvb-gateway.md "Apps and web", one meaning on every platform): `live`
   * the live edge, `startover` anything from ČT's 3-hour buffer (Od začátku, a programme that began < 3 h ago, a
   * resume within the buffer), `archive` iVysílání VOD; Anténa `live`, `recording` (a TVHeadend recording) and
   * `timeline` (a continuous-recording window: its own id, channel and programme, so a neighbouring window is
   * reported as what it is).
   */
  function describe(request, target, context) {
    if (!request || !target) return null;
    var ctx = context || {};
    var kind = request.kind;
    var info;
    var onAir = function (map) {
      var entry = map && request.channelId ? map[request.channelId] : null;
      return entry && entry.current ? entry.current.title : '';
    };
    if (kind === 'pspLive') {
      info = { source: 'psp', mode: 'live', channel: request.channelId };
    } else if (kind === 'dvbLive') {
      info = { source: 'dvb', mode: 'live', channel: request.channelId, title: onAir(ctx.dvbOnAir) };
    } else if (kind === 'dvbRecorded') {
      var tl = target.kind === 'timeline' ? target.timeline || {} : null;
      info = { source: 'dvb', mode: tl ? 'timeline' : 'recording', channel: request.channelId,
        title: request.title, recording: request.recordingId };
      if (tl) {
        if (tl.channel) info.channel = tl.channel;
        if (tl.id) info.recording = tl.id;
        var own = windowProgramme(tl);
        if (own && own.title) info.title = own.title;
      }
    } else if (kind === 'live' || kind === 'startover' || kind === 'programme' || kind === 'archive') {
      var mode = target.kind === 'vod' ? 'archive' : target.kind === 'timeshift' ? 'startover' : 'live';
      info = { source: 'ct', mode: mode, channel: request.channelId || '' };
      info.title = kind === 'live' ? onAir(ctx.ctOnAir) : request.title;
      if (request.idec && mode === 'archive') info.idec = request.idec;
    } else {
      return null;
    }
    var out = { source: info.source, mode: info.mode };
    var channel = text(info.channel, ID_MAX);
    if (channel) out.channel = channel;
    var title = text(info.title, TITLE_MAX);
    if (title) out.title = title;
    var idec = text(info.idec, ID_MAX);
    if (idec) out.idec = idec;
    var recording = text(info.recording, ID_MAX);
    if (recording) out.recording = recording;
    return out;
  }

  /**
   * The reporter of one page. `opts` = {
   *   config() → the gateway's DvbConfig or null,
   *   send(request) → Promise: the transport for the reports (5 s timeout),
   *   sendFinal(request): the page is going away (the web: fetch keepalive); optional, else send,
   *   profileToken() → the signed-in profile's session token or null,
   *   client() → the X-Cztvhub-Client value or null (CT.ClientHeader),
   *   randomBytes(n) → n random bytes (tests); setInterval/clearInterval (tests: a fake clock) }.
   */
  function Reporter(opts) {
    this.opts = opts || {};
    this.enabled = false; // the gateway's watchLog, from the last status check
    this.info = null; // what plays now (also while the log is off: switched on, it starts)
    this.session = null; // the session id of the report in progress
    this.timer = null;
    this.setInterval = this.opts.setInterval || function (fn, ms) { return root.setInterval(fn, ms); };
    this.clearInterval = this.opts.clearInterval || function (id) { root.clearInterval(id); };
  }

  /** The request for `body`, to the gateway only; null without a gateway. */
  Reporter.prototype.request = function (body) {
    var config = this.opts.config ? this.opts.config() : null;
    if (!config) return null;
    var headers = config.authHeaders();
    headers['Content-Type'] = 'application/json';
    var client = this.opts.client ? this.opts.client() : null;
    if (client) headers[CT.ClientHeader.NAME] = client;
    var token = this.opts.profileToken ? this.opts.profileToken() : null;
    if (token) headers[PROFILE_HEADER] = token;
    return { method: 'POST', url: config.url(PATH), headers: headers, body: JSON.stringify(body) };
  };

  /** Sends and forgets: nothing is returned, no error escapes (also a transport that throws at once). */
  Reporter.prototype.post = function (body, final) {
    var request = this.request(body);
    if (!request) return;
    var send = final && this.opts.sendFinal ? this.opts.sendFinal : this.opts.send;
    try {
      var p = send(request);
      if (p && typeof p.then === 'function') p.then(null, function () {});
    } catch (e) {
      // a report never breaks anything
    }
  };

  /** The gateway's switch (every status check; a failed check or a missing field = off). */
  Reporter.prototype.setEnabled = function (on) {
    on = on === true;
    if (on === this.enabled) return;
    this.enabled = on;
    if (on) {
      if (this.info) this.begin();
    } else {
      this.end(false, true); // the log is off: nothing more is sent, the gateway drops the session itself
    }
  };

  /** Something new plays (`info` from describe()): the old session stops first, then a new one starts. */
  Reporter.prototype.play = function (info) {
    this.stop();
    if (!info) return;
    this.info = info;
    if (this.enabled) this.begin();
  };

  /** Playback ended (player closed, channel changed, app in the background); `final` = the page is going away. */
  Reporter.prototype.stop = function (final) {
    this.end(!!final, false);
    this.info = null;
  };

  Reporter.prototype.begin = function () {
    var self = this;
    this.session = sessionId(this.opts.randomBytes);
    var body = { event: 'start', session: this.session };
    Object.keys(this.info).forEach(function (k) { body[k] = self.info[k]; });
    this.post(body, false);
    this.timer = this.setInterval(function () { self.beat(); }, BEAT_MS);
  };

  Reporter.prototype.beat = function () {
    if (this.session) this.post({ event: 'beat', session: this.session }, false);
  };

  Reporter.prototype.end = function (final, silent) {
    if (this.timer !== null) this.clearInterval(this.timer);
    this.timer = null;
    var session = this.session;
    this.session = null;
    if (session && !silent) this.post({ event: 'stop', session: session }, final);
  };

  /**
   * The send for a page that is going away (pagehide): `fetch(…, {keepalive: true})` with the same headers where it
   * exists (it outlives the page), else the ordinary send. No `navigator.sendBeacon`: it cannot carry the bearer
   * header, and the gateway takes a report only with the key in the header, never as the cookie (CSRF: a foreign page
   * could otherwise write entries). `env` = {fetch, send}.
   */
  function finalSend(env) {
    return function (request) {
      if (typeof env.fetch === 'function') {
        return env.fetch(request.url, {
          method: 'POST', headers: request.headers, body: request.body, keepalive: true, credentials: 'omit'
        });
      }
      return env.send(request);
    };
  }

  CT.Watch = {
    PATH: PATH,
    BEAT_MS: BEAT_MS,
    TIMEOUT_MS: TIMEOUT_MS,
    TITLE_MAX: TITLE_MAX,
    PROFILE_HEADER: PROFILE_HEADER,
    sessionId: sessionId,
    describe: describe,
    finalSend: finalSend,
    Reporter: Reporter
  };
})(typeof window !== 'undefined' ? window : global);
