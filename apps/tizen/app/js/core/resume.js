/*
 * "Pokračovat" (spec/apps.md): where this TV stopped in a programme. Stored on the device only; only positions,
 * never stream URLs or video. Keys: `ct:<idec>`, `dvb:<recording id>`. From T13 an entry also keeps what the
 * Archiv row "Pokračovat ve sledování" shows: title, showTitle, image (a CT image URL or a gateway path), source
 * (`ct` / `dvb`) and savedAt; entries saved before have none of it and stay out of the row. ČT entries also keep the
 * programme's `start` (epoch ms) and `channel` (CT API id) when known, so the row resumes by the decision rules
 * (from the buffer within 3 h of the start).
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var DAY_MS = 24 * 3600 * 1000;
  var META_LIMITS = { title: 300, showTitle: 300, image: 1000 };

  /**
   * The row fields of `meta` for the entry `key` as usable strings (cut to a sane length), or null without a title.
   * A missing or unknown source follows the key. `start` and `channel` only for ČT, only both and only valid.
   */
  function cleanMeta(meta, key) {
    if (!meta || typeof meta.title !== 'string' || !meta.title.trim()) return null;
    var out = {};
    Object.keys(META_LIMITS).forEach(function (k) {
      out[k] = typeof meta[k] === 'string' ? meta[k].trim().slice(0, META_LIMITS[k]) : '';
    });
    out.source = meta.source === 'dvb' || meta.source === 'ct' ? meta.source : key.indexOf('dvb:') === 0 ? 'dvb' : 'ct';
    if (out.source === 'ct' && typeof meta.start === 'number' && isFinite(meta.start) && typeof meta.channel === 'string' &&
      CT.Channels.byId(meta.channel)) {
      out.start = meta.start;
      out.channel = meta.channel;
    }
    return out;
  }

  /** savedAt as epoch ms (stored as a number; ISO strings are accepted too), else NaN. */
  function savedMs(value) {
    if (typeof value === 'number') return value;
    return typeof value === 'string' ? Date.parse(value) : NaN;
  }

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
    },

    CONTINUE_MAX: 20,

    /**
     * The Archiv row "Pokračovat ve sledování" (golden `archive_cases.json` "continueRow"): keys of the entries
     * [{key, savedAt, title, …}] that have a title, newest first, at most 20.
     */
    continueRow: function (entries) {
      return entries.filter(function (e) {
        return e && typeof e.title === 'string' && e.title.trim() && !isNaN(savedMs(e.savedAt));
      }).sort(function (a, b) {
        return savedMs(b.savedAt) - savedMs(a.savedAt);
      }).slice(0, ResumeRules.CONTINUE_MAX).map(function (e) { return e.key; });
    },

    /**
     * What the row shows for a programme: the episode as title under its show when there is one, else the
     * programme alone. `image` = a CT image URL or a gateway path, `source` = 'ct' | 'dvb'; a ČT programme with a
     * known broadcast also gets `start` (epoch ms) and `channel` (CT API id).
     */
    meta: function (title, episode, image, source, start, channel) {
      var t = String(title || '').trim();
      var e = String(episode || '').trim();
      var meta = { title: e || t, showTitle: e ? t : '', image: image || '', source: source };
      if (source === 'ct' && typeof start === 'number' && channel) {
        meta.start = start;
        meta.channel = channel;
      }
      return meta;
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

  /**
   * Remembers the progress, or forgets the programme once watched (or barely started). `meta` (optional) =
   * {title, showTitle, image, source} for the Archiv row; without it the entry keeps what it had.
   */
  ResumeStore.prototype.save = function (key, progress, length, meta) {
    if (typeof progress !== 'number' || !isFinite(progress) || typeof length !== 'number' || !isFinite(length)) return;
    var entries = this.entries();
    var now = this.clock();
    var action = ResumeRules.decide(progress, length);
    if (action === 'offer') {
      var entry = { progress: progress, length: length, savedAt: now };
      var row = cleanMeta(meta, key) || cleanMeta(entries[key], key);
      if (row) Object.keys(row).forEach(function (k) { entry[k] = row[k]; });
      entries[key] = entry;
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

  /** "Odebrat z řady": forgets the programme (also its "Pokračovat" in the guide). */
  ResumeStore.prototype.remove = function (key) {
    var entries = this.entries();
    if (!entries[key]) return;
    delete entries[key];
    this.backend.save(entries);
  };

  /**
   * The Archiv row: [{key, title, showTitle, image, source, start, channel, progress, length, label}] of the entries
   * still on offer, in the row's order.
   */
  ResumeStore.prototype.continueRow = function () {
    var self = this;
    var entries = this.entries();
    var list = Object.keys(entries).filter(function (k) {
      var e = entries[k];
      return e && typeof e === 'object' && self.offer(k);
    }).map(function (k) {
      return { key: k, savedAt: entries[k].savedAt, title: entries[k].title };
    });
    return ResumeRules.continueRow(list).map(function (k) {
      var e = entries[k];
      var row = cleanMeta(e, k);
      return {
        key: k, title: row.title, showTitle: row.showTitle, image: row.image, source: row.source,
        start: row.start === undefined ? null : row.start, channel: row.channel || null,
        progress: e.progress, length: e.length, label: ResumeRules.label(e.progress)
      };
    });
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
