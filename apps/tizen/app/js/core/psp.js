/*
 * "Poslanecká sněmovna" (spec/apps.md, T06): the Chamber of Deputies' five public live streams, played directly on
 * the TV from the URL the Chamber publishes (no headers, no cookie, live only: no recording, no "Pokračovat", no
 * start-over, no thumbnails). Golden cases: spec/fixtures/psp_cases.json.
 *
 * PspMonitor answers "is it on now?" by reading only the playlist (never a segment): one stream at a time, each at
 * most once per 60 s (120 s after an error), and only when the UI calls tick() (Živě or the guide on screen).
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var BASE = 'https://pspcr-ott-live.ssl.cdn.cra.cz/channels/';

  function stream(n, name) {
    var id = 'stream' + n;
    return { id: id, key: 'psp:' + id, name: name, url: BASE + 'ps-' + id + '/playlist/cze.m3u8' };
  }

  var Psp = {
    /** The catalogue, in default order; no other URLs, no mirrors. */
    streams: [
      stream(1, 'Schůze PS'),
      stream(2, 'Tiskové konference'),
      stream(3, 'Výbory a semináře 1'),
      stream(4, 'Výbory a semináře 2'),
      stream(5, 'Výbory a semináře 3')
    ],

    CHECK_MS: 60 * 1000,
    ERROR_MS: 120 * 1000,
    STALE_MS: 180 * 1000,

    SOURCE_LABEL: 'Poslanecká sněmovna PČR',
    SWITCH_LABEL: 'Poslanecká sněmovna',
    OFF_MESSAGE: 'Poslanecká sněmovna teď nevysílá.',
    LIVE_TEXT: 'Přímý přenos',
    OFF_TEXT: 'Nevysílá se',
    UNKNOWN_TEXT: 'Zjišťuji…',
    ONLY_LIVE_TEXT: 'Jen přímé přenosy',
    ONLY_LIVE_MESSAGE: 'Poslanecká sněmovna: jen přímé přenosy, bez záznamů.',

    byId: function (id) {
      return Psp.streams.filter(function (s) { return s.id === id; })[0] || null;
    },

    /**
     * On now = HTTP 200 and an HLS playlist with at least one media segment or variant. Anything else (404 "channel
     * ps-streamN is not processed now" outside broadcasts, 5xx, an empty playlist, HTML) = not on.
     */
    isLive: function (status, body) {
      if (status !== 200 || typeof body !== 'string') return false;
      if (!/^﻿?\s*#EXTM3U/.test(body)) return false;
      return /^#EXTINF:/m.test(body) || /^#EXT-X-STREAM-INF:/m.test(body);
    }
  };

  /**
   * `fetch(stream)` → Promise<{status, body}>, rejecting on a network error (the app's transport with only the URL:
   * no headers, no cookie); `clock()` → ms. States per stream id: 'live' | 'off' | 'unknown'.
   */
  function PspMonitor(fetch, clock) {
    this.fetch = fetch;
    this.clock = clock || Date.now;
    this.items = {}; // id → {live: bool | null, error: last check failed, confirmedAt, checkedAt, nextAt}
    this.busy = false;
    this.onChange = null; // (id) after every finished check
  }

  PspMonitor.prototype.item = function (id) {
    if (!this.items[id]) this.items[id] = { live: null, error: false, confirmedAt: 0, checkedAt: 0, nextAt: 0 };
    return this.items[id];
  };

  /** 'live' only while a check confirmed it within the last 3 minutes; then 'unknown' until the next answer. */
  PspMonitor.prototype.state = function (id) {
    var s = this.items[id];
    if (!s || s.live === null) return 'unknown';
    if (!s.live) return 'off';
    return this.clock() - s.confirmedAt <= Psp.STALE_MS ? 'live' : 'unknown';
  };

  /**
   * One finished check: `response` = {status, body}, or null for a network error (keeps the last state, retried
   * after 120 s). A 5xx is "not on", also retried after 120 s; any other answer after 60 s.
   */
  PspMonitor.prototype.record = function (id, response) {
    var now = this.clock();
    var s = this.item(id);
    s.checkedAt = now;
    s.error = !response || !response.status;
    if (s.error) {
      s.nextAt = now + Psp.ERROR_MS;
      return;
    }
    s.live = Psp.isLive(response.status, response.body);
    if (s.live) s.confirmedAt = now;
    s.nextAt = now + (response.status >= 500 ? Psp.ERROR_MS : Psp.CHECK_MS);
  };

  /** The stream of `ids` to check next (the longest overdue, catalogue order on ties), or null. */
  PspMonitor.prototype.due = function (ids) {
    var now = this.clock();
    var self = this;
    var best = null;
    ids.forEach(function (id) {
      var s = self.items[id];
      var at = s ? s.nextAt : 0;
      if (at <= now && (best === null || at < best.at)) best = { id: id, at: at };
    });
    return best ? best.id : null;
  };

  PspMonitor.prototype.run = function (id) {
    var self = this;
    var stream = Psp.byId(id);
    if (!stream) return Promise.resolve();
    var done = function (response) {
      self.record(id, response);
      if (self.onChange) self.onChange(id);
    };
    var request;
    try {
      request = Promise.resolve(this.fetch(stream));
    } catch (e) {
      request = Promise.reject(e);
    }
    return request.then(done, function () { done(null); });
  };

  /**
   * Starts at most one due check among `ids` (the visible streams) unless one is still running. Called about once a
   * second by the app, and only while Živě or the guide is on screen; returns the running check or null.
   */
  PspMonitor.prototype.tick = function (ids) {
    if (this.busy) return null;
    var id = this.due(ids);
    if (id === null) return null;
    var self = this;
    this.busy = true;
    return this.run(id).then(function () { self.busy = false; });
  };

  /**
   * Before playback: the state to act on. A check younger than 60 s is reused (no request); `force` (after a
   * playback error) reads the playlist again so a 404 = "the broadcast just ended". Resolves 'live' | 'off' |
   * 'unknown' (network error: try to play).
   */
  PspMonitor.prototype.open = function (id, force) {
    var self = this;
    var s = this.items[id];
    var fresh = s && !s.error && s.live !== null && this.clock() - s.checkedAt < Psp.CHECK_MS;
    if (fresh && !force) return Promise.resolve(this.state(id));
    return this.run(id).then(function () {
      // A network error just now: nothing is confirmed either way, let the player try.
      return self.items[id].error ? 'unknown' : self.state(id);
    });
  };

  CT.Psp = Psp;
  CT.PspMonitor = PspMonitor;
})(typeof window !== 'undefined' ? window : global);
