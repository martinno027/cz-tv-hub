/*
 * "Anténa": client for the household gateway's /dvb/… API (spec/dvb-gateway.md).
 * Cleartext HTTP is only allowed to home-LAN, mDNS, Tailscale and loopback hosts.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var J = CT.Json;
  var DM = CT.DvbMessages;

  var DEFAULT_PORT = 8098;
  var MIN_KEY_LENGTH = 32;
  var COOKIE_NAME = 'cztvhub_key';

  function isPrivateHost(host) {
    var h = host.toLowerCase().replace(/^\[|\]$/g, '');
    if (h === 'localhost' || /\.(local|ts\.net|home\.arpa)$/.test(h)) return true;
    if (h.indexOf('.') < 0 && h.indexOf(':') < 0) return h.length > 0; // single-label LAN / MagicDNS name
    var ipv4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(h);
    if (ipv4) {
      var p = ipv4.slice(1).map(Number);
      if (p.some(function (n) { return n > 255; })) return false;
      var a = p[0], b = p[1];
      return a === 10 || a === 127 || (a === 192 && b === 168) || (a === 172 && b >= 16 && b <= 31) ||
        (a === 100 && b >= 64 && b <= 127) || (a === 169 && b === 254);
    }
    if (h.indexOf(':') >= 0) {
      return h === '::1' || /^(fd|fc|fe80)/.test(h); // ULA (incl. Tailscale fd7a:115c:a1e0::/48), link-local
    }
    return false;
  }

  /** Where the gateway is and its key. */
  function DvbConfig(baseUrl, key) {
    this.baseUrl = baseUrl;
    this.key = key;
  }

  DvbConfig.prototype.url = function (path) {
    return this.baseUrl + path;
  };

  DvbConfig.prototype.authHeaders = function () {
    return { 'Authorization': 'Bearer ' + this.key };
  };

  /** Samsung's AVPlay cannot send headers; the gateway accepts the same key as this cookie. */
  DvbConfig.prototype.cookie = function () {
    return COOKIE_NAME + '=' + this.key;
  };

  DvbConfig.DEFAULT_PORT = DEFAULT_PORT;
  DvbConfig.MIN_KEY_LENGTH = MIN_KEY_LENGTH;
  DvbConfig.isPrivateHost = isPrivateHost;

  /** `pi`, `pi.tail1234.ts.net`, `192.168.1.5:8098`, `http://…` → `http://host:port`; null if invalid. */
  DvbConfig.normalizeAddress = function (address) {
    var trimmed = String(address || '').trim().replace(/\/+$/, '');
    if (!trimmed) return null;
    var withScheme = trimmed.indexOf('://') >= 0 ? trimmed : 'http://' + trimmed;
    var m = /^([a-z]+):\/\/(\[[0-9a-f:.]+\]|[a-z0-9.-]+)(?::(\d{1,5}))?(?:[/?#].*)?$/i.exec(withScheme);
    if (!m) return null;
    var scheme = m[1].toLowerCase();
    if (scheme !== 'http' && scheme !== 'https') return null;
    var host = m[2];
    // LAN / tailnet only, whatever the scheme (spec/dvb-gateway.md: never exposed to the internet).
    if (!isPrivateHost(host)) return null;
    var port = m[3] ? Number(m[3]) : (scheme === 'https' ? 443 : DEFAULT_PORT);
    if (port < 1 || port > 65535) return null;
    return scheme + '://' + host.toLowerCase() + ':' + port;
  };

  /** A config from typed settings, or throws CtError with a Czech reason. */
  DvbConfig.fromSettings = function (address, key) {
    var base = DvbConfig.normalizeAddress(address);
    if (!base) throw new CT.CtError('address not allowed: ' + address, DM.HOST_NOT_ALLOWED);
    var k = String(key || '').trim();
    if (k.length < MIN_KEY_LENGTH) throw new CT.CtError('key too short', DM.KEY_TOO_SHORT);
    return new DvbConfig(base, k);
  };

  function ensureOk(status, body) {
    var root = J.parse(body);
    if (status === 401) throw new CT.CtError('gateway HTTP 401', DM.BAD_KEY, '401');
    if (status < 200 || status > 299) {
      throw new CT.CtError('gateway HTTP ' + status, J.str(root, 'error') || DM.GENERIC, String(status));
    }
    if (!root) throw new CT.CtError('gateway: not JSON', DM.GENERIC);
    return root;
  }

  function isObject(v) {
    return v !== null && typeof v === 'object' && !Array.isArray(v);
  }

  var RECORDING_STATES = ['completed', 'recording', 'scheduled'];

  var DvbParser = {
    parseChannels: function (status, body) {
      var root = ensureOk(status, body);
      return J.arr(root, 'channels').filter(isObject).map(function (o) {
        var id = J.str(o, 'id');
        if (!id) return null;
        var number = parseInt(J.str(o, 'number'), 10);
        return { id: id, number: isNaN(number) ? 0 : number, name: J.str(o, 'name') || id, iconPath: J.str(o, 'icon') };
      }).filter(Boolean).sort(function (a, b) { return a.number - b.number; });
    },

    parseEpg: function (status, body) {
      var root = ensureOk(status, body);
      return J.arr(root, 'events').filter(isObject).map(function (o) {
        var id = J.str(o, 'id');
        var channel = J.str(o, 'channel');
        var start = J.instant(o.start);
        var end = J.instant(o.end);
        if (!id || !channel || start === null || end === null) return null;
        var rec = J.obj(o, 'recording');
        var recId = J.str(rec, 'id');
        var state = J.str(rec, 'state');
        return {
          id: id,
          channelId: channel,
          title: J.str(o, 'title') || '',
          start: start,
          end: end,
          subtitle: J.str(o, 'subtitle') || '',
          description: J.str(o, 'description') || '',
          genre: J.str(o, 'genre') || '',
          recording: recId ? { id: recId, state: RECORDING_STATES.indexOf(state) >= 0 ? state : 'failed' } : null
        };
      }).filter(Boolean);
    },

    parseStatus: function (status, body) {
      var root = ensureOk(status, body);
      var retention = parseInt(J.str(root, 'retentionDays'), 10);
      return {
        enabled: J.bool(root, 'enabled'),
        retentionDays: isNaN(retention) ? 0 : retention,
        tuners: J.arr(root, 'tuners').filter(isObject).map(function (t) {
          return { name: J.str(t, 'name') || '', inUse: J.bool(t, 'inUse') };
        }),
        tvheadendReachable: J.bool(root, 'tvheadendReachable')
      };
    },

    /**
     * Ad breaks of a recording (spec/ad-breaks.md): entries without numeric start < end are dropped,
     * the rest sorted by start; confidence defaults to 1. "pending" gives [].
     */
    parseBreaks: function (status, body) {
      var root = ensureOk(status, body);
      var num = function (v) { return typeof v === 'number' && isFinite(v); };
      return J.arr(root, 'breaks').filter(isObject).filter(function (b) {
        return num(b.start) && num(b.end) && b.start < b.end;
      }).map(function (b) {
        return { start: b.start, end: b.end, confidence: num(b.confidence) ? b.confidence : 1 };
      }).sort(function (a, b) { return a.start - b.start; });
    },

    /** The gateway's Czech `error` text from a JSON error body, if any. */
    errorText: function (body) {
      return J.str(J.parse(body), 'error');
    }
  };

  /** `transport` as for CtApi. */
  function DvbClient(config, transport) {
    this.config = config;
    this.transport = transport;
  }

  DvbClient.prototype.get = function (path) {
    var headers = this.config.authHeaders();
    headers.Accept = 'application/json';
    return this.transport({ method: 'GET', url: this.config.url(path), headers: headers, body: null })
      .catch(function (e) {
        throw new CT.CtError('gateway ' + path + ' failed: ' + e, DM.UNREACHABLE);
      });
  };

  DvbClient.prototype.status = function () {
    return this.get('/dvb/status').then(function (r) { return DvbParser.parseStatus(r.status, r.body); });
  };

  DvbClient.prototype.channels = function () {
    return this.get('/dvb/channels').then(function (r) { return DvbParser.parseChannels(r.status, r.body); });
  };

  /** Events of a Prague broadcast day (05:00–05:00). */
  DvbClient.prototype.epg = function (day) {
    return this.get('/dvb/epg?date=' + day).then(function (r) { return DvbParser.parseEpg(r.status, r.body); });
  };

  DvbClient.prototype.breaks = function (recordingId) {
    return this.get('/dvb/recording/' + encodeURIComponent(recordingId) + '/breaks').then(function (r) {
      return DvbParser.parseBreaks(r.status, r.body);
    });
  };

  DvbClient.prototype.liveUrl = function (channelId) {
    return this.config.url('/dvb/live/' + encodeURIComponent(channelId) + '.m3u8');
  };

  DvbClient.prototype.recordingUrl = function (recordingId) {
    return this.config.url('/dvb/recording/' + encodeURIComponent(recordingId) + '.m3u8');
  };

  /**
   * Asks for a playlist once before the player opens it: this starts the gateway's session (a live
   * start blocks until the first segment) and turns 502/503 into the gateway's Czech text, which
   * AVPlay's errors do not carry.
   */
  DvbClient.prototype.warmUp = function (url) {
    var headers = this.config.authHeaders();
    return this.transport({ method: 'GET', url: url, headers: headers, body: null })
      .catch(function (e) {
        throw new CT.CtError('gateway ' + url + ' failed: ' + e, DM.UNREACHABLE);
      })
      .then(function (r) {
        if (r.status === 401) throw new CT.CtError('gateway HTTP 401', DM.BAD_KEY, '401');
        if (r.status < 200 || r.status > 299) {
          throw new CT.CtError('gateway HTTP ' + r.status, DvbParser.errorText(r.body) || CT.Messages.PLAYBACK,
            String(r.status));
        }
      });
  };

  function uniqueById(events) {
    var seen = {};
    return events.filter(function (e) {
      if (seen[e.id]) return false;
      seen[e.id] = true;
      return true;
    });
  }

  function byStart(a, b) {
    return a.start - b.start;
  }

  /** In-memory cache of the gateway's channels and EPG days (recording states change, so short-lived). */
  function DvbRepository(client, clock, freshForMs) {
    this.client = client;
    this.clock = clock || Date.now;
    this.freshForMs = freshForMs || 5 * 60 * 1000;
    this.channelCache = null;
    this.days = {};
  }

  DvbRepository.prototype.channels = function () {
    var self = this;
    var now = this.clock();
    if (this.channelCache && now - this.channelCache.at < 3600 * 1000) return Promise.resolve(this.channelCache.list);
    return this.client.channels().then(function (list) {
      self.channelCache = { at: now, list: list };
      return list;
    });
  };

  /** Events of `day` keyed by channel id, sorted by start, de-duplicated by id. */
  DvbRepository.prototype.day = function (day) {
    var self = this;
    var now = this.clock();
    var cached = this.days[day];
    if (cached && now - cached.at < this.freshForMs) return Promise.resolve(cached.plan);
    return this.client.epg(day).then(function (events) {
      var plan = {};
      uniqueById(events).forEach(function (e) {
        (plan[e.channelId] = plan[e.channelId] || []).push(e);
      });
      Object.keys(plan).forEach(function (k) { plan[k].sort(byStart); });
      self.days[day] = { at: now, plan: plan };
      return plan;
    });
  };

  /** {channelId: {current, next}}; the broadcast day starts at 05:00, so yesterday is consulted too. */
  DvbRepository.prototype.onAir = function (now) {
    var self = this;
    var today = CT.Days.today(now);
    var days = [CT.Days.addDays(today, -1), today];
    return Promise.all(days.map(function (d) {
      return self.day(d).then(function (plan) { return { plan: plan }; }, function (e) { return { error: e }; });
    })).then(function (results) {
      var loaded = results.filter(function (r) { return r.plan; });
      if (!loaded.length) throw results[0].error;
      var merged = {};
      loaded.forEach(function (r) {
        Object.keys(r.plan).forEach(function (ch) { merged[ch] = (merged[ch] || []).concat(r.plan[ch]); });
      });
      var result = {};
      Object.keys(merged).forEach(function (ch) {
        var sorted = uniqueById(merged[ch]).sort(byStart);
        var i = sorted.findIndex(function (e) { return e.start <= now && now < e.end; });
        result[ch] = i >= 0
          ? { current: sorted[i], next: sorted[i + 1] || null }
          : { current: null, next: sorted.filter(function (e) { return e.start > now; })[0] || null };
      });
      return result;
    });
  };

  /** The "Přeskočit reklamu" rule: inside a break and at least 3 s before its end → skip to the end. */
  var AdBreaks = {
    MIN_REMAINING_SECONDS: 3,
    skipTarget: function (breaks, position) {
      for (var i = 0; i < breaks.length; i++) {
        var b = breaks[i];
        if (b.start <= position && position < b.end - AdBreaks.MIN_REMAINING_SECONDS) return b.end;
      }
      return null;
    }
  };

  CT.AdBreaks = AdBreaks;
  CT.DvbConfig = DvbConfig;
  CT.DvbParser = DvbParser;
  CT.DvbClient = DvbClient;
  CT.DvbRepository = DvbRepository;
})(typeof window !== 'undefined' ? window : global);
