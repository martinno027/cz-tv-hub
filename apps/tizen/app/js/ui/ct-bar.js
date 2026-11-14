/*
 * The ČT player bar (spec/apps.md "ČT player bar (T62)"): ČT start-over / "Zpět v čase" and the iVysílání archive
 * play with the Anténa timeline's own bar and keys (ui/timeline.js TimelineController), over one window of their own:
 * - timeshift: the 3 h buffer `now − 3 h` … `now`, its start sliding with now. AVPlay and the web have no wall clock of
 *   the stream (no PROGRAM-DATE-TIME), so the position ↔ wall clock map is the estimate start-over and "Pokračovat"
 *   already use: position 0 = the moment of planning − 3 h. `now` is the END OF THE STREAM (ČT's stream ends minutes
 *   behind the clock): position 0 + the player's current length, once it knows one, else the clock (streamNow).
 *   Titles and ticks come from the ČT guide of that channel.
 * - vod: the programme's start (guide / archive) + position, complete; without a known start the labels are the
 *   elapsed position.
 * No neighbouring windows, no ad breaks; seeks and the moving playhead stay inside the clamps of core/timeline.js
 * (ctClamp). Nothing here changes which stream plays or how it is resolved.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var T = CT.Timeline;

  var DURATION_CHECK_MS = 500; // a VOD asks the player for its length at most this often (not every frame)

  function num(v) {
    return typeof v === 'number' && isFinite(v);
  }

  /** The request's programme length (s) as far as it is known. */
  function requestLength(request) {
    if (num(request.lengthSeconds) && request.lengthSeconds > 0) return request.lengthSeconds;
    if (num(request.start) && num(request.end) && request.end > request.start) return (request.end - request.start) / 1000;
    return 0;
  }

  /** A ČT guide programme → the bar's programme (title, ticks, "Pokračovat" by idec). */
  function barProgramme(p) {
    return {
      id: p.idec, idec: p.idec, title: p.title || '', subtitle: p.episodeTitle || '', start: p.start, end: p.end,
      breaks: false, imageUrl: /^https:\/\//.test(p.imageUrl || '') ? p.imageUrl : ''
    };
  }

  /** `mode` = 'timeshift' | 'vod' (CT.Timeline.barMode). */
  function CtBarSource(player, mode) {
    this.player = player;
    this.kind = 'ct';
    this.mode = mode;
    this.closesAtEnd = true; // the end of a ČT stream closes the player as before
    this.controller = null;
    this.days = null; // timeshift: the broadcast days the guide was asked for ("a|b")
    this.durationCheckedAt = 0;
    this.lag = 0; // timeshift: ms the stream's end lies behind the clock (measured from the player's length)
    this.lagCheckedAt = 0;
  }

  CtBarSource.prototype.attach = function (controller) {
    this.controller = controller;
  };

  CtBarSource.prototype.owns = function (target) {
    return T.barMode(target) === this.mode;
  };

  /** The window of a freshly resolved target (the player's plannedAt is set). */
  CtBarSource.prototype.window = function (target) {
    var player = this.player;
    var request = player.request;
    var channel = CT.Channels.byId(target.channelId || request.channelId);
    var tl = {
      id: 'ct:' + this.mode + ':' + (request.idec || target.channelId || request.channelId || ''),
      channel: channel ? channel.id : '', channelName: channel ? channel.name : '', playlist: null,
      complete: false, programme: null, pieces: null, programmes: [], previous: null, next: null, elapsed: false,
      fallback: { title: request.title || '', subtitle: request.episodeTitle || '' }, origin: 0
    };
    if (this.mode === 'timeshift') {
      tl.origin = player.plannedAt - CT.Decisions.TIMESHIFT_WINDOW_MS; // position 0 (the estimate)
      this.lag = 0; // a new stream: the clock until its player tells its length
      this.lagCheckedAt = 0;
      tl.pieces = [{ position: 0, time: tl.origin, duration: CT.Decisions.TIMESHIFT_WINDOW_MS / 1000 }];
      tl.programme = { start: num(request.start) ? request.start : tl.origin, end: player.plannedAt };
      this.days = null;
      this.update(tl, Date.now());
      this.loadGuide(tl, Date.now());
      return tl;
    }
    var w = T.ctWindow('vod', Date.now(), num(request.start) ? request.start : null, 0);
    tl.elapsed = w.start === null;
    tl.origin = tl.elapsed ? 0 : w.start;
    tl.pieces = [{ position: 0, time: tl.origin, duration: 0 }];
    tl.programmes = [{ id: request.idec || null, idec: request.idec || null, title: request.title || '',
      subtitle: request.episodeTitle || '', start: tl.origin, end: tl.origin, breaks: false, imageUrl: '' }];
    tl.programme = { start: tl.origin, end: tl.origin };
    this.setLength(tl, requestLength(request));
    this.durationCheckedAt = 0;
    return tl;
  };

  /** A VOD's length (s): the piece, the programme, `complete` (an unknown length = no end yet). */
  CtBarSource.prototype.setLength = function (tl, seconds) {
    tl.pieces[0].duration = seconds;
    tl.programmes[0].end = tl.programme.end = tl.origin + Math.round(seconds * 1000);
    tl.complete = seconds > 0;
  };

  /** Every render: the buffer slides with now; a VOD takes the player's own length once it knows it. */
  CtBarSource.prototype.update = function (tl, now) {
    if (this.mode === 'timeshift') {
      var w = T.ctWindow('timeshift', this.streamNow(tl, now));
      var piece = tl.pieces[0];
      piece.position = (w.start - tl.origin) / 1000;
      piece.time = w.start;
      piece.duration = (w.end - w.start) / 1000;
      return;
    }
    if (now - this.durationCheckedAt < DURATION_CHECK_MS) return;
    this.durationCheckedAt = now;
    var seconds = this.player.backend.duration() / 1000;
    if (seconds > 0 && Math.abs(seconds - tl.pieces[0].duration) >= 1) {
      this.setLength(tl, seconds);
      if (this.controller && this.controller.tl === tl) this.controller.buildBar();
    }
  };

  /**
   * The timeshift window's `now` (spec "ČT player bar"): the wall clock of the stream's end = position 0 + the
   * player's seekable length (AVPlay getDuration, the web's video.seekable end), asked at most every 0.5 s; between
   * two readings it runs on with the clock. Before the player knows a length: the clock. Never later than the clock
   * (the estimate cannot know an end ahead of now).
   */
  CtBarSource.prototype.streamNow = function (tl, now) {
    if (now - this.lagCheckedAt >= DURATION_CHECK_MS) {
      this.lagCheckedAt = now;
      var b = this.player.backend;
      var length = b.seekableEnd ? b.seekableEnd() : b.duration();
      if (length > 0) this.lag = Math.max(0, now - (tl.origin + length));
    }
    return now - this.lag;
  };

  /** The `now` of the bar's labels ("Teď" at the stream's end). */
  CtBarSource.prototype.labelNow = function (tl, now) {
    return this.mode === 'timeshift' ? this.streamNow(tl, now) : now;
  };

  /** Positions (s) the playhead may reach now: ctClamp through this window's map. */
  CtBarSource.prototype.range = function (tl, now) {
    if (this.mode === 'timeshift') {
      var at = function (time) { return (time - tl.origin) / 1000; };
      var end = this.streamNow(tl, now);
      return { lo: at(T.ctClamp('timeshift', end, -Infinity)), hi: at(T.ctClamp('timeshift', end, Infinity)) };
    }
    return { lo: 0, hi: T.ctClamp('vod', now, Infinity, tl.complete ? tl.pieces[0].duration : 0) };
  };

  /** Every half second: the guide again once the buffer reaches into another broadcast day. */
  CtBarSource.prototype.tick = function (tl, now) {
    if (this.mode === 'timeshift') this.loadGuide(tl, now);
  };

  /**
   * The programmes of this channel in the buffer from the ČT guide the app already keeps (ScheduleRepository: one
   * request per broadcast day, cached); the bar's ticks and title follow once they are there. A failed day leaves
   * the title of what was opened.
   */
  CtBarSource.prototype.loadGuide = function (tl, now) {
    var self = this;
    var schedules = this.player.app.schedules;
    if (!schedules || !tl.channel) return;
    var from = CT.Days.openDay(now - CT.Decisions.TIMESHIFT_WINDOW_MS);
    var to = CT.Days.openDay(now);
    var days = from === to ? [to] : [from, to];
    var key = days.join('|');
    if (this.days === key) return;
    this.days = key;
    Promise.all(days.map(function (day) {
      return schedules.day(day).then(function (plan) { return plan[tl.channel] || []; }, function () { return []; });
    })).then(function (lists) {
      var seen = {};
      var programmes = [];
      lists.forEach(function (list) {
        list.forEach(function (p) {
          if (seen[p.idec + '@' + p.start]) return;
          seen[p.idec + '@' + p.start] = true;
          programmes.push(barProgramme(p));
        });
      });
      programmes.sort(function (a, b) { return a.start - b.start; });
      var ctl = self.controller;
      if (!programmes.length || !ctl || ctl.tl !== tl) return;
      tl.programmes = programmes;
      ctl.buildBar();
      ctl.render();
    });
  };

  CtBarSource.prototype.openWindow = function () {
    return Promise.reject(new CT.CtError('the ČT bar has one window'));
  };

  CtBarSource.prototype.errorText = function (e) {
    return e && e.userMessage ? e.userMessage : CT.Messages.GENERIC;
  };

  CtBarSource.prototype.refresh = function () {
    return null; // nothing to fetch: the buffer slides by itself, the guide comes with tick
  };

  CtBarSource.prototype.breaks = function () {
    return Promise.resolve([]);
  };

  /**
   * "Pokračovat" as before (`ct:<idec>`): timeshift = the programme at the playhead with an idec, its wall clock −
   * its start (the opened one keeps the request's Archiv row; without the guide the request's own programme, as
   * before); VOD = the request's key at the position.
   */
  CtBarSource.prototype.save = function (store, request, programme, time, position) {
    if (!request) return;
    if (this.mode === 'vod') {
      if (request.resumeKey) store.save(request.resumeKey, position, request.lengthSeconds, request.resumeMeta);
      return;
    }
    var tl = this.controller ? this.controller.tl : null;
    // in the 30 s before the opened programme's start the playhead counts as that programme (progress 0): starting
    // it does not clear the previous programme's "Pokračovat"
    var opened = num(request.start) && time < request.start && time >= request.start - CT.Decisions.START_MARGIN_MS;
    if (opened) {
      var own = tl ? tl.programmes.filter(function (p) { return p.start === request.start && p.idec; })[0] : null;
      if (own) {
        programme = own;
      } else {
        if (request.resumeKey) store.save(request.resumeKey, 0, request.lengthSeconds, request.resumeMeta);
        return;
      }
    }
    if (programme && programme.idec) {
      var key = 'ct:' + programme.idec;
      var mine = request.resumeKey === key;
      var meta = mine && request.resumeMeta ? request.resumeMeta
        : CT.ResumeRules.meta(programme.title, programme.subtitle, programme.imageUrl, 'ct', programme.start, tl ? tl.channel : null);
      store.save(key, T.progress(programme, time), (programme.end - programme.start) / 1000, meta);
      return;
    }
    if (tl && !tl.programmes.length && request.resumeKey && num(request.start)) {
      var progress = (time - request.start) / 1000;
      if (progress >= 0) store.save(request.resumeKey, progress, request.lengthSeconds, request.resumeMeta);
    }
  };

  /** No window to continue in: the player's own position (resumePosition) after re-resolving. */
  CtBarSource.prototype.continueAt = function () {
    return null;
  };

  CT.CtBarSource = CtBarSource;
})(window);
