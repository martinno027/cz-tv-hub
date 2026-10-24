/*
 * Full-screen playback. The stream is resolved here, on the TV, right before playback; on a playback
 * error (typically an expired signed URL) it is re-resolved once and resumed.
 *
 * Samsung's AVPlay plays HLS natively. It cannot send request headers, so "Anténa" streams carry the
 * gateway key as a cookie (setStreamingProperty COOKIE) - only for the gateway's own streams. Poslanecká sněmovna
 * streams are opened with the published URL and nothing else (no cookie, no header), live only. A desktop browser
 * falls back to <video>.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var RE_RESOLVE_COOLDOWN_MS = 5 * 60 * 1000;
  var LIVE_EDGE_GUARD_MS = 10 * 1000;
  var OSD_MS = 4000;
  var BREAKS_REFRESH_MS = 60 * 1000;
  var BREAKS_CHECK_MS = 500;
  var RESUME_SAVE_MS = 10 * 1000;
  var ZAP_BOX_MS = 3000;
  var HINT_LIVE = 'OK pauza · ▲ ▼ kanál · 0–9 číslo kanálu · AUDIO zvuk · Zpět konec';
  var HINT_OTHER = 'OK pauza · ◀ ▶ posun o 10 s · ⏪ ⏩ o minutu · ▲ AUDIO zvuk · Zpět konec';
  var HINT_TIMELINE = 'OK pauza · ◀ ▶ 10 s, podržet = rychleji · ▲ ▼ začátek pořadu · AUDIO nebo podržet OK zvuk · Zpět konec';

  function isLive(request) {
    return !!request && (request.kind === 'live' || request.kind === 'dvbLive' || request.kind === 'pspLive');
  }

  function channelKey(request) {
    return CT.LIVE_PREFIX[request.kind] + request.channelId;
  }

  function $(id) {
    return document.getElementById(id);
  }

  function clock(ms) {
    var s = Math.max(0, Math.floor(ms / 1000));
    var h = Math.floor(s / 3600);
    var m = Math.floor(s / 60) % 60;
    var sec = s % 60;
    return (h ? h + ':' + (m < 10 ? '0' : '') : '') + m + ':' + (sec < 10 ? '0' : '') + sec;
  }

  /** AVPlay backend; callbacks: onReady, onError(text), onEnded, onBuffering(bool). */
  function AvPlayBackend(cb) {
    this.av = root.webapis.avplay;
    this.cb = cb;
  }

  AvPlayBackend.prototype.open = function (target, startMs) {
    var av = this.av;
    var cb = this.cb;
    this.close();
    av.open(target.url);
    av.setDisplayRect(0, 0, 1920, 1080);
    av.setDisplayMethod('PLAYER_DISPLAY_MODE_LETTER_BOX');
    // The gateway key cookie goes only to the gateway (never to CT's or the Chamber's CDN).
    if (target.dvb && !target.psp) av.setStreamingProperty('COOKIE', target.dvb.config.cookie());
    if (target.kind === 'live') {
      // Live: start once ~2 s (one gateway segment) is buffered; not every firmware accepts this.
      try {
        av.setBufferingParam('PLAYER_BUFFER_FOR_PLAY', 'PLAYER_BUFFER_SIZE_IN_SECOND', 2);
      } catch (e) {
        // keep the player's default
      }
    }
    av.setListener({
      onbufferingstart: function () { cb.onBuffering(true); },
      onbufferingcomplete: function () { cb.onBuffering(false); },
      onstreamcompleted: function () { cb.onEnded(); },
      onerror: function (type) { cb.onError(String(type)); },
      onevent: function () {},
      oncurrentplaytime: function () {}
    });
    av.prepareAsync(function () {
      // A timeline and "Od začátku" always seek (also to 0): a growing EVENT playlist would otherwise start live.
      if (startMs > 0 || target.alwaysSeek) {
        av.seekTo(startMs, function () {
          av.play();
          cb.onReady();
        }, function () {
          av.play();
          cb.onReady();
        });
      } else {
        av.play();
        cb.onReady();
      }
    }, function (e) {
      cb.onError(e && e.name ? e.name : 'prepare failed');
    });
  };

  AvPlayBackend.prototype.close = function () {
    try {
      if (this.av.getState() !== 'NONE') {
        this.av.stop();
        this.av.close();
      }
    } catch (e) {
      // Already closed.
    }
  };

  AvPlayBackend.prototype.state = function () {
    try {
      return this.av.getState();
    } catch (e) {
      return 'NONE';
    }
  };

  AvPlayBackend.prototype.togglePause = function () {
    if (this.state() === 'PLAYING') this.av.pause();
    else if (this.state() === 'PAUSED') this.av.play();
  };

  AvPlayBackend.prototype.paused = function () {
    return this.state() === 'PAUSED';
  };

  AvPlayBackend.prototype.position = function () {
    try {
      return this.av.getCurrentTime();
    } catch (e) {
      return 0;
    }
  };

  AvPlayBackend.prototype.duration = function () {
    try {
      return this.av.getDuration();
    } catch (e) {
      return 0;
    }
  };

  AvPlayBackend.prototype.seekTo = function (ms) {
    var duration = this.duration();
    var to = Math.max(0, ms);
    if (duration > 0) to = Math.min(to, Math.max(0, duration - LIVE_EDGE_GUARD_MS));
    try {
      this.av.seekTo(to);
    } catch (e) {
      // Not seekable (plain live).
    }
  };

  /** Audio tracks [{index, language}] and the selected one's index (AVPlay's track list). */
  AvPlayBackend.prototype.audioTracks = function () {
    try {
      return CT.AudioTracks.fromAvPlay(this.av.getTotalTrackInfo());
    } catch (e) {
      return [];
    }
  };

  AvPlayBackend.prototype.currentAudio = function () {
    try {
      var info = this.av.getCurrentStreamInfo().filter(function (t) { return t.type === 'AUDIO'; })[0];
      return info ? info.index : -1;
    } catch (e) {
      return -1;
    }
  };

  AvPlayBackend.prototype.selectAudio = function (index) {
    try {
      this.av.setSelectTrack('AUDIO', index);
      return true;
    } catch (e) {
      return false;
    }
  };

  AvPlayBackend.prototype.seekBy = function (deltaMs) {
    this.seekTo(this.position() + deltaMs);
  };

  /** <video> backend for desktop development (plays HLS only where the browser can). */
  function Html5Backend(cb) {
    var v = this.video = $('html5player');
    this.cb = cb;
    v.onplaying = function () { cb.onBuffering(false); };
    v.onwaiting = function () { cb.onBuffering(true); };
    v.onended = function () { cb.onEnded(); };
    v.onerror = function () {
      if (v.getAttribute('src')) cb.onError('video error'); // not the error of clearing src in close()
    };
  }

  Html5Backend.prototype.open = function (target, startMs) {
    var v = this.video;
    var cb = this.cb;
    v.style.display = 'block';
    v.src = target.url;
    v.onloadedmetadata = function () {
      if (startMs > 0 || target.alwaysSeek) v.currentTime = startMs / 1000;
      v.play();
      cb.onReady();
    };
  };

  Html5Backend.prototype.close = function () {
    this.video.removeAttribute('src');
    this.video.load();
    this.video.style.display = 'none';
  };

  Html5Backend.prototype.togglePause = function () {
    if (this.video.paused) this.video.play();
    else this.video.pause();
  };

  Html5Backend.prototype.paused = function () {
    return this.video.paused;
  };

  Html5Backend.prototype.position = function () {
    return (this.video.currentTime || 0) * 1000;
  };

  Html5Backend.prototype.duration = function () {
    var d = this.video.duration;
    return isFinite(d) ? d * 1000 : 0;
  };

  Html5Backend.prototype.seekTo = function (ms) {
    this.video.currentTime = Math.max(0, ms / 1000);
  };

  Html5Backend.prototype.audioTracks = function () {
    var list = this.video.audioTracks;
    var out = [];
    for (var i = 0; list && i < list.length; i++) out.push({ index: i, language: list[i].language });
    return out;
  };

  Html5Backend.prototype.currentAudio = function () {
    var list = this.video.audioTracks;
    for (var i = 0; list && i < list.length; i++) if (list[i].enabled) return i;
    return -1;
  };

  Html5Backend.prototype.selectAudio = function (index) {
    var list = this.video.audioTracks;
    if (!list) return false;
    for (var i = 0; i < list.length; i++) list[i].enabled = i === index;
    return true;
  };

  Html5Backend.prototype.seekBy = function (deltaMs) {
    this.seekTo(this.position() + deltaMs);
  };

  /** The player overlay. `app` provides planner and close(). */
  function Player(app) {
    var self = this;
    this.app = app;
    this.request = null;
    this.target = null;
    this.plannedAt = 0;
    this.lastReResolveAt = 0;
    this.loadToken = 0;
    this.osdTimer = null;
    this.breaks = [];
    this.breaksTimer = null;
    this.skipTo = null;
    this.zapList = []; // live: the visible channels when the player opened ([{key, source, channel}])
    this.zapTimer = null;
    this.zapBoxTimer = null;
    this.numberEntry = new CT.NumberEntry(function (n) { self.zapToNumber(n); });
    this.tl = null; // TimelineController while an Anténa timeline plays
    this.timelineAt = null; // {id, time}: the timeline window and wall clock to continue at after re-resolving
    var cb = {
      onReady: function () {
        self.hideMessage();
        self.showOsd();
        self.startBreaks();
      },
      onBuffering: function (on) {
        $('osd-state').textContent = on ? 'Načítání…' : '';
      },
      onEnded: function () {
        if (self.tl) self.tl.onEnded();
        else self.close();
      },
      onError: function (what) {
        self.onError(what);
      }
    };
    var hasAvPlay = root.webapis && root.webapis.avplay;
    this.backend = this.mainBackend = hasAvPlay ? new AvPlayBackend(cb) : new Html5Backend(cb);
    // ?demo: a simulated player for timelines, so the timeline bar can be checked in a browser
    this.demoBackend = CT.Demo.enabled ? CT.Demo.backend(cb) : null;
    $('avplayer').style.display = hasAvPlay ? 'block' : 'none';
    setInterval(function () { self.updateOsd(); self.updateTitle(); }, 1000);
    setInterval(function () { self.saveProgress(); }, RESUME_SAVE_MS);
  }

  Player.prototype.isOpen = function () {
    return this.request !== null;
  };

  Player.prototype.open = function (request) {
    this.request = request;
    this.lastReResolveAt = 0;
    this.timelineAt = null;
    // Zapping uses the list of the moment the player opened (a settings change applies next time).
    this.zapList = isLive(request) ? this.app.channelList(this.app.dvbChannels) : [];
    $('osd-hint').textContent = isLive(request) ? HINT_LIVE : HINT_OTHER;
    $('player').classList.remove('hidden');
    this.updateTitle();
    this.load(false, null);
  };

  /** ▲/▼, CH+/CH− while live: the neighbour channel; the stream is resolved after a short pause. */
  Player.prototype.zap = function (by) {
    var keys = this.zapList.map(function (item) { return item.key; });
    var key = CT.ChannelNumbers.step(keys, channelKey(this.request), by);
    if (key === null) return;
    this.numberEntry.cancel();
    this.switchTo(keys.indexOf(key), true);
  };

  Player.prototype.zapToNumber = function (number) {
    if (!isLive(this.request)) return;
    var keys = this.zapList.map(function (item) { return item.key; });
    if (CT.ChannelNumbers.resolve(keys, number) === null) {
      this.numberEntry.notFound(number);
      return;
    }
    this.switchTo(number - 1, false);
  };

  /** Plays zapList[index] live; `delayed` = wait for more quick presses before resolving. */
  Player.prototype.switchTo = function (index, delayed) {
    var self = this;
    var item = this.zapList[index];
    this.request = this.app.liveRequest(item);
    this.lastReResolveAt = 0;
    this.updateTitle();
    this.showZapBox(index);
    clearTimeout(this.zapTimer);
    if (!delayed) {
      this.zapTimer = null;
      this.load(false, null);
      return;
    }
    var token = this.loadToken;
    this.zapTimer = setTimeout(function () {
      self.zapTimer = null;
      if (token === self.loadToken && self.isOpen()) self.load(false, null);
    }, CT.ChannelNumbers.ZAP_DELAY_MS);
  };

  /** "<number>  <channel>" and the programme airing now, top left for ~3 s. */
  Player.prototype.showZapBox = function (index) {
    var item = this.zapList[index];
    var channel = $('zap-channel');
    channel.innerHTML = '';
    var number = document.createElement('span');
    number.className = 'zap-number';
    number.textContent = String(index + 1);
    channel.appendChild(number);
    channel.appendChild(document.createTextNode(item.channel.name));
    var now = this.app.airingNow(item);
    var text = now ? now.title : '';
    if (item.source === 'psp') {
      var state = this.app.pspState(item.channel.id);
      text = state === 'live' ? CT.Psp.LIVE_TEXT : state === 'off' ? CT.Psp.OFF_TEXT : '';
    }
    $('zap-programme').textContent = text;
    $('zap-box').classList.add('visible');
    clearTimeout(this.zapBoxTimer);
    this.zapBoxTimer = setTimeout(function () { $('zap-box').classList.remove('visible'); }, ZAP_BOX_MS);
  };

  /** The playing channel's name (the request's title when the channel list does not have it). */
  Player.prototype.channelName = function () {
    var r = this.request;
    var app = this.app;
    var isDvb = r.kind === 'dvbLive' || r.kind === 'dvbRecorded';
    var channel = null;
    if (r.kind === 'pspLive') {
      channel = CT.Psp.byId(r.channelId);
    } else if (isDvb) {
      var list = (app.dvbState && app.dvbState.channels) || app.dvbChannels || [];
      channel = list.filter(function (c) { return c.id === r.channelId; })[0] || null;
    } else {
      channel = CT.Channels.byId(r.channelId);
    }
    return channel ? channel.name : r.title;
  };

  /** Info bar text; live is re-evaluated every second against the guide data the app keeps fresh. */
  Player.prototype.updateTitle = function () {
    var r = this.request;
    if (!r || this.tl) return; // the timeline bar names the programme at the playhead itself
    var app = this.app;
    var name = this.channelName();
    var t;
    if (r.kind === 'live') {
      t = CT.PlayerTitle.live(name, app.ctState && app.ctState.onAir ? app.ctState.onAir[r.channelId] : null, Date.now());
    } else if (r.kind === 'pspLive') {
      t = { title: name, subtitle: CT.Psp.LIVE_TEXT + ' · ' + CT.Psp.SOURCE_LABEL };
    } else if (r.kind === 'dvbLive') {
      t = CT.PlayerTitle.live(name, app.dvbState && app.dvbState.onAir ? app.dvbState.onAir[r.channelId] : null, Date.now());
    } else {
      t = CT.PlayerTitle.programme(r.title, name, r.episodeTitle || '');
    }
    if ($('osd-title').textContent !== t.title) $('osd-title').textContent = t.title;
    if ($('osd-subtitle').textContent !== t.subtitle) $('osd-subtitle').textContent = t.subtitle;
  };

  /**
   * `resumeMs`: position to continue at after re-resolving (null = the target's own start). A timeline continues at
   * the wall clock in `timelineAt` instead (positions of a re-fetched window may differ).
   */
  Player.prototype.load = function (forceRefresh, resumeMs) {
    var self = this;
    var token = ++this.loadToken;
    var at = this.timelineAt;
    this.stopBreaks();
    this.backend.close();
    this.showMessage('Načítání…');
    var planned = at ? this.app.planner.timelineWindow(at.id, this.request.title, at.time) :
      this.app.planner.plan(this.request, forceRefresh);
    planned.then(function (target) {
      if (token !== self.loadToken) return;
      self.useTarget(target);
      self.plannedAt = Date.now();
      self.timelineAt = null;
      if (self.tl) self.tl.setWindow(target.timeline);
      var start = resumeMs;
      if (start === null) {
        if (target.startPositionMs !== null) start = target.startPositionMs;
        else if (target.kind === 'timeshift') start = target.fallbackOffsetMs;
        else start = 0;
      }
      try {
        self.backend.open(target, start);
      } catch (e) {
        self.onError(String(e));
      }
    }, function (e) {
      if (token !== self.loadToken) return;
      self.showMessage(e && e.userMessage ? e.userMessage : CT.Messages.GENERIC);
    });
  };

  /** The backend for `target` and the timeline controller for a timeline; returns nothing. */
  Player.prototype.useTarget = function (target) {
    this.target = target;
    var backend = target.kind === 'timeline' && this.demoBackend ? this.demoBackend : this.mainBackend;
    if (backend !== this.backend) {
      this.backend.close();
      this.backend = backend;
    }
    if (target.kind === 'timeline') {
      if (!this.tl) this.tl = new CT.TimelineController(this);
      $('osd-hint').textContent = HINT_TIMELINE;
    } else if (this.tl) {
      this.tl.stop();
      this.tl = null;
    }
  };

  /** The neighbouring timeline window: plays at its mapped position, the bar stays on screen. */
  Player.prototype.switchTarget = function (target) {
    this.loadToken++;
    this.stopBreaks();
    this.useTarget(target);
    this.plannedAt = Date.now();
    this.timelineAt = null;
    this.tl.setWindow(target.timeline);
    try {
      this.backend.open(target, target.startPositionMs);
    } catch (e) {
      this.onError(String(e));
    }
  };

  /** The same target again from `ms` (a timeline after its stream completed). */
  Player.prototype.reopen = function (ms) {
    try {
      this.backend.open(this.target, Math.max(0, ms));
    } catch (e) {
      this.onError(String(e));
    }
  };

  Player.prototype.onError = function (what) {
    if (!this.isOpen()) return;
    if (root.console) root.console.warn('playback error: ' + what);
    // Re-resolve once per incident (signed URLs expire after ~1 h of playback). For "Anténa" this also
    // asks the gateway again, which explains a busy tuner in Czech.
    var now = Date.now();
    if (this.lastReResolveAt === 0 || now - this.lastReResolveAt > RE_RESOLVE_COOLDOWN_MS) {
      this.lastReResolveAt = now;
      this.timelineAt = this.tl ? this.tl.continueAt() : null;
      this.load(true, this.resumePosition());
    } else {
      this.backend.close();
      this.showMessage(CT.Messages.PLAYBACK);
      if (this.request.kind === 'pspLive') this.explainPsp();
    }
  };

  /** A Chamber stream failed again: if its playlist says it is not on (a check reused or ≥ 60 s apart), say so. */
  Player.prototype.explainPsp = function () {
    var self = this;
    var request = this.request;
    this.app.psp.open(request.channelId, false).then(function (state) {
      if (self.request === request && state === 'off') self.showMessage(CT.Psp.OFF_MESSAGE);
    });
  };

  /**
   * Where to continue after re-resolving: the same archive position; in the rewind buffer the same
   * moment, whose offset shrinks as the buffer slides forward; live restarts at the live edge.
   */
  Player.prototype.resumePosition = function () {
    if (!this.target || this.target.kind === 'timeline') return null;
    var position = this.backend.position();
    if (this.target.kind === 'vod') return position;
    if (this.target.kind === 'timeshift') return Math.max(0, position - (Date.now() - this.plannedAt));
    return null;
  };

  Player.prototype.close = function () {
    var request = this.request;
    this.saveProgress();
    this.loadToken++;
    this.stopBreaks();
    this.backend.close();
    clearTimeout(this.zapTimer);
    this.zapTimer = null;
    this.numberEntry.cancel();
    clearTimeout(this.zapBoxTimer);
    $('zap-box').classList.remove('visible');
    this.request = null;
    this.target = null;
    if (this.tl) this.tl.stop();
    this.tl = null;
    this.timelineAt = null;
    $('player').classList.add('hidden');
    this.app.onPlayerClosed(request);
  };

  /** The app went to the background: release the player, resolve afresh when it comes back. */
  Player.prototype.suspend = function () {
    if (!this.isOpen()) return;
    this.saveProgress();
    this.timelineAt = this.tl ? this.tl.continueAt() : null;
    this.suspendedAt = this.resumePosition();
    this.loadToken++;
    this.stopBreaks();
    this.backend.close();
  };

  Player.prototype.resume = function () {
    if (!this.isOpen()) return;
    this.load(true, this.suspendedAt === undefined ? null : this.suspendedAt);
  };

  Player.prototype.onKey = function (key) {
    if (this.tl) {
      this.tl.onKeyDown(key);
      return;
    }
    var live = isLive(this.request);
    var digit = CT.Keys.digit(key);
    if (digit >= 0) {
      // Number entry only while live (spec/apps.md "Channel numbers and zapping").
      if (live) this.numberEntry.digit(digit, this.zapList.length);
      return;
    }
    if (this.numberEntry.active() && (key === 'enter' || key === 'back')) {
      if (key === 'enter') this.numberEntry.commit();
      else this.numberEntry.cancel();
      return;
    }
    switch (key) {
      case 'back':
      case 'stop':
        this.close();
        return;
      case 'enter':
        // While "Přeskočit reklamu" is shown, OK presses it; otherwise OK pauses.
        if (this.skipTo !== null) {
          this.skipAd();
          return;
        }
        if (!this.target) return;
        this.backend.togglePause();
        break;
      case 'playpause':
      case 'play':
      case 'pause':
        if (!this.target) return;
        this.backend.togglePause();
        break;
      case 'left':
        this.seek(-10 * 1000);
        break;
      case 'right':
        this.seek(10 * 1000);
        break;
      case 'rewind':
        this.seek(-60 * 1000);
        break;
      case 'forward':
        this.seek(60 * 1000);
        break;
      case 'up':
        if (live) this.zap(-1);
        else this.nextAudio(); // start-over, archive and recordings keep ▲ as before
        break;
      case 'down':
        if (live) this.zap(1);
        break;
      case 'chup': // CH+ = next = ▼
        if (live) this.zap(1);
        break;
      case 'chdown':
        if (live) this.zap(-1);
        break;
      case 'audio':
        this.nextAudio();
        break;
      default:
        break;
    }
    this.showOsd();
  };

  /** Key releases: the timeline player seeks on release and tells a long OK from a short one. */
  Player.prototype.onKeyUp = function (key) {
    if (this.tl) this.tl.onKeyUp(key);
  };

  /** AUDIO/MTS or green (▲ outside live): the next broadcast audio track (Anténa renditions, spec/apps.md "Audio choice for Anténa"). */
  Player.prototype.nextAudio = function () {
    if (!this.target) return;
    var tracks = this.backend.audioTracks();
    if (tracks.length < 2) {
      $('osd-state').textContent = 'Jen jedna zvuková stopa';
      return;
    }
    var position = CT.AudioTracks.next(tracks, this.backend.currentAudio());
    if (this.backend.selectAudio(tracks[position].index)) {
      $('osd-state').textContent = CT.AudioTracks.osd(tracks, position);
    }
  };

  /**
   * "Pokračovat": seconds from the programme start, remembered on this TV. Archive and Anténa recordings use the
   * position; the rewind buffer has no wall clock in AVPlay, so its start is estimated as planned-at − 3 h.
   */
  Player.prototype.progressSeconds = function () {
    var r = this.request;
    var t = this.target;
    if (!r || !t || !r.resumeKey || t.kind === 'live') return null;
    var position = this.backend.position();
    if (t.kind === 'vod') return position / 1000;
    var wall = this.plannedAt - CT.Decisions.TIMESHIFT_WINDOW_MS + position;
    return (wall - r.start) / 1000;
  };

  Player.prototype.saveProgress = function () {
    if (!this.isOpen() || !this.target || this.backend.position() <= 0) return;
    if (this.tl) {
      this.tl.saveProgress(this.app.resumeStore);
      return;
    }
    var progress = this.progressSeconds();
    if (progress === null || progress < 0) return;
    this.app.resumeStore.save(this.request.resumeKey, progress, this.request.lengthSeconds);
  };

  /**
   * "Přeskočit reklamu" (spec/ad-breaks.md): only for finished "Anténa" recordings. Breaks are fetched on
   * start and every minute; failures are silent (no button).
   */
  Player.prototype.startBreaks = function () {
    var self = this;
    var target = this.target;
    this.stopBreaks();
    if (!target || !target.dvb || !target.recordingId) return;
    var token = this.loadToken;
    var fetch = function () {
      target.dvb.breaks(target.recordingId).then(function (list) {
        if (token === self.loadToken) self.breaks = list;
      }, function () {});
    };
    fetch();
    var fetchedAt = Date.now();
    this.breaksTimer = setInterval(function () {
      if (Date.now() - fetchedAt >= BREAKS_REFRESH_MS) {
        fetchedAt = Date.now();
        fetch();
      }
      self.updateSkip();
    }, BREAKS_CHECK_MS);
  };

  Player.prototype.stopBreaks = function () {
    clearInterval(this.breaksTimer);
    this.breaksTimer = null;
    this.breaks = [];
    this.setSkip(null);
  };

  Player.prototype.updateSkip = function () {
    this.setSkip(CT.AdBreaks.skipTarget(this.breaks, this.backend.position() / 1000));
  };

  Player.prototype.setSkip = function (to) {
    this.skipTo = to;
    $('skip-ad').classList.toggle('visible', to !== null);
  };

  Player.prototype.skipAd = function () {
    if (this.skipTo === null) return;
    this.backend.seekTo(this.skipTo * 1000);
    this.setSkip(null);
  };

  Player.prototype.seek = function (deltaMs) {
    if (!this.target || this.target.kind === 'live') return;
    this.backend.seekBy(deltaMs);
  };

  Player.prototype.showOsd = function () {
    var self = this;
    if (this.tl) {
      this.tl.showBar();
      return;
    }
    $('osd').classList.add('visible');
    this.updateOsd();
    clearTimeout(this.osdTimer);
    this.osdTimer = setTimeout(function () {
      if (!self.backend.paused()) $('osd').classList.remove('visible');
    }, OSD_MS);
  };

  Player.prototype.updateOsd = function () {
    if (!this.isOpen() || !this.target || this.tl) return;
    var live = this.target.kind === 'live';
    var position = this.backend.position();
    var duration = this.backend.duration();
    $('osd-bar').style.visibility = live || !duration ? 'hidden' : 'visible';
    $('osd-progress').style.width = duration ? Math.min(100, position / duration * 100) + '%' : '0';
    $('osd-position').textContent = live ? 'Živě' : clock(position);
    $('osd-duration').textContent = live || !duration ? '' : clock(duration);
    if (this.backend.paused()) $('osd-state').textContent = 'Pozastaveno';
  };

  Player.prototype.showMessage = function (text) {
    var m = $('player-message');
    m.textContent = text;
    m.style.display = 'flex';
  };

  Player.prototype.hideMessage = function () {
    $('player-message').style.display = 'none';
  };

  CT.Player = Player;
})(window);
