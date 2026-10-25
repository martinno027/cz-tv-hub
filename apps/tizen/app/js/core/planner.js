/*
 * Play requests (what the viewer asked for; never a stream URL or key) and the planner that turns
 * them into a freshly resolved stream right before playback.
 *
 * Requests: {kind: 'live', channelId, title}
 *           {kind: 'startover', channelId, title, start}
 *           {kind: 'programme', channelId, title, idec, start, end, isPlayableNow}
 *           {kind: 'dvbLive', channelId, title}
 *           {kind: 'dvbRecorded', channelId, title, recordingId, fromStart}
 *           (a continuous-recording id plays through the channel timeline, a TVHeadend DVR id as before)
 *           {kind: 'pspLive', channelId: Chamber stream id, title} (live only)
 *           {kind: 'archive', idec, title, start?} (an iVysílání episode from the Archiv tab without a known CT
 *           channel and broadcast time: VOD only; airing now → start-over (with `start`) or live on CT's channel)
 *           Optional on startover/programme/dvbRecorded/archive: resumeSeconds ("Pokračovat"), and for saving the
 *           progress resumeKey (`ct:<idec>` / `dvb:<recording id>`), lengthSeconds, resumeMeta (the Archiv row's
 *           title, showTitle, image, source); startover/programme also carry `start`. Optional for the player's
 *           info bar: episodeTitle, channelName (when channelId names no known channel).
 * Targets:  {url, kind: 'live' | 'timeshift' | 'vod' | 'timeline', title, seekToWallClock, fallbackOffsetMs,
 *            startPositionMs, dvb: DvbClient | null, recordingId: finished Anténa recording | null,
 *            timeline: the parsed window (kind 'timeline') | null,
 *            alwaysSeek: seek to startPositionMs even when it is 0 (a growing playlist starts at its live edge),
 *            psp: bool}
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var M = CT.Messages;

  function target(url, kind, title, extra) {
    var t = {
      url: url, kind: kind, title: title, seekToWallClock: null, fallbackOffsetMs: 0, startPositionMs: null, dvb: null,
      recordingId: null, timeline: null, alwaysSeek: false, psp: false
    };
    return Object.assign(t, extra || {});
  }

  /**
   * `dvb()` returns the configured DvbClient or null; `psp` = {monitor: PspMonitor, playUrl(stream) → url (optional,
   * the published URL by default; the browser demo swaps it so it never contacts the Chamber's CDN)}.
   */
  function Planner(api, live, clock, dvb, psp) {
    this.api = api;
    this.live = live || new CT.LiveResolver(api);
    this.clock = clock || Date.now;
    this.dvb = dvb || function () { return null; };
    this.psp = psp || null;
  }

  /** Resolves to a target or rejects with CtError carrying a Czech message. */
  Planner.prototype.plan = function (request, forceRefresh) {
    try {
      if (request.kind === 'dvbLive' || request.kind === 'dvbRecorded') return this.planDvb(request);
      if (request.kind === 'pspLive') return this.planPsp(request, forceRefresh);
      if (request.kind === 'archive') return this.archive(request, forceRefresh);
      var channel = CT.Channels.byId(request.channelId);
      if (!channel) throw new CT.CtError('unknown channel ' + request.channelId);
      switch (request.kind) {
        case 'live':
          return this.live.resolve(channel.id, forceRefresh).then(function (s) {
            return target(s.main, 'live', request.title);
          });
        case 'startover':
          return this.startover(channel, request.title, request.start, forceRefresh, request.resumeSeconds);
        case 'programme':
          return this.programme(channel, request, forceRefresh);
        default:
          throw new CT.CtError('unknown request ' + request.kind);
      }
    } catch (e) {
      return Promise.reject(e);
    }
  };

  Planner.prototype.planDvb = function (request) {
    var client = this.dvb();
    if (!client) return Promise.reject(new CT.CtError('DVB not configured', CT.DvbMessages.UNREACHABLE));
    if (request.kind === 'dvbLive') {
      var liveUrl = client.liveUrl(request.channelId);
      return client.warmUp(liveUrl).then(function () {
        return target(liveUrl, 'live', request.title, { dvb: client });
      });
    }
    if (CT.Timeline.isTimelineId(request.recordingId)) {
      return this.timeline(client, request.recordingId, request.title, function (tl) {
        return CT.Timeline.openTime(tl, request.resumeSeconds);
      });
    }
    var url = client.recordingUrl(request.recordingId);
    var resume = request.resumeSeconds;
    return client.warmUp(url).then(function () {
      // A finished recording can carry ad breaks; the growing "Od začátku" one does not (v1).
      return target(url, 'vod', request.title, {
        dvb: client,
        startPositionMs: resume ? resume * 1000 : (request.fromStart ? 0 : null),
        // "Od začátku" of a growing DVR recording: an explicit seek to 0, else AVPlay starts it live
        alwaysSeek: !!request.fromStart && !resume,
        recordingId: request.fromStart ? null : request.recordingId
      });
    });
  };

  /**
   * A window of the channel timeline around programme `id`, starting at the wall clock `timeAt(timeline)` mapped to
   * its position (also for the neighbouring window, which continues at the same wall clock). AVPlay has no wall-clock
   * seek, so the map is the only way there; the playlist's EXT-X-START is the fallback of players that skip the seek.
   */
  Planner.prototype.timeline = function (client, id, title, timeAt) {
    return client.timeline(id).then(function (tl) {
      if (!tl.id) tl.id = id;
      var url = client.config.url(tl.playlist);
      return client.warmUp(url).then(function () {
        var position = CT.Timeline.toPosition(tl.pieces, timeAt(tl));
        return target(url, 'timeline', title, {
          dvb: client, timeline: tl, startPositionMs: Math.round(position * 1000), alwaysSeek: true
        });
      });
    });
  };

  /** The neighbouring window `id` (previous/next of the playing one), continuing at the wall clock `time`. */
  Planner.prototype.timelineWindow = function (id, title, time) {
    var client = this.dvb();
    if (!client) return Promise.reject(new CT.CtError('DVB not configured', CT.DvbMessages.UNREACHABLE));
    return this.timeline(client, id, title, function () { return time; });
  };

  /**
   * Poslanecká sněmovna: the published URL as is (AVPlay sends no cookie or header for it), live only. Not on now
   * (a fresh check, or the playlist answering 404 right now) → "Poslanecká sněmovna teď nevysílá.".
   */
  Planner.prototype.planPsp = function (request, forceRefresh) {
    var psp = this.psp;
    var stream = CT.Psp.byId(request.channelId);
    if (!stream || !psp) return Promise.reject(new CT.CtError('unknown stream ' + request.channelId));
    return psp.monitor.open(stream.id, !!forceRefresh).then(function (state) {
      if (state === 'off') throw new CT.CtError(stream.id + ' not on air', CT.Psp.OFF_MESSAGE, 'PSP_OFF');
      var url = psp.playUrl ? psp.playUrl(stream) : stream.url;
      return target(url, 'live', request.title, { psp: true });
    });
  };

  Planner.prototype.programme = function (channel, request, forceRefresh) {
    var self = this;
    var decision = CT.Decisions.decide(request.start, request.end, request.isPlayableNow, this.clock());
    switch (decision.action) {
      case 'future':
        return Promise.reject(new CT.CtError(request.idec + ' has not started', M.FUTURE));
      case 'startover':
        return this.startover(channel, request.title, request.start, forceRefresh, request.resumeSeconds);
      default:
        // Not playable: still ask the API so the viewer gets CT's reason.
        return this.api.vodUrl(request.idec).then(function (url) {
          var resume = request.resumeSeconds;
          return target(url, 'vod', request.title, { startPositionMs: resume ? resume * 1000 : null });
        }, function (e) {
          if (!(e instanceof CT.StillLiveError)) throw e;
          var airingOn = CT.Channels.byId(e.channelId) || channel;
          return self.startover(airingOn, request.title, request.start, forceRefresh, request.resumeSeconds);
        });
    }
  };

  /**
   * An iVysílání episode by idec (Archiv): CT's VOD as for the guide's archive; CT's own text when it cannot play
   * (licence, DRM). Airing right now: from the start in the buffer when the start is known, else live.
   */
  Planner.prototype.archive = function (request, forceRefresh) {
    var self = this;
    var resume = request.resumeSeconds;
    return this.api.vodUrl(request.idec).then(function (url) {
      return target(url, 'vod', request.title, { startPositionMs: resume ? resume * 1000 : null });
    }, function (e) {
      if (!(e instanceof CT.StillLiveError)) throw e;
      var channel = CT.Channels.byId(e.channelId);
      if (!channel) throw e;
      if (typeof request.start === 'number') return self.startover(channel, request.title, request.start, forceRefresh, resume);
      return self.live.resolve(channel.id, forceRefresh).then(function (s) {
        return target(s.main, 'live', request.title);
      });
    });
  };

  /** From the programme start (minus the margin), or from `resumeSeconds` into it ("Pokračovat"). */
  Planner.prototype.startover = function (channel, title, start, forceRefresh, resumeSeconds) {
    var self = this;
    return this.live.resolve(channel.id, forceRefresh).then(function (s) {
      if (!s.timeshift) throw new CT.CtError(channel.id + ' has no timeshift', M.NO_TIMESHIFT);
      var now = self.clock();
      if (resumeSeconds) {
        var point = start + resumeSeconds * 1000;
        var bufferStart = now - CT.Decisions.TIMESHIFT_WINDOW_MS;
        return target(s.timeshift, 'timeshift', title, {
          seekToWallClock: point,
          fallbackOffsetMs: Math.max(0, Math.floor((point - bufferStart) / 1000)) * 1000
        });
      }
      return target(s.timeshift, 'timeshift', title, {
        seekToWallClock: start - CT.Decisions.START_MARGIN_MS,
        fallbackOffsetMs: CT.Decisions.startoverOffsetSeconds(start, now) * 1000
      });
    });
  };

  CT.Planner = Planner;
})(typeof window !== 'undefined' ? window : global);
