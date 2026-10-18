/*
 * Play requests (what the viewer asked for; never a stream URL or key) and the planner that turns
 * them into a freshly resolved stream right before playback.
 *
 * Requests: {kind: 'live', channelId, title}
 *           {kind: 'startover', channelId, title, start}
 *           {kind: 'programme', channelId, title, idec, start, end, isPlayableNow}
 *           {kind: 'dvbLive', channelId, title}
 *           {kind: 'dvbRecorded', channelId, title, recordingId, fromStart}
 *           Optional on startover/programme/dvbRecorded: resumeSeconds ("Pokračovat"), and for saving the progress
 *           resumeKey (`ct:<idec>` / `dvb:<recording id>`), lengthSeconds; startover/programme also carry `start`.
 * Targets:  {url, kind: 'live' | 'timeshift' | 'vod', title, seekToWallClock, fallbackOffsetMs,
 *            startPositionMs, dvb: DvbClient | null, recordingId: finished Anténa recording | null}
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var M = CT.Messages;

  function target(url, kind, title, extra) {
    var t = {
      url: url, kind: kind, title: title, seekToWallClock: null, fallbackOffsetMs: 0, startPositionMs: null, dvb: null,
      recordingId: null
    };
    return Object.assign(t, extra || {});
  }

  /** `dvb()` returns the configured DvbClient or null. */
  function Planner(api, live, clock, dvb) {
    this.api = api;
    this.live = live || new CT.LiveResolver(api);
    this.clock = clock || Date.now;
    this.dvb = dvb || function () { return null; };
  }

  /** Resolves to a target or rejects with CtError carrying a Czech message. */
  Planner.prototype.plan = function (request, forceRefresh) {
    try {
      if (request.kind === 'dvbLive' || request.kind === 'dvbRecorded') return this.planDvb(request);
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
    var url = client.recordingUrl(request.recordingId);
    var resume = request.resumeSeconds;
    return client.warmUp(url).then(function () {
      // A finished recording can carry ad breaks; the growing "Od začátku" one does not (v1).
      return target(url, 'vod', request.title, {
        dvb: client,
        startPositionMs: resume ? resume * 1000 : (request.fromStart ? 0 : null),
        recordingId: request.fromStart ? null : request.recordingId
      });
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
