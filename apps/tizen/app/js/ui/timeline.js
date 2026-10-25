/*
 * The "Anténa" timeline player's own bar and keys (spec/apps.md "Anténa timeline player"), driven by the Player:
 * the bar shows the window in wall clock (recorded pieces, gaps, programme ticks, the programme at the playhead,
 * the played part), the title follows the programme at the playhead, ◀/▶ seek and scrub when held, ▲/▼ jump
 * between programmes, a seek past the window's ends opens the neighbouring window at the same wall clock.
 * The rules themselves are in core/timeline.js.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var T = CT.Timeline;

  var BAR_MS = 5000;
  var LONG_OK_MS = 800;
  // A key without its keyup (not every firmware sends one) counts as released this long after its last keydown.
  var RELEASE_FALLBACK_MS = 800;
  var TICK_MS = 500;
  var BREAKS_REFRESH_MS = 60 * 1000;
  var END_SLACK_S = 1;
  var JUMP_SAME_MS = 10 * 1000; // ▲ within this of the playhead counts as not moving
  var DIRECTION = { left: -1, right: 1, rewind: -1, forward: 1 };
  // ⏪/⏩ act as a held ◀/▶: a tap already moves a minute (as before T05), holding speeds up like the arrows.
  var MIN_HELD = { left: 0, right: 0, rewind: 1, forward: 1 };

  function $(id) {
    return document.getElementById(id);
  }

  function div(className, leftPct, widthPct) {
    var el = document.createElement('div');
    el.className = className;
    el.style.left = leftPct + '%';
    if (widthPct !== undefined) el.style.width = widthPct + '%';
    return el;
  }

  /** `player` = the CT.Player that owns the backend, the OSD and the "Přeskočit reklamu" button. */
  function TimelineController(player) {
    var self = this;
    this.player = player;
    this.tl = null;
    this.hold = null; // {key, dir, startedAt, base, timer}
    this.ok = null; // {at, long, timer}
    this.swallow = null; // {key, at}: the press that only showed the bar (its repeats and release are ignored)
    this.scrub = null; // position (s) being chosen, shown before the seek
    this.switching = null; // wall clock the next window will continue at
    this.switchingId = null;
    this.ended = false;
    this.breaks = {}; // programme id → {at, list}
    this.refreshedAt = 0;
    this.audioList = null; // {tracks, focus}
    this.timer = setInterval(function () { self.tick(); }, TICK_MS);
  }

  TimelineController.prototype.stop = function () {
    clearInterval(this.timer);
    this.cancelKeys();
    this.closeAudioList();
    $('osd').classList.remove('timeline');
  };

  TimelineController.prototype.cancelKeys = function () {
    if (this.hold) clearTimeout(this.hold.timer);
    if (this.ok) clearTimeout(this.ok.timer);
    this.hold = null;
    this.ok = null;
    this.scrub = null;
  };

  /** A (new) window started playing. */
  TimelineController.prototype.setWindow = function (timeline) {
    var changed = !this.tl || this.tl.id !== timeline.id;
    this.tl = timeline;
    this.switching = null;
    this.switchingId = null;
    this.ended = false;
    this.refreshedAt = Date.now();
    if (changed) this.breaks = {};
    $('osd').classList.add('timeline');
    this.buildBar();
    this.render();
  };

  TimelineController.prototype.backend = function () {
    return this.player.backend;
  };

  /** Seconds of the window; a growing one also as far as the player already sees. */
  TimelineController.prototype.duration = function () {
    var d = T.duration(this.tl.pieces);
    if (!this.tl.complete) d = Math.max(d, this.backend().duration() / 1000);
    return d;
  };

  TimelineController.prototype.position = function () {
    return this.backend().position() / 1000;
  };

  TimelineController.prototype.timeOf = function (position) {
    return this.tl.complete ? T.toTime(this.tl.pieces, position) : T.extrapolate(this.tl.pieces, Math.max(0, position));
  };

  /** Wall clock of the playhead: the seek being chosen, the next window's start, or what plays. */
  TimelineController.prototype.playheadTime = function () {
    if (this.switching !== null) return this.switching;
    return this.scrub !== null ? this.targetTime(this.scrub) : this.timeOf(this.position());
  };

  /** Wall clock a seek target leads to: past an end with a neighbouring window it runs on, else the edge. */
  TimelineController.prototype.targetTime = function (target) {
    var tl = this.tl;
    var where = T.edge(target, this.duration(), tl.complete, !!tl.previous, !!tl.next);
    if (where === 'previous' || where === 'next') return T.extrapolate(tl.pieces, target);
    return this.timeOf(Math.min(this.duration(), Math.max(0, target)));
  };

  /** Wall clock of what actually plays (progress, ad breaks), null while switching windows. */
  TimelineController.prototype.playingTime = function () {
    if (!this.tl || this.switching !== null) return null;
    return this.timeOf(this.position());
  };

  TimelineController.prototype.programmeAt = function (time) {
    var i = T.programmeAt(this.tl.programmes, time);
    return i === null ? null : this.tl.programmes[i];
  };

  // -- keys ---------------------------------------------------------------------------------------------------

  /** Keydown of the timeline player (a held key repeats its keydown until the keyup). */
  TimelineController.prototype.onKeyDown = function (key) {
    if (!this.tl) return;
    if (this.audioList) {
      this.onAudioListKey(key);
      return;
    }
    var now = Date.now();
    if (this.swallow && this.swallow.key === key && now - this.swallow.at < RELEASE_FALLBACK_MS) {
      this.swallow.at = now; // still the press that only showed the bar
      return;
    }
    this.swallow = null;
    var visible = $('osd').classList.contains('visible');
    var always = key === 'playpause' || key === 'play' || key === 'pause' || key === 'back' || key === 'stop' ||
      key === 'audio' || (key === 'enter' && this.player.skipTo !== null);
    if (!visible && !always && !this.hold && !this.ok) {
      this.swallow = { key: key, at: now };
      this.showBar();
      return;
    }
    switch (key) {
      case 'back':
      case 'stop':
        this.player.close();
        return;
      case 'left':
      case 'right':
      case 'rewind':
      case 'forward':
        this.holdDown(key);
        break;
      case 'up':
      case 'down':
        this.jump(key);
        break;
      case 'enter':
        if (this.player.skipTo !== null && !this.ok) {
          this.player.skipAd();
          break;
        }
        this.okDown();
        break;
      case 'playpause':
      case 'play':
      case 'pause':
        this.togglePause();
        break;
      case 'audio':
        this.player.nextAudio();
        break;
      default:
        break; // no zapping, no number entry in this player
    }
    this.showBar();
  };

  TimelineController.prototype.onKeyUp = function (key) {
    if (this.swallow && this.swallow.key === key) {
      this.swallow = null;
      return;
    }
    if (this.hold && this.hold.key === key) this.holdUp(Date.now());
    if (key === 'enter' && this.ok) this.okUp();
  };

  TimelineController.prototype.holdDown = function (key) {
    var self = this;
    var now = Date.now();
    if (this.hold && this.hold.key !== key) this.holdUp(this.hold.lastAt);
    if (!this.hold) {
      this.hold = { key: key, dir: DIRECTION[key], startedAt: now, lastAt: now, base: this.position(), timer: null };
    }
    var h = this.hold;
    h.lastAt = now;
    this.scrub = h.base + h.dir * T.holdOffset(this.heldSeconds(h, now));
    clearTimeout(h.timer);
    // no keyup came: the key was released around its last keydown
    h.timer = setTimeout(function () { if (self.hold === h) self.holdUp(h.lastAt); }, RELEASE_FALLBACK_MS);
    this.render();
  };

  TimelineController.prototype.heldSeconds = function (h, now) {
    return Math.max(MIN_HELD[h.key], (now - h.startedAt) / 1000);
  };

  /** Release of ◀/▶/⏪/⏩ (at `releasedAt`, default now): the seek happens now. */
  TimelineController.prototype.holdUp = function (releasedAt) {
    var h = this.hold;
    if (!h) return;
    clearTimeout(h.timer);
    this.hold = null;
    var target = h.base + h.dir * T.holdOffset(this.heldSeconds(h, releasedAt || Date.now()));
    this.scrub = null;
    this.seekTo(target, this.targetTime(target));
  };

  TimelineController.prototype.okDown = function () {
    var self = this;
    var now = Date.now();
    if (!this.ok) this.ok = { at: now, long: false, timer: null };
    var ok = this.ok;
    if (!ok.long && now - ok.at >= LONG_OK_MS) {
      ok.long = true;
      this.openAudioList();
    }
    clearTimeout(ok.timer);
    ok.timer = setTimeout(function () { if (self.ok === ok) self.okUp(); }, RELEASE_FALLBACK_MS);
  };

  TimelineController.prototype.okUp = function () {
    var ok = this.ok;
    clearTimeout(ok.timer);
    this.ok = null;
    if (!ok.long) this.togglePause();
  };

  TimelineController.prototype.togglePause = function () {
    if (this.ended) {
      this.reopen(this.position());
      return;
    }
    this.backend().togglePause();
    this.render();
  };

  /** ▲/▼: start of this programme or the one before / of the next one. */
  TimelineController.prototype.jump = function (key) {
    var tl = this.tl;
    var from = this.playheadTime();
    var time = T.jump(tl.programmes, from, key === 'up' ? 'up' : 'down');
    // ▲ to a start that is not recorded (in a gap) lands where the recording resumes; pressed again there it
    // would land on the same spot, so it goes on to the programme before.
    var here = T.toPosition(tl.pieces, from);
    for (var n = 0; key === 'up' && time !== null && n < tl.programmes.length; n++) {
      if (time < T.span(tl.pieces).start || Math.abs(T.toPosition(tl.pieces, time) - here) * 1000 > JUMP_SAME_MS) break;
      time = T.jump(tl.programmes, time, 'up');
    }
    if (time !== null) this.seekToTime(time);
  };

  /** A wall-clock target: through the map inside the window, through the neighbouring window outside it. */
  TimelineController.prototype.seekToTime = function (time) {
    var span = T.span(this.tl.pieces);
    var position;
    if (time < span.start) position = -1;
    else if (time >= span.end && this.tl.complete) position = this.duration();
    else position = T.toPosition(this.tl.pieces, time);
    this.seekTo(position, time);
  };

  /** Seek to `target` (s), or open `previous`/`next` continuing at the wall clock `time` (spec "Edges"). */
  TimelineController.prototype.seekTo = function (target, time) {
    if (this.switching !== null) return; // one switch at a time
    var tl = this.tl;
    var duration = this.duration();
    switch (T.edge(target, duration, tl.complete, !!tl.previous, !!tl.next)) {
      case 'previous':
        this.openWindow(tl.previous, time);
        return;
      case 'next':
        this.openWindow(tl.next, time);
        return;
      case 'clamp':
        target = target < 0 ? 0 : duration;
        break;
      default:
        break;
    }
    if (this.ended) {
      this.reopen(target);
      return;
    }
    this.backend().seekTo(target * 1000); // the backend keeps a guard before a growing end
    this.player.setSkip(null);
    this.render();
  };

  /** After the stream completed: play again from `position` in the same window. */
  TimelineController.prototype.reopen = function (position) {
    this.ended = false;
    this.player.reopen(Math.min(position, this.duration() - END_SLACK_S) * 1000);
  };

  TimelineController.prototype.openWindow = function (id, time) {
    var self = this;
    var player = this.player;
    var token = player.loadToken;
    this.switching = time;
    this.switchingId = id;
    this.render();
    player.app.planner.timelineWindow(id, player.request.title, time).then(function (target) {
      if (token !== player.loadToken || player.tl !== self) return;
      player.switchTarget(target);
    }, function (e) {
      if (token !== player.loadToken || player.tl !== self) return;
      self.switching = null;
      self.switchingId = null;
      $('osd-state').textContent = e && e.userMessage ? e.userMessage : CT.DvbMessages.GENERIC;
      self.render();
    });
  };

  /** Playback reached the end of the window: the next one, if any, else stay at the end. */
  TimelineController.prototype.onEnded = function () {
    if (this.switching !== null) return;
    var tl = this.tl;
    var end = T.duration(tl.pieces);
    if (tl.complete && tl.next) {
      this.openWindow(tl.next, T.extrapolate(tl.pieces, end));
      return;
    }
    this.ended = true;
    this.showBar();
  };

  // -- audio list (long OK) ------------------------------------------------------------------------------------

  TimelineController.prototype.openAudioList = function () {
    var tracks = this.backend().audioTracks();
    if (tracks.length < 2) {
      $('osd-state').textContent = 'Jen jedna zvuková stopa';
      return;
    }
    var current = this.backend().currentAudio();
    var focus = 0;
    tracks.forEach(function (t, i) { if (t.index === current) focus = i; });
    this.audioList = { tracks: tracks, focus: focus, current: current };
    this.renderAudioList();
    $('audio-list').classList.add('visible');
  };

  TimelineController.prototype.closeAudioList = function () {
    this.audioList = null;
    $('audio-list').classList.remove('visible');
  };

  TimelineController.prototype.renderAudioList = function () {
    var list = this.audioList;
    var box = $('audio-list-items');
    box.innerHTML = '';
    list.tracks.forEach(function (t, i) {
      var el = document.createElement('div');
      el.className = 'audio-item' + (i === list.focus ? ' focused' : '') + (t.index === list.current ? ' current' : '');
      el.textContent = CT.AudioTracks.label(t, i);
      box.appendChild(el);
    });
  };

  TimelineController.prototype.onAudioListKey = function (key) {
    var list = this.audioList;
    switch (key) {
      case 'up':
        list.focus = Math.max(0, list.focus - 1);
        break;
      case 'down':
        list.focus = Math.min(list.tracks.length - 1, list.focus + 1);
        break;
      case 'enter':
        if (this.ok) return; // still the long press that opened the list
        if (this.backend().selectAudio(list.tracks[list.focus].index)) {
          $('osd-state').textContent = CT.AudioTracks.osd(list.tracks, list.focus);
        }
        this.closeAudioList();
        return;
      case 'back':
      case 'stop':
        this.closeAudioList();
        return;
      default:
        return;
    }
    this.renderAudioList();
  };

  // -- bar -----------------------------------------------------------------------------------------------------

  TimelineController.prototype.showBar = function () {
    var self = this;
    $('osd').classList.add('visible');
    this.render();
    clearTimeout(this.player.osdTimer);
    this.player.osdTimer = setTimeout(function () { self.hideBarLater(); }, BAR_MS);
  };

  /** The bar stays while paused, while a seek is chosen and while the next window opens. */
  TimelineController.prototype.hideBarLater = function () {
    var self = this;
    if (this.backend().paused() || this.hold || this.switching !== null || this.ended || this.audioList) {
      this.player.osdTimer = setTimeout(function () { self.hideBarLater(); }, 1000);
      return;
    }
    $('osd').classList.remove('visible');
  };

  /** The bar's time axis: the window's recorded span, a growing one up to the playhead. */
  TimelineController.prototype.axis = function (playhead) {
    var span = T.span(this.tl.pieces);
    return { start: span.start, end: Math.max(span.end, Math.min(playhead, Date.now())), span: span };
  };

  /** Pieces and programme ticks; rebuilt when the window (or a growing window's JSON) changes. */
  TimelineController.prototype.buildBar = function () {
    var track = $('tl-pieces');
    track.innerHTML = '';
    var ticks = $('tl-ticks');
    ticks.innerHTML = '';
    var a = this.axis(this.playheadTime());
    this.builtAxisEnd = a.end;
    var pct = function (t) { return Math.max(0, Math.min(100, (t - a.start) / (a.end - a.start || 1) * 100)); };
    this.tl.pieces.forEach(function (p) {
      var from = pct(p.time);
      track.appendChild(div('tl-piece', from, pct(p.time + p.duration * 1000) - from));
    });
    this.tl.programmes.forEach(function (p) {
      if (p.start > a.start && p.start < a.end) ticks.appendChild(div('tl-tick', pct(p.start)));
    });
  };

  TimelineController.prototype.render = function () {
    if (!this.tl) return;
    var player = this.player;
    var playhead = this.playheadTime();
    var a = this.axis(playhead);
    if (a.end - this.builtAxisEnd > (a.end - a.start) / 200) this.buildBar(); // a growing window moves its scale
    var pct = function (t) { return Math.max(0, Math.min(100, (t - a.start) / (a.end - a.start || 1) * 100)); };
    var now = Date.now();
    var programme = this.programmeAt(playhead);
    var channel = player.channelName();
    var t = programme ? CT.PlayerTitle.programme(programme.title, channel, programme.subtitle) : { title: channel, subtitle: '' };
    if ($('osd-title').textContent !== t.title) $('osd-title').textContent = t.title;
    if ($('osd-subtitle').textContent !== t.subtitle) $('osd-subtitle').textContent = t.subtitle;

    var at = pct(playhead);
    $('tl-played').style.width = at + '%';
    $('tl-head').style.left = at + '%';
    var label = $('tl-label');
    label.textContent = T.label(playhead, now);
    // keep the label on screen at the bar's ends
    label.style.left = at + '%';
    label.style.transform = 'translateX(' + (at < 8 ? '0' : at > 92 ? '-100%' : '-50%') + ')';
    var highlight = $('tl-programme');
    if (programme) {
      var from = pct(programme.start);
      highlight.style.left = from + '%';
      highlight.style.width = (pct(programme.end) - from) + '%';
      highlight.style.display = 'block';
    } else {
      highlight.style.display = 'none';
    }
    var ends = T.edgeLabels(a.span.start, a.span.end, playhead, now, !this.tl.complete);
    $('tl-start').textContent = ends.start;
    $('tl-end').textContent = ends.end;
    var text = this.switching !== null ? 'Načítání…' : this.ended ? 'Konec nahrávky' : this.backend().paused() ? 'Pozastaveno' : '';
    var state = $('osd-state');
    // clears only its own text, not "Zvuk: …" or the player's buffering note
    if (text) state.textContent = text;
    else if (this.stateText && state.textContent === this.stateText) state.textContent = '';
    this.stateText = text;
  };

  // -- every half second: end of window, growing JSON, ad breaks --------------------------------------------

  TimelineController.prototype.tick = function () {
    var player = this.player;
    if (!this.tl || !player.isOpen() || !player.target || player.target.kind !== 'timeline') return;
    var tl = this.tl;
    var position = this.position();
    if (tl.complete && tl.next && !this.ended && this.switching === null && position >= T.duration(tl.pieces) - END_SLACK_S) {
      this.onEnded();
      return;
    }
    if (!tl.complete && Date.now() - this.refreshedAt >= T.REFRESH_MS) this.refresh();
    this.updateSkip();
    if ($('osd').classList.contains('visible')) this.render();
  };

  /** A growing window: the JSON again every minute (new pieces, programmes, `complete`, `next`). */
  TimelineController.prototype.refresh = function () {
    var self = this;
    var tl = this.tl;
    var dvb = this.player.target.dvb;
    this.refreshedAt = Date.now();
    dvb.timeline(tl.id).then(function (fresh) {
      if (self.tl !== tl || self.switching !== null) return;
      self.tl = fresh;
      self.buildBar();
      self.render();
    }, function () {});
  };

  /** "Přeskočit reklamu" for the programme at the playhead (ended, with an id and stored breaks). */
  TimelineController.prototype.updateSkip = function () {
    var self = this;
    var time = this.playingTime();
    var programme = time === null ? null : this.programmeAt(time);
    // a programme still airing is a growing recording: no button in v1 (spec/ad-breaks.md)
    if (!programme || !programme.id || !programme.breaks || programme.end > Date.now()) {
      this.player.setSkip(null);
      return;
    }
    var cached = this.breaks[programme.id];
    if (!cached || Date.now() - cached.at >= BREAKS_REFRESH_MS) {
      var entry = this.breaks[programme.id] = { at: Date.now(), list: cached ? cached.list : [] };
      this.player.target.dvb.breaks(programme.id).then(function (list) { entry.list = list; }, function () {});
      cached = entry;
    }
    var to = T.skipTime(programme, cached.list, time);
    this.player.setSkip(to === null ? null : T.toPosition(this.tl.pieces, to));
  };

  /** "Pokračovat": the programme at the playhead (with an id), seconds from its start. */
  /**
   * "Pokračovat" of the programme at the playhead; the Archiv row gets the request's title and image when that is
   * the programme that was opened, else the neighbour's own title and thumbnail path.
   */
  TimelineController.prototype.saveProgress = function (store, request) {
    var time = this.playingTime();
    var programme = time === null ? null : this.programmeAt(time);
    if (!programme || !programme.id) return;
    var meta = request && request.recordingId === programme.id && request.resumeMeta ? request.resumeMeta
      : CT.ResumeRules.meta(programme.title, programme.subtitle, CT.Thumbs.path(programme.id), 'dvb');
    store.save('dvb:' + programme.id, T.progress(programme, time), (programme.end - programme.start) / 1000, meta);
  };

  /** Where to continue after re-resolving or coming back from the background: this window, same wall clock. */
  TimelineController.prototype.continueAt = function () {
    if (!this.tl) return null;
    if (this.switching !== null) return { id: this.switchingId, time: this.switching };
    return { id: this.tl.id, time: this.timeOf(this.position()) };
  };

  CT.TimelineController = TimelineController;
})(window);
