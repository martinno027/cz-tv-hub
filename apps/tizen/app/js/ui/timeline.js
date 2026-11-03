/*
 * The "Anténa" timeline player's own bar and keys (spec/apps.md "Anténa timeline player"), driven by the Player:
 * the bar shows the window in wall clock (recorded pieces, gaps, programme ticks, the programme at the playhead,
 * the played part), the title follows the programme at the playhead, ◀/▶ seek 10 s and, held, rewind / fast-forward
 * at speeds 1–4 (T55), ▲/▼ jump
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
  // ⏪/⏩: every press acts at once as a hold of ◀/▶ (start at speed 1, else speed ± 1), spec T55.
  var MEDIA = { rewind: true, forward: true };
  var PREVIEW_MS = 1000; // while moving, the picture follows the playhead at most this often (where it can)
  var PAUSE_CHECK_MS = 250; // while moving, how often the video is made sure to be paused

  /** The next animation frame (Chromium 56 has it), else ~60 fps by timer. */
  function nextFrame(fn) {
    if (root.requestAnimationFrame) return { raf: root.requestAnimationFrame(fn) };
    return { timer: setTimeout(fn, 16) };
  }

  function cancelFrame(handle) {
    if (!handle) return;
    if (handle.raf !== undefined && root.cancelAnimationFrame) root.cancelAnimationFrame(handle.raf);
    if (handle.timer !== undefined) clearTimeout(handle.timer);
  }

  /** Writes only a changed text (render runs every frame while rewinding / fast-forwarding). */
  function setText(el, text) {
    if (el.textContent !== text) el.textContent = text;
  }

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
    this.press = null; // {key, dir, startedAt, lastAt, acted, timer, holdTimer}: ◀/▶/⏪/⏩ down
    this.trick = null; // {direction, speed, base (s), since, from: {id, position, time}, frame, previewAt}: moving
    this.keyupSeen = false; // this remote sends keyups (only then a press still down after 0.5 s is a hold by time)
    this.pending = null; // {position (s), at}: a seek the player is not at yet; the bar shows its target meanwhile
    this.ok = null; // {at, long, timer}
    this.swallow = null; // {key, at = its last keydown}: the press that only showed the bar or stopped the movement (its repeats and release are ignored; the same key 0.8 s later is a new press)
    this.scrub = null; // position (s) being dragged on the web bar (web-input.js), shown before the seek
    this.switching = null; // wall clock the next window will continue at
    this.switchingId = null;
    this.ended = false;
    this.breaks = {}; // programme id → {at, list}
    this.refreshedAt = 0;
    this.audioList = null; // {tracks, items, note, focus} (CT.Mute.choice)
    this.timer = setInterval(function () { self.tick(); }, TICK_MS);
  }

  TimelineController.prototype.stop = function () {
    clearInterval(this.timer);
    this.cancelKeys();
    this.closeAudioList();
    $('osd').classList.remove('timeline');
  };

  TimelineController.prototype.cancelKeys = function () {
    this.endPress();
    this.endTrick();
    if (this.ok) clearTimeout(this.ok.timer);
    this.ok = null;
    this.scrub = null;
    this.pending = null;
  };

  /** A (new) window started playing. */
  TimelineController.prototype.setWindow = function (timeline) {
    var changed = !this.tl || this.tl.id !== timeline.id;
    var tr = this.trick;
    // moving across a window's end goes on in the neighbouring window from the time it opened at; any other
    // (re)opening ends the movement
    if (tr && this.switching !== null) {
      tr.base = T.toPosition(timeline.pieces, this.switching);
      tr.since = Date.now();
    } else if (tr) {
      this.endTrick();
    }
    this.tl = timeline;
    this.switching = null;
    this.switchingId = null;
    this.ended = false;
    this.pending = null;
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

  /** The position the bar shows: a seek's target until the player is there (T55: no jump back), else what plays. */
  TimelineController.prototype.shownPosition = function () {
    var position = this.position();
    var p = this.pending;
    if (p && T.seekSettled(p.position, position, Date.now() - p.at)) this.pending = p = null;
    return p ? p.position : position;
  };

  TimelineController.prototype.timeOf = function (position) {
    return this.tl.complete ? T.toTime(this.tl.pieces, position) : T.extrapolate(this.tl.pieces, Math.max(0, position));
  };

  /** Wall clock of the playhead: the next window's start, a drag (web), the rewind / fast-forward, or what plays. */
  TimelineController.prototype.playheadTime = function () {
    if (this.switching !== null) return this.switching;
    if (this.scrub !== null) return this.targetTime(this.scrub);
    if (this.trick) return this.targetTime(this.trickAt(Date.now()));
    return this.timeOf(this.shownPosition());
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
      this.swallow.at = now; // still the press that only showed the bar (or stopped the movement)
      return;
    }
    this.swallow = null;
    if (this.trick && this.trickKey(key, now)) {
      this.showBar();
      return;
    }
    var visible = $('osd').classList.contains('visible');
    var always = key === 'playpause' || key === 'play' || key === 'pause' || key === 'back' || key === 'stop' ||
      key === 'audio' || key === 'mute' || key === 'rewind' || key === 'forward' ||
      (key === 'enter' && this.player.skipTo !== null);
    if (!visible && !always && !this.press && !this.ok) {
      this.showBar();
      // ◀/▶ with the bar hidden: a click only shows it, a hold starts moving at once (spec T55)
      if (key === 'left' || key === 'right') this.arrowDown(key, now, true);
      else this.swallow = { key: key, at: now }; // this press (key + the time of its last keydown) only showed it
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
        this.arrowDown(key, now);
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
        this.openAudioList(); // the "Zvuk" choice with the mute item (spec/apps.md "Mute (T34)")
        break;
      case 'mute':
        this.player.toggleMute();
        break;
      default:
        break; // no zapping, no number entry in this player
    }
    this.showBar();
  };

  /**
   * A key while moving (T55): OK / play-pause = stop and play here, Back = stop and return, ▲/▼ = stop and jump;
   * true when the key was used here (the arrows go on to arrowDown).
   */
  TimelineController.prototype.trickKey = function (key, now) {
    switch (key) {
      case 'enter':
      case 'playpause':
      case 'play':
      case 'pause':
        this.swallow = { key: key, at: now }; // its repeats and keyup are not a long OK / another pause
        this.trickEvent('ok');
        return true;
      case 'back':
      case 'stop':
        this.swallow = { key: key, at: now };
        this.trickEvent('back');
        return true;
      case 'up':
      case 'down':
        var here = this.trickAt(now);
        this.endTrick();
        if (!this.jump(key, true)) this.finishAt(here); // nothing to jump to: plays where it stands
        return true;
      default:
        return false;
    }
  };

  TimelineController.prototype.onKeyUp = function (key) {
    if (this.swallow && this.swallow.key === key) {
      this.swallow = null;
      this.keyupSeen = true;
      return;
    }
    this.keyupSeen = true;
    if (this.press && this.press.key === key) this.releasePress();
    if (key === 'enter' && this.ok) this.okUp();
  };

  /**
   * ◀/▶/⏪/⏩ down (also every repeat of a held key); `quiet` = it showed the hidden bar, so a click of it does
   * nothing more. Stopped: a release under 0.5 s is a click (±10 s), a press
   * still down at 0.5 s (by time where this remote sends keyups, else by its repeats lasting 0.5 s) starts moving.
   * Moving, and ⏪/⏩ always: the press acts at once, its repeats add nothing. Without a keyup the press ends 0.8 s
   * after its last repeat.
   */
  TimelineController.prototype.arrowDown = function (key, now, quiet) {
    var self = this;
    var p = this.press;
    if (p && p.key === key) {
      p.lastAt = now;
      if (!p.acted && now - p.startedAt >= T.HOLD_MS) this.pressHeld(p);
    } else {
      if (p) this.releasePress(); // another key came: that press is over (its keyup lost)
      // quiet = the press that showed the bar: as a click it did that, as a hold it moves
      p = this.press = { key: key, dir: DIRECTION[key], startedAt: now, lastAt: now, acted: false, quiet: !!quiet,
        timer: null, holdTimer: null };
      if (MEDIA[key] || this.trick) {
        p.acted = true;
        this.trickEvent(MEDIA[key] ? 'hold' : 'click', p.dir);
      } else if (this.keyupSeen) {
        p.holdTimer = setTimeout(function () { if (self.press === p && !p.acted) self.pressHeld(p); }, T.HOLD_MS);
      }
    }
    clearTimeout(p.timer);
    p.timer = setTimeout(function () { if (self.press === p) self.releasePress(); }, RELEASE_FALLBACK_MS);
  };

  TimelineController.prototype.pressHeld = function (p) {
    p.acted = true;
    this.trickEvent('hold', p.dir);
  };

  /** The press is over: one that did nothing yet was a click. */
  TimelineController.prototype.releasePress = function () {
    var p = this.endPress();
    if (p && !p.acted) this.trickEvent('click', p.dir, p.quiet);
  };

  TimelineController.prototype.endPress = function () {
    var p = this.press;
    if (!p) return null;
    clearTimeout(p.timer);
    clearTimeout(p.holdTimer);
    this.press = null;
    return p;
  };

  /**
   * One event of rewind / fast-forward (T.trick): a 10 s step, a new speed or direction, or the stop; `barHidden` =
   * the press showed the hidden bar.
   */
  TimelineController.prototype.trickEvent = function (type, direction, barHidden) {
    var tr = this.trick;
    var r = T.trick(tr ? { direction: tr.direction, speed: tr.speed } : null, { type: type, direction: direction || 1 },
      !!barHidden);
    if (r.step) {
      var target = this.shownPosition() + r.step;
      this.seekTo(target, this.targetTime(target));
    } else if (r.state) {
      this.move(r.state);
    } else if (r.seek === 'play-here') {
      this.finishAt(this.trickAt(Date.now()));
    } else if (r.seek === 'return') {
      var from = tr.from;
      this.endTrick();
      // while a neighbouring window opens: it plays where it opened (the seek waits for no switch); after crossing
      // into another window: the start window again, at the start position
      if (this.switching !== null || from.id === this.tl.id) this.seekTo(from.position, from.time, true);
      else this.openWindow(from.id, from.time);
    }
  };

  /** Starts moving, or goes on from where the playhead is now at another speed or the other way. */
  TimelineController.prototype.move = function (state) {
    var self = this;
    var now = Date.now();
    var tr = this.trick;
    if (!tr) {
      var position = this.shownPosition();
      tr = this.trick = { base: position, since: now, frame: null, previewAt: now, pausedAt: now,
        from: { id: this.tl.id, position: position, time: this.timeOf(position) } };
      this.pending = null;
      this.pauseVideo();
      tr.frame = nextFrame(function () { self.frame(tr); });
    } else {
      tr.base = this.trickAt(now);
      tr.since = now;
    }
    tr.direction = state.direction;
    tr.speed = state.speed;
    this.render();
  };

  /** Playhead (s) of the movement at `now`; it waits while the next window opens. */
  TimelineController.prototype.trickAt = function (now) {
    var tr = this.trick;
    if (this.switching !== null) return tr.base;
    return T.trickPosition(tr.base, tr.direction, tr.speed, now - tr.since);
  };

  /**
   * Every frame while moving: the playhead and clock move (smooth, not with key repeats), the video stays paused and
   * its picture follows at most once a second where the backend can; an end of the window opens the neighbour
   * (and goes on there) or stops there.
   */
  TimelineController.prototype.frame = function (tr) {
    var self = this;
    if (this.trick !== tr) return;
    var now = Date.now();
    if (this.switching !== null) {
      tr.since = now; // waits at the switch: base is rebased in setWindow
    } else {
      var position = this.trickAt(now);
      var tl = this.tl;
      var where = T.edge(position, this.duration(), tl.complete, !!tl.previous, !!tl.next);
      if (where === 'previous' || where === 'next') {
        tr.base = position;
        this.openWindow(where === 'previous' ? tl.previous : tl.next, T.extrapolate(tl.pieces, position));
      } else if (where === 'clamp') {
        this.finishAt(position < 0 ? 0 : this.duration());
        return;
      } else {
        if (now - tr.pausedAt >= PAUSE_CHECK_MS) {
          tr.pausedAt = now; // a newly opened window starts playing: paused again (not every frame: AVPlay calls)
          this.pauseVideo();
        }
        var b = this.backend();
        if (b.preview && now - tr.previewAt >= PREVIEW_MS) {
          tr.previewAt = now;
          b.preview(position * 1000);
        }
      }
    }
    this.render();
    tr.frame = nextFrame(function () { self.frame(tr); });
  };

  /** Stops moving without seeking. */
  TimelineController.prototype.endTrick = function () {
    var tr = this.trick;
    if (!tr) return;
    cancelFrame(tr.frame);
    this.trick = null;
  };

  /** Stops moving and plays from `position` (s): one exact seek; `inside` = in this window, not past its edges. */
  TimelineController.prototype.finishAt = function (position, inside) {
    this.endTrick();
    this.seekTo(position, this.targetTime(position), true, inside);
  };

  TimelineController.prototype.pauseVideo = function () {
    var b = this.backend();
    if (b.pause) b.pause();
    else if (!b.paused()) b.togglePause();
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

  /** ▲/▼: start of this programme or the one before / of the next one; false when there is none. */
  TimelineController.prototype.jump = function (key, play) {
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
    if (time === null) return false;
    this.seekToTime(time, play);
    return true;
  };

  /** A wall-clock target: through the map inside the window, through the neighbouring window outside it. */
  TimelineController.prototype.seekToTime = function (time, play) {
    var span = T.span(this.tl.pieces);
    var position;
    if (time < span.start) position = -1;
    else if (time >= span.end && this.tl.complete) position = this.duration();
    else position = T.toPosition(this.tl.pieces, time);
    this.seekTo(position, time, play);
  };

  /**
   * Seek to `target` (s), or open `previous`/`next` continuing at the wall clock `time` (spec "Edges"); always
   * exact. `play` = the video was paused by rewind / fast-forward and plays from the target; a seek during the
   * movement (the web's drag on the bar) ends it the same way.
   */
  TimelineController.prototype.seekTo = function (target, time, play, inside) {
    if (this.trick) {
      this.endTrick();
      play = true;
    }
    if (this.switching !== null) return; // one switch at a time
    var tl = this.tl;
    var duration = this.duration();
    switch (inside ? 'clamp' : T.edge(target, duration, tl.complete, !!tl.previous, !!tl.next)) {
      case 'previous':
        this.openWindow(tl.previous, time);
        return;
      case 'next':
        this.openWindow(tl.next, time);
        return;
      case 'clamp':
        target = Math.max(0, Math.min(duration, target));
        break;
      default:
        break;
    }
    if (this.ended) {
      this.reopen(target);
      return;
    }
    // the backend keeps a guard before a growing end and says where it seeks to
    var backend = this.backend();
    var landed = backend.seekTo(target * 1000);
    if (play && backend.paused()) backend.togglePause(); // paused by the movement: plays from the target
    this.pending = { position: typeof landed === 'number' && isFinite(landed) ? landed / 1000 : target, at: Date.now() };
    this.player.setSkip(null);
    this.render();
  };

  /** After the stream completed: play again from `position` in the same window. */
  TimelineController.prototype.reopen = function (position) {
    this.ended = false;
    this.pending = null;
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
      // moving into a window that did not open: it stops inside this window at that edge and plays there (not
      // through the edge rule again, which would ask for the same window at once)
      if (self.trick) self.finishAt(self.trick.base < 0 ? 0 : self.duration() - END_SLACK_S, true);
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

  /** The "Zvuk" choice: the tracks (several only) and „Vypnout zvuk"/„Zapnout zvuk" (spec/apps.md "Mute (T34)"). */
  TimelineController.prototype.openAudioList = function () {
    var tracks = this.backend().audioTracks();
    var current = this.backend().currentAudio();
    var choice = CT.Mute.choice(tracks, current, this.player.mute.muted);
    this.audioList = { tracks: tracks, items: choice.items, note: choice.note, focus: choice.focus };
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
    var note = $('audio-list-note');
    note.textContent = list.note;
    note.style.display = list.note ? 'block' : 'none';
    box.innerHTML = '';
    list.items.forEach(function (item, i) {
      var el = document.createElement('div');
      el.className = 'audio-item' + (i === list.focus ? ' focused' : '') + (item.current ? ' current' : '') +
        (item.kind === 'mute' && list.items.length > 1 ? ' mute-item' : '');
      el.textContent = item.label;
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
        list.focus = Math.min(list.items.length - 1, list.focus + 1);
        break;
      case 'enter':
        if (this.ok) return; // still the long press that opened the list
        var item = list.items[list.focus];
        if (item.kind === 'mute') {
          this.player.toggleMute();
        } else if (this.backend().selectAudio(list.tracks[item.position].index)) {
          $('osd-state').textContent = CT.AudioTracks.osd(list.tracks, item.position);
        }
        this.closeAudioList();
        return;
      case 'back':
      case 'stop':
        this.closeAudioList();
        return;
      case 'mute': // the yellow key / M with the choice open: its mute item follows
        this.player.toggleMute();
        list.items[list.items.length - 1].label = CT.Mute.label(this.player.mute.muted);
        break;
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
    if (this.backend().paused() || this.press || this.trick || this.switching !== null || this.ended || this.audioList) {
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
    setText($('osd-title'), t.title);
    setText($('osd-subtitle'), t.subtitle);

    var at = pct(playhead);
    $('tl-played').style.width = at + '%';
    $('tl-head').style.left = at + '%';
    var label = $('tl-label');
    setText($('tl-clock'), T.label(playhead, now));
    var tr = this.trick;
    var badge = $('tl-trick');
    setText(badge, tr ? T.trickBadge(tr.direction, tr.speed) : '');
    badge.style.display = tr ? 'inline-block' : 'none';
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
    setText($('tl-start'), ends.start);
    setText($('tl-end'), ends.end);
    var text = this.switching !== null ? 'Načítání…' : this.ended ? 'Konec nahrávky' :
      this.backend().paused() && !tr ? 'Pozastaveno' : '';
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
    return { id: this.tl.id, time: this.timeOf(this.shownPosition()) }; // a seek on its way counts as done
  };

  CT.TimelineController = TimelineController;
})(window);
