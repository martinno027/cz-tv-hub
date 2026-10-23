/*
 * "Anténa" thumbnails in the guide detail (spec/apps.md "Anténa thumbnails", spec/dvb-gateway.md "Thumbnails",
 * approved 2026-10-23): one still per recording from the gateway, shown only when the gateway and this TV both
 * have them on. Kept in memory only (object URLs of blobs; evicted ones are revoked), never in any storage;
 * fetched around the guide focus by CT.ImageLoader (prefetch.js).
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var Thumbs = {
    MAX_ENTRIES: 20,
    /** A growing recording's still is asked again after this (the gateway refreshes early ones). */
    REFRESH_MS: 60 * 1000,

    /** Recording id of an Anténa entry whose thumbnail may exist (completed or still recording), else null. */
    recordingId: function (entry) {
      if (!entry || entry.source !== 'dvb' || !entry.item) return null;
      var rec = entry.item.recording;
      if (!rec || !rec.id) return null;
      return rec.state === 'completed' || rec.state === 'recording' ? rec.id : null;
    },

    /** What to load for the focused entry: a recording id, or null (switched off or nothing to show). */
    wanted: function (serverOn, tvOn, entry) {
      return serverOn === true && tvOn !== false ? Thumbs.recordingId(entry) : null;
    },

    path: function (recordingId) {
      return '/dvb/recording/' + encodeURIComponent(recordingId) + '/thumb.jpg';
    }
  };

  /**
   * Small LRU of object URLs: get(id, now) → url | null; put(id, url, now, final, size). At most `max` entries
   * and `maxBytes` of blobs (sizes as given to put). `revoke(url)` is called for every URL that leaves the
   * cache (eviction, replacement, clear), so blobs do not pile up in memory.
   */
  function ThumbCache(max, revoke, maxBytes) {
    this.max = max || Thumbs.MAX_ENTRIES;
    this.maxBytes = maxBytes || Infinity;
    this.bytes = 0;
    this.revoke = revoke || function () {};
    this.items = []; // [{id, url, at, final, size}], oldest first
  }

  ThumbCache.prototype.indexOf = function (id) {
    for (var i = 0; i < this.items.length; i++) if (this.items[i].id === id) return i;
    return -1;
  };

  ThumbCache.prototype.get = function (id, now) {
    var i = this.indexOf(id);
    if (i < 0) return null;
    var item = this.items[i];
    if (!item.final && now - item.at >= Thumbs.REFRESH_MS) return null;
    this.items.splice(i, 1);
    this.items.push(item);
    return item.url;
  };

  ThumbCache.prototype.put = function (id, url, now, final, size) {
    var i = this.indexOf(id);
    if (i >= 0) {
      var old = this.items.splice(i, 1)[0];
      this.bytes -= old.size;
      if (old.url !== url) this.revoke(old.url);
    }
    this.items.push({ id: id, url: url, at: now, final: !!final, size: size || 0 });
    this.bytes += size || 0;
    while (this.items.length > 1 && (this.items.length > this.max || this.bytes > this.maxBytes)) {
      var gone = this.items.shift();
      this.bytes -= gone.size;
      this.revoke(gone.url);
    }
  };

  /** Drops every entry, or only those whose id `match(id)` accepts. */
  ThumbCache.prototype.clear = function (match) {
    var self = this;
    this.items = this.items.filter(function (item) {
      if (match && !match(item.id)) return true;
      self.bytes -= item.size;
      self.revoke(item.url);
      return false;
    });
  };

  CT.Thumbs = Thumbs;
  CT.ThumbCache = ThumbCache;
})(typeof window !== 'undefined' ? window : global);
