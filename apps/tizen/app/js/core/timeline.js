/*
 * "Anténa" timeline player (spec/apps.md "Anténa timeline player"): a continuous-recording programme plays inside a
 * window of its channel's recording (`GET /dvb/timeline/<id>`). AVPlay has no wall-clock seek, so positions (seconds
 * in the timeline playlist) and wall clock (epoch ms) are converted through the JSON's `pieces`. Golden cases:
 * spec/fixtures/timeline_cases.json.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var J = CT.Json;

  var START_MARGIN_MS = 30 * 1000; // "Od začátku" and ad-break positions count from 30 s before the start
  var JUMP_INTO_MS = 10 * 1000;
  var NOW_EDGE_MS = 30 * 1000;
  var HOLD_FROM_S = 0.5; // released sooner = a click (±10 s)
  var CLICK_STEP_S = 10;
  var TRICK_SPEEDS = [1, 2, 3, 4, 8, 16]; // rewind / fast-forward speeds (T55; 8 and 16: T62)
  var TRICK_SECONDS = 60; // seconds of programme per second at speed 1, times the speed
  var SEEK_ARRIVED_S = 3; // a fast seek lands on a keyframe up to ~2 s off
  var SEEK_SETTLE_MS = 10000; // both chosen (T55, as Android), not measured on a TV: how long AVPlay takes to land is unknown
  var CT_BUFFER_GONE_MS = 60 * 1000; // T62: the oldest minute of ČT's buffer may already be gone from the CDN
  var CT_LIVE_GUARD_MS = 10 * 1000; // T62: as the players' live-edge guard (and the VOD's end)
  // The gateway's continuous-recording id (server archive.py RECORDING_ID_RE). TVHeadend DVR ids are hex uuids,
  // which can start with "b" too, so the first letter alone is not enough.
  var RECORDING_ID = /^b[A-Za-z0-9_]{1,40}-\d{9,11}-\d{9,11}$/;

  function num(v) {
    return typeof v === 'number' && isFinite(v);
  }

  function bad(what) {
    return new CT.CtError('timeline: ' + what, CT.DvbMessages.GENERIC);
  }

  function pieceEnd(piece) {
    return piece.position + piece.duration;
  }

  function lastPiece(pieces) {
    return pieces[pieces.length - 1];
  }

  /** `H:MM` / `H:MM:SS` of Prague wall clock, hour without a leading zero. */
  function clockText(ms, seconds) {
    var hm = CT.Days.clock(ms);
    if (!seconds) return hm;
    var s = Math.floor(ms / 1000) % 60;
    return hm + ':' + (s < 10 ? '0' : '') + s;
  }

  function optionalId(o, key) {
    var v = o[key];
    if (v === null || v === undefined) return null;
    if (typeof v !== 'string' || !v) throw bad(key);
    return v;
  }

  var Timeline = {
    START_MARGIN_MS: START_MARGIN_MS,
    REFRESH_MS: 60 * 1000,

    /** A continuous-recording programme id (`b<channel>-<start>-<end>`), which plays through the timeline. */
    isTimelineId: function (id) {
      return typeof id === 'string' && RECORDING_ID.test(id);
    },

    /**
     * The JSON of `GET /dvb/timeline/<id>` → {id, channel, playlist, complete, programme: {start, end},
     * pieces: [{position, time, duration}], programmes: [{id, title, subtitle, start, end, breaks}], previous, next}.
     * Unknown fields are ignored; a missing or malformed field the player needs is a CtError, never a crash.
     * Programmes without valid times are dropped (EPG data, like the guide).
     */
    parse: function (status, body) {
      var o = CT.DvbParser.ensureOk(status, body);
      var playlist = J.str(o, 'playlist');
      // Only a path on the gateway: AVPlay sends the key cookie to whatever URL it opens.
      if (!playlist || playlist.charAt(0) !== '/' || playlist.charAt(1) === '/' || playlist.indexOf('\\') >= 0) {
        throw bad('playlist');
      }
      var programme = J.obj(o, 'programme');
      var pStart = J.instant(programme && programme.start);
      var pEnd = J.instant(programme && programme.end);
      if (pStart === null || pEnd === null || pEnd <= pStart) throw bad('programme');
      if (!Array.isArray(o.pieces) || !o.pieces.length) throw bad('pieces');
      var pieces = o.pieces.map(function (p) {
        var time = J.instant(p && p.time);
        if (!p || !num(p.position) || !num(p.duration) || p.duration < 0 || p.position < 0 || time === null) throw bad('piece');
        return { position: p.position, time: time, duration: p.duration };
      });
      for (var i = 1; i < pieces.length; i++) {
        if (pieces[i].position < pieces[i - 1].position || pieces[i].time < pieces[i - 1].time) throw bad('pieces order');
      }
      var programmes = J.arr(o, 'programmes').map(function (p) {
        if (p === null || typeof p !== 'object') return null;
        var start = J.instant(p.start);
        var end = J.instant(p.end);
        if (start === null || end === null || end <= start) return null;
        var id = typeof p.id === 'string' && p.id ? p.id : null;
        return {
          id: id, title: J.str(p, 'title') || '', subtitle: J.str(p, 'subtitle') || '', start: start, end: end,
          breaks: id !== null && p.breaks === true
        };
      }).filter(Boolean).sort(function (a, b) { return a.start - b.start; });
      return {
        id: J.str(o, 'id') || '',
        channel: J.str(o, 'channel') || '',
        playlist: playlist,
        complete: o.complete === true,
        programme: { start: pStart, end: pEnd },
        pieces: pieces,
        programmes: programmes,
        previous: optionalId(o, 'previous'),
        next: optionalId(o, 'next')
      };
    },

    /** Seconds of playlist covered by the pieces. */
    duration: function (pieces) {
      return pieceEnd(lastPiece(pieces));
    },

    /** Wall clock of a position; a boundary belongs to the later piece; outside the window clamps. */
    toTime: function (pieces, position) {
      var end = Timeline.duration(pieces);
      return Timeline.extrapolate(pieces, Math.min(end, Math.max(0, position)));
    },

    /**
     * Like toTime, but past the ends the time runs on with the position (a growing window's live edge, and the
     * target of a seek that leaves the window, which the neighbouring window continues at).
     */
    extrapolate: function (pieces, position) {
      var piece = pieces[0];
      for (var i = 1; i < pieces.length && pieces[i].position <= position; i++) piece = pieces[i];
      var into = position - piece.position;
      if (piece !== lastPiece(pieces)) into = Math.min(into, piece.duration);
      return piece.time + Math.round(into * 1000);
    },

    /** Position of a wall-clock time; in a gap the next piece's start; outside the window clamps. */
    toPosition: function (pieces, time) {
      for (var i = 0; i < pieces.length; i++) {
        var p = pieces[i];
        if (time < p.time) return p.position;
        if (time < p.time + p.duration * 1000) return p.position + (time - p.time) / 1000;
      }
      return Timeline.duration(pieces);
    },

    /** First and last recorded wall clock of the window. */
    span: function (pieces) {
      var last = lastPiece(pieces);
      return { start: pieces[0].time, end: last.time + Math.round(last.duration * 1000) };
    },

    /** What a seek target (seconds) does: 'seek' | 'previous' | 'next' | 'clamp' (stay at the edge). */
    edge: function (target, duration, complete, hasPrevious, hasNext) {
      if (target < 0) return hasPrevious ? 'previous' : 'clamp';
      if (target >= duration) return complete && hasNext ? 'next' : 'clamp';
      return 'seek';
    },

    CLICK_STEP_S: CLICK_STEP_S,
    HOLD_MS: HOLD_FROM_S * 1000,

    /** A press of `held` seconds: 'click' (±10 s) under 0.5 s, else 'hold' (starts rewind / fast-forward). */
    pressKind: function (held) {
      return held < HOLD_FROM_S ? 'click' : 'hold';
    },

    /** Seconds of programme per second at a trick-play speed (`trick.speeds`: 1, 2, 3, 4, 8, 16). */
    trickRate: function (speed) {
      return TRICK_SECONDS * Math.max(1, Math.min(TRICK_SPEEDS[TRICK_SPEEDS.length - 1], speed));
    },

    /** The bar's sign while moving: `◀◀` / `▶▶`, from speed 2 on with the number (`◀◀ 2`). */
    trickBadge: function (direction, speed) {
      return (direction < 0 ? '\u25c0\u25c0' : '\u25b6\u25b6') + (speed > 1 ? ' ' + speed : '');
    },

    /**
     * Rewind / fast-forward (T55, golden `trick`): `state` null = stopped, else {direction: -1|1, speed: 1|2|3|4|8|16};
     * `event` {type: 'click' | 'hold' | 'ok' | 'back', direction} (a click = a press under 0.5 s, a hold = one held
     * from 0.5 s; ⏪/⏩ count as a hold); `barHidden` = the bar was hidden before this event. Returns {state, step
     * (s, a click while stopped; 0 when it only showed the hidden bar), seek: 'play-here' | 'return' | 'none'}.
     * While moving a click or a hold on the same side = the next speed (at most 16), on the other side the one
     * before, and from 1 the other side turns round.
     */
    trick: function (state, event, barHidden) {
      var dir = event.direction < 0 ? -1 : 1;
      if (!state) {
        if (event.type === 'click') return { state: null, step: barHidden ? 0 : dir * CLICK_STEP_S, seek: 'none' };
        if (event.type === 'hold') return { state: { direction: dir, speed: 1 }, step: 0, seek: 'none' };
        return { state: null, step: 0, seek: 'none' };
      }
      if (event.type === 'ok') return { state: null, step: 0, seek: 'play-here' };
      if (event.type === 'back') return { state: null, step: 0, seek: 'return' };
      var next;
      var at = TRICK_SPEEDS.indexOf(state.speed);
      if (dir === state.direction) next = { direction: dir, speed: TRICK_SPEEDS[Math.min(TRICK_SPEEDS.length - 1, at + 1)] };
      else if (at > 0) next = { direction: state.direction, speed: TRICK_SPEEDS[at - 1] };
      else next = { direction: dir, speed: 1 };
      return { state: next, step: 0, seek: 'none' };
    },

    /** Playhead (s) `elapsedMs` after `base` while moving at `speed` in `direction`. */
    trickPosition: function (base, direction, speed, elapsedMs) {
      return base + direction * Timeline.trickRate(speed) * elapsedMs / 1000;
    },

    /**
     * After a seek the bar shows its target (`target`, s) until the player's `position` is within 3 s of it, or
     * 10 s have passed (a seek that landed elsewhere, e.g. a fast seek's keyframe or a refused seek).
     */
    seekSettled: function (target, position, elapsedMs) {
      return Math.abs(position - target) <= SEEK_ARRIVED_S || elapsedMs >= SEEK_SETTLE_MS;
    },

    /** `Dnes 21:14:30`, `Včera 23:59:59`, `čt 22. 10. 10:05:00`: the Prague calendar day compared with now. */
    label: function (time, now) {
      return CT.Days.label(CT.Days.today(time), CT.Days.today(now)) + ' ' + clockText(time, true);
    },

    /**
     * Labels under the bar's ends: `H:MM`, with the day label in front only when the day is not the playhead's;
     * a growing window's end is "Teď" while its last piece ends within 30 s of now.
     */
    edgeLabels: function (start, end, playhead, now, growing) {
      var today = CT.Days.today(now);
      var playDay = CT.Days.today(playhead);
      function text(t) {
        var day = CT.Days.today(t);
        return (day === playDay ? '' : CT.Days.label(day, today) + ' ') + clockText(t, false);
      }
      return { start: text(start), end: growing && now - end <= NOW_EDGE_MS ? 'Teď' : text(end) };
    },

    /** Index of the programme airing at `time` (a boundary belongs to the later one), null in a gap. */
    programmeAt: function (programmes, time) {
      var found = null;
      for (var i = 0; i < programmes.length; i++) {
        if (programmes[i].start <= time && time < programmes[i].end) found = i;
      }
      return found;
    },

    /**
     * ▲ ('up'): more than 10 s into a programme → its start, else the start of the programme before;
     * ▼ ('down'): the next programme's start. null = nothing there.
     */
    jump: function (programmes, time, direction) {
      var i;
      if (direction === 'down') {
        for (i = 0; i < programmes.length; i++) if (programmes[i].start > time) return programmes[i].start;
        return null;
      }
      var at = Timeline.programmeAt(programmes, time);
      if (at !== null && time - programmes[at].start > JUMP_INTO_MS) return programmes[at].start;
      var before = at !== null ? programmes[at].start : time;
      var target = null;
      for (i = 0; i < programmes.length; i++) if (programmes[i].start < before) target = programmes[i].start;
      return target;
    },

    /** "Pokračovat": seconds from the programme's start (before the start = 0). */
    progress: function (programme, time) {
      return Math.max(0, (time - programme.start) / 1000);
    },

    /** Where a window opens: `resumeSeconds` into its programme, else programme start − 30 s. */
    openTime: function (timeline, resumeSeconds) {
      var p = timeline.programme;
      return resumeSeconds ? p.start + resumeSeconds * 1000 : p.start - START_MARGIN_MS;
    },

    /**
     * Ad-break skip for the programme at the playhead (spec/ad-breaks.md positions count from programme start − 30 s):
     * the wall clock to skip to, or null.
     */
    skipTime: function (programme, breaks, time) {
      var origin = programme.start - START_MARGIN_MS;
      var to = CT.AdBreaks.skipTarget(breaks, (time - origin) / 1000);
      return to === null ? null : origin + to * 1000;
    },

    // -- ČT player bar (T62, spec/apps.md "ČT player bar", golden spec/fixtures/ct_bar_cases.json) -----------------

    /**
     * Which bar a resolved target plays with: 'timeline' (the Anténa timeline), 'timeshift' / 'vod' (ČT start-over
     * and archive, T62), or null = the plain OSD (ČT and Anténa live, TVHeadend DVR recordings, the Chamber).
     */
    barMode: function (target) {
      if (!target) return null;
      if (target.kind === 'timeline') return 'timeline';
      if (target.dvb || target.psp) return null;
      return target.kind === 'timeshift' || target.kind === 'vod' ? target.kind : null;
    },

    CT_BUFFER_GONE_MS: CT_BUFFER_GONE_MS,
    CT_LIVE_GUARD_MS: CT_LIVE_GUARD_MS,

    /**
     * The one window of a ČT playback (`window`): 'timeshift' = the buffer `now − 3 h` … `now`, growing (its start
     * slides with now); 'vod' = `programmeStart` … `+ duration` (s), complete; a VOD without a known start has no
     * wall clock (start/end null). Times epoch ms.
     */
    ctWindow: function (kind, now, programmeStart, duration) {
      if (kind === 'timeshift') return { start: now - CT.Decisions.TIMESHIFT_WINDOW_MS, end: now, growing: true };
      if (!num(programmeStart)) return { start: null, end: null, growing: false };
      return { start: programmeStart, end: programmeStart + Math.round(duration * 1000), growing: false };
    },

    /**
     * Where a seek or the moving playhead may go (`clamp`): timeshift targets are wall clocks (ms) inside
     * [now − 3 h + 60 s, now − 10 s] (the oldest minute may be gone from the CDN; 10 s = the live-edge guard), VOD
     * targets positions (s) inside [0, duration − 10]; an unknown duration (0) has no upper limit yet.
     */
    ctClamp: function (kind, now, target, duration) {
      if (kind === 'timeshift') {
        var low = now - CT.Decisions.TIMESHIFT_WINDOW_MS + CT_BUFFER_GONE_MS;
        return Math.max(low, Math.min(now - CT_LIVE_GUARD_MS, target));
      }
      var high = duration > 0 ? Math.max(0, duration - CT_LIVE_GUARD_MS / 1000) : Infinity;
      return Math.max(0, Math.min(high, target));
    },

    /** A VOD without a known start: the playhead's label = the elapsed position `H:MM:SS` (`0:12:34`). */
    elapsedLabel: function (seconds) {
      var s = Math.max(0, Math.floor(seconds));
      var m = Math.floor(s / 60) % 60;
      var sec = s % 60;
      return Math.floor(s / 3600) + ':' + (m < 10 ? '0' : '') + m + ':' + (sec < 10 ? '0' : '') + sec;
    },

    /** …and the bar's ends: `0:00` and the duration `H:MM` (whole minutes, floored). */
    elapsedEdges: function (duration) {
      var minutes = Math.max(0, Math.floor(duration / 60));
      var m = minutes % 60;
      return { start: '0:00', end: Math.floor(minutes / 60) + ':' + (m < 10 ? '0' : '') + m };
    }
  };

  CT.Timeline = Timeline;
})(typeof window !== 'undefined' ? window : global);
