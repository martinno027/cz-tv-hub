/*
 * "Pokračovat" (spec/apps.md): where this TV stopped in a programme. Stored on the device only; only positions,
 * never stream URLs or video. Keys: `ct:<idec>`, `dvb:<recording id>`.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var DAY_MS = 24 * 3600 * 1000;

  function pad2(n) {
    return n < 10 ? '0' + n : String(n);
  }

  var ResumeRules = {
    MIN_SECONDS: 60,
    END_MARGIN_SECONDS: 120,
    MAX_ENTRIES: 200,
    MAX_AGE_DAYS: 30,

    /** 'offer' | 'clear' (watched) | 'none' (too little to remember). */
    decide: function (progress, length) {
      if (progress >= length - ResumeRules.END_MARGIN_SECONDS) return 'clear';
      if (progress >= ResumeRules.MIN_SECONDS) return 'offer';
      return 'none';
    },

    /** `m:ss` below an hour, else `h:mm:ss`. */
    label: function (seconds) {
      var s = Math.floor(seconds);
      var h = Math.floor(s / 3600);
      var m = Math.floor(s / 60) % 60;
      return h ? h + ':' + pad2(m) + ':' + pad2(s % 60) : m + ':' + pad2(s % 60);
    }
  };

  /** `backend` = {load(): object | null, save(object)}; tests pass an in-memory one. */
  function ResumeStore(backend, clock) {
    this.backend = backend;
    this.clock = clock || Date.now;
  }

  ResumeStore.prototype.entries = function () {
    var data = this.backend.load();
    return data !== null && typeof data === 'object' && !Array.isArray(data) ? data : {};
  };

  /** Remembers the progress, or forgets the programme once watched (or barely started). */
  ResumeStore.prototype.save = function (key, progress, length) {
    var entries = this.entries();
    var now = this.clock();
    var action = ResumeRules.decide(progress, length);
    if (action === 'offer') {
      entries[key] = { progress: progress, length: length, savedAt: now };
    } else if (action === 'clear') {
      delete entries[key];
    }
    this.backend.save(ResumeStore.prune(entries, now));
  };

  /** {progress, label} to offer for `key`, or null. */
  ResumeStore.prototype.offer = function (key) {
    var e = this.entries()[key];
    if (!e || typeof e.progress !== 'number' || typeof e.length !== 'number') return null;
    if (this.clock() - e.savedAt > ResumeRules.MAX_AGE_DAYS * DAY_MS) return null;
    if (ResumeRules.decide(e.progress, e.length) !== 'offer') return null;
    return { progress: e.progress, label: ResumeRules.label(e.progress) };
  };

  /** Drops entries older than 30 days and keeps the 200 most recent. */
  ResumeStore.prune = function (entries, now) {
    var keys = Object.keys(entries).filter(function (k) {
      var e = entries[k];
      return e && typeof e.savedAt === 'number' && now - e.savedAt <= ResumeRules.MAX_AGE_DAYS * DAY_MS;
    }).sort(function (a, b) { return entries[b].savedAt - entries[a].savedAt; });
    var kept = {};
    keys.slice(0, ResumeRules.MAX_ENTRIES).forEach(function (k) { kept[k] = entries[k]; });
    return kept;
  };

  CT.ResumeRules = ResumeRules;
  CT.ResumeStore = ResumeStore;
})(typeof window !== 'undefined' ? window : global);
