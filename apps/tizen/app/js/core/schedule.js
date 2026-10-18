/* Schedule cache per broadcast day and the short-lived live URL cache. */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  /**
   * In-memory schedule cache, one entry per broadcast day (all channels in one request).
   * Today's and future days are refetched after `freshForMs`; past days never change.
   * A failed day is not cached, so it can be retried without breaking the other days.
   */
  function ScheduleRepository(api, clock, freshForMs) {
    this.api = api;
    this.channels = CT.Channels.all;
    this.clock = clock || Date.now;
    this.freshForMs = freshForMs || 3600 * 1000;
    this.cache = {};
  }

  /** Programmes of `day` keyed by channel id, sorted by start. Rejects with CtError. */
  ScheduleRepository.prototype.day = function (day) {
    var self = this;
    var now = this.clock();
    var entry = this.cache[day];
    if (entry && (day < CT.Days.today(now) || now - entry.at < this.freshForMs)) return Promise.resolve(entry.plan);
    var slugs = this.channels.map(function (c) { return c.slug; });
    return this.api.schedules(slugs, day).then(function (bySlug) {
      var plan = {};
      self.channels.forEach(function (ch) {
        plan[ch.id] = (bySlug[ch.slug] || []).slice().sort(function (a, b) { return a.start - b.start; });
      });
      self.cache[day] = { at: now, plan: plan };
      return plan;
    });
  };

  /**
   * {channelId: {current, next}}. Programmes after midnight belong to the previous broadcast day,
   * so yesterday's plan is consulted when today's has nothing on air.
   */
  ScheduleRepository.prototype.onAir = function (now) {
    var self = this;
    var today = CT.Days.today(now);
    var result = {};
    var lastError = null;
    var allFound = function () {
      return self.channels.every(function (c) { return result[c.id] && result[c.id].current; });
    };
    var visit = function (day) {
      if (allFound()) return Promise.resolve();
      return self.day(day).then(function (plan) {
        self.channels.forEach(function (ch) {
          if (result[ch.id] && result[ch.id].current) return;
          var list = plan[ch.id] || [];
          var i = list.findIndex(function (p) { return p.start <= now && now < p.end; });
          if (i >= 0) {
            result[ch.id] = { current: list[i], next: list[i + 1] || null };
          } else if (!result[ch.id]) {
            // A gap between programmes: still tell what comes next.
            result[ch.id] = { current: null, next: list.filter(function (p) { return p.start > now; })[0] || null };
          }
        });
      }, function (e) {
        lastError = e;
      });
    };
    return visit(today).then(function () {
      return visit(CT.Days.addDays(today, -1));
    }).then(function () {
      if (!Object.keys(result).length && lastError) throw lastError;
      return result;
    });
  };

  /** Live stream URLs reused in memory for at most `ttlMs` (signed URLs live ~1 h). */
  function LiveResolver(api, clock, ttlMs) {
    this.api = api;
    this.clock = clock || Date.now;
    this.ttlMs = ttlMs || 60 * 1000;
    this.cache = {};
  }

  LiveResolver.prototype.resolve = function (channelId, forceRefresh) {
    var self = this;
    var now = this.clock();
    var hit = this.cache[channelId];
    if (!forceRefresh && hit && now - hit.at < this.ttlMs) return Promise.resolve(hit.streams);
    return this.api.liveStreams(channelId).then(function (streams) {
      self.cache[channelId] = { at: now, streams: streams };
      return streams;
    });
  };

  CT.ScheduleRepository = ScheduleRepository;
  CT.LiveResolver = LiveResolver;
})(typeof window !== 'undefined' ? window : global);
