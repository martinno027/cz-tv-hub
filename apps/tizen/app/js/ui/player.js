/*
 * Full-screen playback. The stream is resolved here, on the TV, right before playback; on a playback
 * error (typically an expired signed URL) it is re-resolved once and resumed.
 *
 * Samsung's AVPlay plays HLS natively. It cannot send request headers, so "Anténa" streams carry the
 * gateway key as a cookie (setStreamingProperty COOKIE). A desktop browser falls back to <video>.
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
    if (target.dvb) av.setStreamingProperty('COOKIE', target.dvb.config.cookie());
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
      if (startMs > 0) {
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
      if (startMs > 0) v.currentTime = startMs / 1000;
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
        self.close();
      },
      onError: function (what) {
        self.onError(what);
      }
    };
    var hasAvPlay = root.webapis && root.webapis.avplay;
    this.backend = hasAvPlay ? new AvPlayBackend(cb) : new Html5Backend(cb);
    $('avplayer').style.display = hasAvPlay ? 'block' : 'none';
    setInterval(function () { self.updateOsd(); }, 1000);
    setInterval(function () { self.saveProgress(); }, RESUME_SAVE_MS);
  }

  Player.prototype.isOpen = function () {
    return this.request !== null;
  };

  Player.prototype.open = function (request) {
    this.request = request;
    this.lastReResolveAt = 0;
    $('player').classList.remove('hidden');
    $('osd-title').textContent = request.title;
    this.load(false, null);
  };

  /** `resumeMs`: position to continue at after re-resolving (null = the target's own start). */
  Player.prototype.load = function (forceRefresh, resumeMs) {
    var self = this;
    var token = ++this.loadToken;
    this.stopBreaks();
    this.backend.close();
    this.showMessage('Načítání…');
    this.app.planner.plan(this.request, forceRefresh).then(function (target) {
      if (token !== self.loadToken) return;
      self.target = target;
      self.plannedAt = Date.now();
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

  Player.prototype.onError = function (what) {
    if (!this.isOpen()) return;
    if (root.console) root.console.warn('playback error: ' + what);
    // Re-resolve once per incident (signed URLs expire after ~1 h of playback). For "Anténa" this also
    // asks the gateway again, which explains a busy tuner in Czech.
    var now = Date.now();
    if (this.lastReResolveAt === 0 || now - this.lastReResolveAt > RE_RESOLVE_COOLDOWN_MS) {
      this.lastReResolveAt = now;
      this.load(true, this.resumePosition());
    } else {
      this.backend.close();
      this.showMessage(CT.Messages.PLAYBACK);
    }
  };

  /**
   * Where to continue after re-resolving: the same archive position; in the rewind buffer the same
   * moment, whose offset shrinks as the buffer slides forward; live restarts at the live edge.
   */
  Player.prototype.resumePosition = function () {
    if (!this.target) return null;
    var position = this.backend.position();
    if (this.target.kind === 'vod') return position;
    if (this.target.kind === 'timeshift') return Math.max(0, position - (Date.now() - this.plannedAt));
    return null;
  };

  Player.prototype.close = function () {
    this.saveProgress();
    this.loadToken++;
    this.stopBreaks();
    this.backend.close();
    this.request = null;
    this.target = null;
    $('player').classList.add('hidden');
    this.app.onPlayerClosed();
  };

  /** The app went to the background: release the player, resolve afresh when it comes back. */
  Player.prototype.suspend = function () {
    if (!this.isOpen()) return;
    this.saveProgress();
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
      default:
        break;
    }
    this.showOsd();
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
    $('osd').classList.add('visible');
    this.updateOsd();
    clearTimeout(this.osdTimer);
    this.osdTimer = setTimeout(function () {
      if (!self.backend.paused()) $('osd').classList.remove('visible');
    }, OSD_MS);
  };

  Player.prototype.updateOsd = function () {
    if (!this.isOpen() || !this.target) return;
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
