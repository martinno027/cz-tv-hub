/*
 * The web's player backend (spec/web.md "Player"): an HTML5 <video> instead of Samsung's AVPlay, with the same API
 * the shared Player and TimelineController use (open, close, togglePause, paused, position, duration, seekTo,
 * seekBy, audioTracks, currentAudio, selectAudio, audioOutput) and the same callbacks (onReady, onError, onEnded,
 * onBuffering).
 *
 * HLS natively where the browser has it together with an audio track API (Safari), else hls.js (vendor/hls.min.js).
 * hls.js adds the gateway key as `Authorization` only to the gateway's own /dvb/ URLs (never to ČT's CDN); a browser's
 * native HLS cannot add a header and gets the key from the cookie `cztvhub_key` (Path=/dvb, set by web-gateway.js).
 * Anténa video is H.265: `hevcSupported` decides before playing (the Czech text instead of a black picture).
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var LIVE_EDGE_GUARD_MS = 10 * 1000; // as AvPlayBackend: never seek into the last 10 s of a growing playlist
  var HLS_TYPE = 'application/vnd.apple.mpegurl';
  var HEVC_TYPE = 'video/mp4; codecs="hvc1.1.6.L120.90"';
  var HEVC_MESSAGE = 'Tento prohlížeč neumí přehrát vysílání z antény (H.265). Zkuste Safari, nebo Chrome či Edge na ' +
    'počítači s podporou HEVC.';

  /**
   * 'native' | 'hls' | 'none': native HLS only where the browser also lists audio tracks (Safari; a browser with
   * native HLS but no track API would lose the Anténa audio choice), else hls.js when it runs, else native if any.
   */
  function mode(video, Hls) {
    var native = !!(video && video.canPlayType && video.canPlayType(HLS_TYPE));
    var mse = !!(Hls && Hls.isSupported && Hls.isSupported());
    if (native && (video.audioTracks !== undefined || !mse)) return 'native';
    if (mse) return 'hls';
    return 'none';
  }

  /** Whether this browser decodes the Anténa's H.265 in the mode it plays HLS in. */
  function hevcSupported(video, Hls, env) {
    var m = mode(video, Hls);
    if (m === 'native') return !!video.canPlayType(HEVC_TYPE);
    if (m === 'none') return false;
    var source = env && (env.MediaSource || env.ManagedMediaSource);
    return !!(source && source.isTypeSupported && source.isTypeSupported(HEVC_TYPE));
  }

  /** The gateway's own media and API URLs (same origin, path /dvb/…): only these get the key header. */
  function isGatewayUrl(url, origin) {
    var m = /^(https?:\/\/[^/?#]+)(\/[^?#]*)?/i.exec(String(url || ''));
    if (!m) return String(url || '').indexOf('/dvb/') === 0; // relative to the page = the gateway
    return sameOrigin(m[1], origin) && (m[2] || '').indexOf('/dvb/') === 0;
  }

  /** 'http://h:80' and 'http://h' are the same origin. */
  function sameOrigin(a, b) {
    var norm = function (o) {
      var m = /^(https?):\/\/(.+?)(?::(\d+))?$/i.exec(String(o || ''));
      if (!m) return null;
      var scheme = m[1].toLowerCase();
      var port = m[3] || (scheme === 'https' ? '443' : '80');
      return scheme + '://' + m[2].toLowerCase() + ':' + port;
    };
    var na = norm(a);
    return na !== null && na === norm(b);
  }

  /**
   * `deps` = {video, Hls (the hls.js constructor or null), key() → the gateway key or null, origin}. Samsung's
   * Html5Backend is the model; positions are in ms from the playlist's start, as AVPlay's.
   */
  function WebBackend(cb, deps) {
    var self = this;
    this.cb = cb;
    this.video = deps.video;
    this.Hls = deps.Hls || null;
    this.key = deps.key || function () { return null; };
    this.origin = deps.origin;
    this.hls = null;
    this.session = 0; // events of a closed session are ignored
    this.opened = false;
    this.started = false; // playing was asked for (AVPlay: PLAYING/PAUSED, not NONE/IDLE)
    this.audio = new CT.Mute.ElementAudio(this.video); // spec/apps.md "Mute (T34)": the <video>'s own muted
    var v = this.video;
    v.addEventListener('playing', function () { if (self.opened) cb.onBuffering(false); });
    v.addEventListener('waiting', function () { if (self.opened) cb.onBuffering(true); });
    v.addEventListener('ended', function () { if (self.opened) cb.onEnded(); });
    v.addEventListener('error', function () {
      // the error of clearing the source in close() is not an error; hls.js reports its own
      if (self.opened && !self.hls && v.getAttribute('src')) cb.onError('video error');
    });
  }

  WebBackend.prototype.audioOutput = function () {
    return this.audio;
  };

  WebBackend.prototype.mode = function () {
    return mode(this.video, this.Hls);
  };

  /** hls.js settings: start position, and the key header for the gateway's URLs only. */
  WebBackend.prototype.hlsConfig = function (startSeconds) {
    var self = this;
    return {
      startPosition: startSeconds,
      enableWorker: true,
      xhrSetup: function (xhr, url) {
        var key = self.key();
        if (key && isGatewayUrl(url, self.origin)) xhr.setRequestHeader('Authorization', 'Bearer ' + key);
      }
    };
  };

  WebBackend.prototype.open = function (target, startMs) {
    var self = this;
    var cb = this.cb;
    var v = this.video;
    this.close();
    var session = ++this.session;
    this.opened = true;
    // A timeline and "Od začátku" always seek (also to 0): a growing EVENT playlist would otherwise start live.
    var seek = startMs > 0 || !!target.alwaysSeek;
    var startSeconds = seek ? Math.max(0, startMs) / 1000 : -1;
    var started = false;
    var start = function () {
      if (session !== self.session || started) return;
      started = true;
      self.started = true;
      self.playVideo();
      cb.onReady();
    };
    v.style.display = 'block';
    var how = this.mode();
    if (how === 'hls') {
      var Hls = this.Hls;
      var hls = this.hls = new Hls(this.hlsConfig(startSeconds));
      hls.on(Hls.Events.MANIFEST_PARSED, start);
      hls.on(Hls.Events.ERROR, function (event, data) {
        if (session !== self.session || !data || !data.fatal) return;
        cb.onError(String(data.type) + ': ' + String(data.details));
      });
      hls.loadSource(target.url);
      hls.attachMedia(v);
      return;
    }
    if (how === 'none') {
      cb.onError('no HLS in this browser');
      return;
    }
    v.onloadedmetadata = function () {
      if (session !== self.session) return;
      if (seek) v.currentTime = startSeconds;
      start();
    };
    v.src = target.url;
  };

  /** play() may be refused (autoplay without a gesture): the video then stays paused and the bar says so. */
  WebBackend.prototype.playVideo = function () {
    try {
      var p = this.video.play();
      if (p && p.catch) p.catch(function () {});
    } catch (e) {
      // see above
    }
  };

  WebBackend.prototype.close = function () {
    var v = this.video;
    this.session++;
    this.opened = false;
    this.started = false;
    if (this.hls) {
      try {
        this.hls.destroy();
      } catch (e) {
        // already gone
      }
      this.hls = null;
    }
    v.onloadedmetadata = null;
    if (v.getAttribute('src')) {
      v.removeAttribute('src');
      v.load();
    }
    v.style.display = 'none';
  };

  WebBackend.prototype.togglePause = function () {
    if (this.video.paused) this.playVideo();
    else this.video.pause();
  };

  /** Paused by the viewer: as AVPlay's PAUSED, so a failed or loading stream does not read "Pozastaveno". */
  WebBackend.prototype.paused = function () {
    return this.started && !!this.video.paused;
  };

  WebBackend.prototype.position = function () {
    return (this.video.currentTime || 0) * 1000;
  };

  WebBackend.prototype.duration = function () {
    var d = this.video.duration;
    return isFinite(d) && d > 0 ? d * 1000 : 0;
  };

  WebBackend.prototype.seekTo = function (ms) {
    var duration = this.duration();
    var to = Math.max(0, ms);
    if (duration > 0) to = Math.min(to, Math.max(0, duration - LIVE_EDGE_GUARD_MS));
    this.video.currentTime = to / 1000;
  };

  WebBackend.prototype.seekBy = function (deltaMs) {
    this.seekTo(this.position() + deltaMs);
  };

  /** [{index, language}]: hls.js's renditions (the manifest's LANGUAGE), or the browser's own audioTracks. */
  WebBackend.prototype.audioTracks = function () {
    var out = [];
    var i;
    if (this.hls) {
      var tracks = this.hls.audioTracks || [];
      for (i = 0; i < tracks.length; i++) out.push({ index: i, language: tracks[i].lang || '' });
      return out;
    }
    var list = this.video.audioTracks;
    for (i = 0; list && i < list.length; i++) out.push({ index: i, language: list[i].language || '' });
    return out;
  };

  WebBackend.prototype.currentAudio = function () {
    if (this.hls) return typeof this.hls.audioTrack === 'number' ? this.hls.audioTrack : -1;
    var list = this.video.audioTracks;
    for (var i = 0; list && i < list.length; i++) if (list[i].enabled) return i;
    return -1;
  };

  WebBackend.prototype.selectAudio = function (index) {
    if (this.hls) {
      if (index < 0 || index >= (this.hls.audioTracks || []).length) return false;
      this.hls.audioTrack = index;
      return true;
    }
    var list = this.video.audioTracks;
    if (!list || index < 0 || index >= list.length) return false;
    for (var i = 0; i < list.length; i++) list[i].enabled = i === index;
    return true;
  };

  CT.WebPlayer = {
    HEVC_TYPE: HEVC_TYPE,
    HEVC_MESSAGE: HEVC_MESSAGE,
    mode: mode,
    hevcSupported: hevcSupported,
    isGatewayUrl: isGatewayUrl,
    sameOrigin: sameOrigin,
    WebBackend: WebBackend
  };
})(typeof window !== 'undefined' ? window : global);
