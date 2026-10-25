/*
 * Guide images around the focus (spec/apps.md "Image prefetch"): CT images and Anténa thumbnails are fetched
 * into blob URLs ahead of the viewer, nearest to the focus first, the focused one ahead of everything. Kept in
 * memory only (an LRU of object URLs, evicted ones revoked), never in any storage.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var HOUR = 3600 * 1000;

  var Prefetch = {
    ROWS_ABOVE: 3,
    ROWS_BELOW: 8,
    SPAN_MS: 3 * HOUR,
    /** A new window is queued once the focus moved more than this from the last prefetch point. */
    MOVE_ROWS: 3,
    MOVE_MS: HOUR,
    /** Requests at a time per kind: CT's CDN, the gateway (it runs at most 2 ffmpeg). */
    LIMITS: { ct: 4, gw: 2 },
    MAX_IMAGES: 300,
    /** Memory budget of the downloaded images (JPEG bytes as blobs; only the one on screen is decoded). */
    MAX_BYTES: 48 * 1024 * 1024,
    /** Gateway busy (503) without a usable Retry-After: try again after this. */
    BUSY_RETRY_MS: 5000,
    /** A failed image (or a 404 of a growing recording) is asked again after this. */
    RETRY_MS: 60 * 1000,

    /** Whether `focus` {row, time} is far enough from `last` (null = nothing prefetched yet) for a new window. */
    moved: function (last, focus) {
      return !last || Math.abs(focus.row - last.row) > Prefetch.MOVE_ROWS || Math.abs(focus.time - last.time) > Prefetch.MOVE_MS;
    },

    /**
     * The image requests of the window around `focus` {row, time}: rows 3 above to 8 below, slots overlapping
     * ±3 h, nearest first (by rows, then by time; below before above on a tie). `rows` = slot lists
     * ({start, end, item}); `imageOf(item)` = a request {key, …} or null. Each key once.
     */
    window: function (rows, focus, imageOf) {
      var t0 = focus.time - Prefetch.SPAN_MS;
      var t1 = focus.time + Prefetch.SPAN_MS;
      var found = [];
      var r0 = Math.max(0, focus.row - Prefetch.ROWS_ABOVE);
      var r1 = Math.min(rows.length - 1, focus.row + Prefetch.ROWS_BELOW);
      for (var r = r0; r <= r1; r++) {
        var slots = rows[r] || [];
        for (var i = 0; i < slots.length; i++) {
          var s = slots[i];
          if (!s.item || s.end <= t0 || s.start >= t1) continue;
          var request = imageOf(s.item);
          if (!request) continue;
          var dt = s.start > focus.time ? s.start - focus.time : s.end <= focus.time ? focus.time - s.end : 0;
          found.push({ dr: r - focus.row, dt: dt, start: s.start, request: request });
        }
      }
      found.sort(function (a, b) {
        return Math.abs(a.dr) - Math.abs(b.dr) || a.dt - b.dt || b.dr - a.dr || a.start - b.start;
      });
      var seen = {};
      var out = [];
      found.forEach(function (f) {
        if (seen[f.request.key]) return;
        seen[f.request.key] = true;
        out.push(f.request);
      });
      return out;
    }
  };

  /**
   * Fetch queue and memory cache. Requests are {key, kind: 'ct' | 'gw', final, …}; `final` false = may change
   * (a growing recording: its image and its 404 expire after 60 s). `opts`: fetch(request) → Promise of
   * {status, url, size, retryAfter} (url = an object URL or null, size in bytes, retryAfter in seconds),
   * revoke(url), now(), setTimeout, limits, max, maxBytes.
   */
  function ImageLoader(opts) {
    this.fetch = opts.fetch;
    this.now = opts.now || Date.now;
    this.limits = opts.limits || Prefetch.LIMITS;
    this.revoke = opts.revoke || function () {};
    this.setTimeout = opts.setTimeout || function (fn, ms) { return setTimeout(fn, ms); };
    this.cache = new CT.ThumbCache(opts.max || Prefetch.MAX_IMAGES, this.revoke, opts.maxBytes || Prefetch.MAX_BYTES);
    this.queue = []; // waiting requests, next first
    this.running = {}; // key -> [callback]
    this.active = {}; // kind -> requests in flight
    this.failed = {}; // key -> time until which it is not asked again
    this.generations = {}; // kind -> count of clear()s, so late answers after a clear are dropped
    this.busy = {}; // key -> {request, waiting}: answered 503, asked again after Retry-After
    this.inWindow = {}; // keys of the last prefetch window
    this.focusKey = null; // the focused programme's image (load)
  }

  /** The cached object URL of `key`, or null. */
  ImageLoader.prototype.get = function (key) {
    return this.cache.get(key, this.now());
  };

  ImageLoader.prototype.isFailed = function (key) {
    var until = this.failed[key];
    if (until === undefined) return false;
    if (this.now() < until) return true;
    delete this.failed[key];
    return false;
  };

  ImageLoader.prototype.needed = function (request) {
    return !this.running[request.key] && !this.busy[request.key] && !this.isFailed(request.key) && !this.get(request.key);
  };

  /**
   * The focused programme's image: `done(url, status)` at once when cached (or known to fail), else the request
   * goes ahead of the queue. Returns the cached URL or null.
   */
  ImageLoader.prototype.load = function (request, done) {
    this.focusKey = request.key;
    var url = this.get(request.key);
    if (url || this.isFailed(request.key)) {
      if (done) done(url, url ? 200 : 0);
      return url;
    }
    var waiting = this.running[request.key] || (this.busy[request.key] && this.busy[request.key].waiting);
    if (waiting) {
      if (done) waiting.push(done);
      return null;
    }
    this.queue = this.queue.filter(function (q) { return q.key !== request.key; });
    this.queue.unshift({ key: request.key, kind: request.kind, final: request.final, request: request, done: done });
    this.pump();
    return null;
  };

  /**
   * Replaces what waits with `requests` (nearest first); requests already running finish. Waiting requests marked
   * `keep` (the Archiv cards on screen, not part of the guide's window) stay in front.
   */
  ImageLoader.prototype.prefetch = function (requests) {
    var self = this;
    this.inWindow = {};
    requests.forEach(function (r) { self.inWindow[r.key] = true; });
    var kept = this.queue.filter(function (q) { return q.request.keep; });
    this.queue = kept.concat(requests.filter(function (r) { return self.needed(r); }).map(function (r) {
      return { key: r.key, kind: r.kind, final: r.final, request: r, done: null };
    }));
    this.pump();
  };

  ImageLoader.prototype.pump = function () {
    for (var i = 0; i < this.queue.length;) {
      var q = this.queue[i];
      if ((this.active[q.kind] || 0) >= (this.limits[q.kind] || 1)) {
        i++;
        continue;
      }
      this.queue.splice(i, 1);
      if (this.needed(q)) this.start(q);
      else if (q.done) this.load(q.request, q.done);
    }
  };

  ImageLoader.prototype.start = function (q) {
    var self = this;
    var generation = this.generations[q.kind] || 0;
    this.active[q.kind] = (this.active[q.kind] || 0) + 1;
    this.running[q.key] = q.done ? [q.done] : [];
    function finish(status, url, size, retryAfter) {
      self.active[q.kind]--;
      var waiting = self.running[q.key] || [];
      delete self.running[q.key];
      if (generation !== (self.generations[q.kind] || 0)) {
        if (url) self.revoke(url);
        url = null;
      } else if (url) {
        self.cache.put(q.key, url, self.now(), q.final !== false, size);
      } else if (status === 503) {
        // Gateway busy: never remembered as missing, asked again after Retry-After (the waiting callbacks wait on).
        self.later(q, waiting, retryAfter);
        self.pump();
        return;
      } else {
        self.failed[q.key] = status === 404 && q.final !== false ? Infinity : self.now() + Prefetch.RETRY_MS;
      }
      waiting.forEach(function (done) { done(url, status); });
      self.pump();
    }
    this.fetch(q.request).then(function (r) {
      finish(r.status, r.url || null, r.size || 0, r.retryAfter);
    }, function () {
      finish(0, null);
    });
  };

  /** After a 503: back into the queue after `retryAfter` s (default 5) if still focused or in the window. */
  ImageLoader.prototype.later = function (q, waiting, retryAfter) {
    var self = this;
    var ms = retryAfter > 0 ? retryAfter * 1000 : Prefetch.BUSY_RETRY_MS;
    var entry = { request: q.request, waiting: waiting };
    this.busy[q.key] = entry;
    this.setTimeout(function () {
      if (self.busy[q.key] !== entry) return; // cleared meanwhile
      delete self.busy[q.key];
      var done = function (url, status) { entry.waiting.forEach(function (d) { d(url, status); }); };
      if (q.key === self.focusKey) {
        self.queue = self.queue.filter(function (x) { return x.key !== q.key; });
        self.queue.unshift({ key: q.key, kind: q.kind, final: q.final, request: q.request, done: done });
      } else if (self.inWindow[q.key] && self.needed(q)) {
        self.queue.push({ key: q.key, kind: q.kind, final: q.final, request: q.request, done: done });
      } else {
        return; // left the window: dropped like any stale queue item
      }
      self.pump();
    }, ms);
  };

  /** Forgets images (and failures) of one kind, or all; requests running now are discarded when they end. */
  ImageLoader.prototype.clear = function (kind) {
    var self = this;
    var match = kind ? function (key) { return key.indexOf(kind + ':') === 0; } : null;
    this.cache.clear(match);
    this.queue = this.queue.filter(function (q) { return match && !match(q.key); });
    Object.keys(this.failed).forEach(function (key) { if (!match || match(key)) delete self.failed[key]; });
    Object.keys(this.busy).forEach(function (key) { if (!match || match(key)) delete self.busy[key]; });
    (kind ? [kind] : Object.keys(this.limits)).forEach(function (k) {
      self.generations[k] = (self.generations[k] || 0) + 1;
    });
  };

  CT.Prefetch = Prefetch;
  CT.ImageLoader = ImageLoader;
})(typeof window !== 'undefined' ? window : global);
