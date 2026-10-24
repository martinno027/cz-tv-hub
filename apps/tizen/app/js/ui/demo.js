/*
 * Development only: `index.html?demo` in a desktop browser answers every request from the synthetic
 * fixtures in spec/fixtures (served from the repository root), moved to the requested day, so the UI can
 * be checked without a TV, CT or a gateway. Nothing in it plays (the fixture URLs are not real).
 * `?demo&channels=60` adds generated Anténa channels up to that count (for checking a big guide).
 * Poslanecká sněmovna: synthetic playlists (stream 1 on, the others "not processed now" 404), and playback gets a
 * local URL that does not exist, so the demo never contacts the Chamber's CDN.
 * `?demo&psp=13` = streams 1 and 3 on; `?demo&psp=0` = none on.
 * "Profil": an in-memory fake of the gateway's profile API (forgotten on reload, so a TV signed in before the reload
 * gets the 401 path); profile "Demo" with password "1234" exists (ČT2 hidden, thumbnails off).
 * `?demo&profile=offline` = reading and saving a profile fail as if the gateway were down (login still works).
 * "Kanály" household default: /dvb/defaults answers from memory (ČT24 first, ČT art hidden, version 1);
 * `?demo&defaults=none` = no default (version 0). Change CT.Demo.defaults from the console and open Nastavení.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var FIXTURES = '../../../spec/fixtures/';
  var CT_DAY = '2026-10-16';
  var DVB_DAY = '2026-10-18';
  var DAY_MS = 24 * 3600 * 1000;
  var KEY = 'demo-key-demo-key-demo-key-demo-key';
  var EXTRA = (function () {
    var m = /[?&]channels=(\d+)/.exec(root.location.search);
    return m ? Math.min(300, Number(m[1])) : 0;
  })();
  var SLOTS = [15, 20, 25, 30, 45, 60, 90];
  var PSP_ON = (function () {
    var m = /[?&]psp=(\d+)/.exec(root.location.search);
    return m ? m[1] : '1';
  })();
  var PSP_LIVE = '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720\nchunklist.m3u8\n';

  /** The synthetic answer for a Chamber playlist URL (never fetched from the CDN in the demo). */
  function pspPlaylist(url) {
    var m = /\/ps-stream(\d)\//.exec(url);
    var n = m ? m[1] : '?';
    if (PSP_ON.indexOf(n) >= 0) return { status: 200, body: PSP_LIVE };
    return { status: 404, body: 'NOT_FOUND: channel ps-stream' + n + ' is not processed now' };
  }
  var TITLES = ['Zprávy', 'Pohádka', 'Sportovní přenos', 'Film: Testovací obrazec', 'Soutěž', 'Hudební klip',
    'Dokument o anténách', 'Kulturní magazín'];

  function extraIds(count) {
    var ids = [];
    for (var n = count; n < EXTRA; n++) ids.push('demo' + ('000' + n).slice(-4));
    return ids;
  }

  /** Generated channels after the fixture's ones, up to `?channels=`. */
  function withExtraChannels(response) {
    var body = JSON.parse(response.body);
    extraIds(body.channels.length).forEach(function (id, i) {
      body.channels.push({ id: id, number: body.channels.length + 1, name: 'Test ' + (body.channels.length + 1), icon: null });
    });
    return { status: response.status, body: JSON.stringify(body) };
  }

  /** A whole broadcast day of generated events for every generated channel. */
  function withExtraEvents(response, day, fixtureChannels) {
    var body = JSON.parse(response.body);
    var from = Date.parse(day + 'T03:00:00Z');
    extraIds(fixtureChannels).forEach(function (id, c) {
      var slot = SLOTS[c % SLOTS.length] * 60 * 1000;
      for (var t = from, k = 0; t < from + DAY_MS; t += slot, k++) {
        body.events.push({ id: id + '-' + k, channel: id, title: TITLES[(k + c) % TITLES.length], subtitle: 'Díl ' + k,
          description: 'Generovaný pořad ukázky.', genre: '', start: new Date(t).toISOString(),
          end: new Date(t + slot).toISOString(), recording: null });
      }
    });
    return { status: response.status, body: JSON.stringify(body) };
  }

  function load(name) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open('GET', FIXTURES + name, true);
      xhr.onload = function () {
        if (xhr.status !== 200) return reject(new Error('no fixture ' + name));
        var json = JSON.parse(xhr.responseText);
        resolve({ status: json.status, body: JSON.stringify(json.body) });
      };
      xhr.onerror = function () { reject(new Error('network')); };
      xhr.send();
    });
  }

  /** Shifts every ISO timestamp in the body by whole days. */
  function shifted(response, fromDay, toDay) {
    var delta = Date.UTC.apply(null, toDay.split('-').map(Number).map(function (v, i) { return i === 1 ? v - 1 : v; })) -
      Date.UTC.apply(null, fromDay.split('-').map(Number).map(function (v, i) { return i === 1 ? v - 1 : v; }));
    var body = response.body.replace(/"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)"/g, function (_, iso) {
      return '"' + new Date(Date.parse(iso) + delta).toISOString() + '"';
    });
    return { status: response.status, body: body };
  }

  var PROFILE_OFFLINE = /[?&]profile=offline\b/.test(root.location.search);
  var profiles = [{ name: 'Demo', password: '1234', version: 1, tokens: [],
    settings: { v: 1, channelPrefs: { order: [], hidden: ['ct:CH_2'], sourcesOff: [] }, thumbnails: false } }];
  var defaults = /[?&]defaults=none\b/.test(root.location.search)
    ? { channelPrefs: null, version: 0, updated: null }
    : { channelPrefs: { order: ['ct:CH_24', 'ct:CH_1'], hidden: ['ct:CH_6'], sourcesOff: [] }, version: 1,
      updated: '2026-10-24T08:00:00Z' };
  var loginFailures = {}; // name key → [times]
  var tokenCount = 0;

  function answer(status, body) {
    return new Promise(function (resolve) {
      setTimeout(function () { resolve({ status: status, body: body === null ? '' : JSON.stringify(body) }); }, 300);
    });
  }

  function profileByKey(key) {
    return profiles.filter(function (p) { return CT.Profile.nameKey(p.name) === key; })[0] || null;
  }

  function signedInAnswer(p, status) {
    var token = 'demo-token-' + (++tokenCount);
    p.tokens.push(token);
    return answer(status, { token: token, name: p.name, settings: p.settings, version: p.version });
  }

  /** The gateway's /dvb/profiles… routes (spec/dvb-gateway.md "Profiles"), with its Czech error texts. */
  function profileApi(request) {
    var url = request.url;
    var body = CT.Json.parse(request.body || '') || {};
    var token = (request.headers || {})['X-Cztvhub-Profile'];
    var owner = profiles.filter(function (p) { return p.tokens.indexOf(token) >= 0; })[0] || null;
    if (/\/dvb\/profiles$/.test(url) && request.method === 'POST') {
      var name = CT.Profile.normalizeName(body.name);
      if (name === null || !CT.Profile.validPassword(body.password)) return answer(400, { error: 'Neplatné jméno nebo heslo' });
      if (profileByKey(CT.Profile.nameKey(name))) return answer(409, { error: 'Profil s tímto jménem už existuje' });
      if (profiles.length >= 20) return answer(507, { error: 'Profilů je už 20, další se nevejde' });
      var created = { name: name, password: body.password, version: 1, tokens: [], settings: body.settings || { v: 1 } };
      profiles.push(created);
      return signedInAnswer(created, 201);
    }
    if (/\/dvb\/profiles\/login$/.test(url) && request.method === 'POST') {
      var key = CT.Profile.nameKey(body.name) || '';
      var now = Date.now();
      var recent = (loginFailures[key] || []).filter(function (t) { return now - t < 10 * 60 * 1000; });
      loginFailures[key] = recent;
      if (recent.length >= 5 && now - recent[recent.length - 1] < 60 * 1000) {
        return answer(429, { error: 'Příliš mnoho pokusů, zkuste to za minutu' });
      }
      var p = profileByKey(key);
      if (!p || p.password !== body.password) {
        recent.push(now);
        return answer(401, { error: 'Nesprávné jméno nebo heslo' });
      }
      return signedInAnswer(p, 200);
    }
    if (!owner) return answer(401, { error: 'Profil byl odhlášen', code: 'profileSignedOut' });
    if (/\/dvb\/profile\/logout$/.test(url)) {
      owner.tokens = owner.tokens.filter(function (t) { return t !== token; });
      return answer(204, null);
    }
    if (PROFILE_OFFLINE) return Promise.reject(new Error('network error'));
    if (request.method === 'PUT') {
      owner.settings = body.settings;
      owner.version += 1;
      return answer(200, { version: owner.version, updated: new Date().toISOString() });
    }
    return answer(200, { name: owner.name, settings: owner.settings, version: owner.version, updated: new Date().toISOString() });
  }

  function transport(request) {
    var url = request.url;
    if (/\/dvb\/profiles?(\/|$)/.test(url)) return profileApi(request);
    if (/\/dvb\/defaults$/.test(url)) return answer(200, defaults);
    if (url.indexOf('pspcr-ott-live.ssl.cdn.cra.cz') >= 0) {
      return new Promise(function (resolve) { setTimeout(function () { resolve(pspPlaylist(url)); }, 300); });
    }
    if (url.indexOf('/graphql/') >= 0) {
      var date = JSON.parse(request.body).variables.date.split('.'); // MM.DD.YYYY
      var day = date[2] + '-' + date[0] + '-' + date[1];
      return load('schedule_ct1_ct24.json').then(function (r) { return shifted(r, CT_DAY, day); });
    }
    if (url.indexOf('/stream-data/channel/') >= 0) return load('live_CH_1.json');
    if (url.indexOf('/media/external/') >= 0) return load(/1$/.test(url) ? 'vod_drm.json' : 'vod_no_licence.json');
    if (url.indexOf('/dvb/status') >= 0) {
      return load('dvb/dvb_status.json').then(function (r) {
        var body = JSON.parse(r.body);
        body.thumbnails = true; // the demo "gateway" offers thumbnails (synthetic, see blobTransport)
        return { status: r.status, body: JSON.stringify(body) };
      });
    }
    if (url.indexOf('/dvb/channels') >= 0) {
      return load('dvb/dvb_channels.json').then(function (r) { return EXTRA ? withExtraChannels(r) : r; });
    }
    if (url.indexOf('/dvb/epg') >= 0) {
      var dvbDay = /date=([\d-]+)/.exec(url)[1];
      return Promise.all([load('dvb/dvb_epg_today.json'), load('dvb/dvb_channels.json')]).then(function (r) {
        var events = shifted(r[0], DVB_DAY, dvbDay);
        return EXTRA ? withExtraEvents(events, dvbDay, JSON.parse(r[1].body).channels.length) : events;
      });
    }
    if (url.indexOf('/dvb/live/') >= 0) return Promise.resolve({ status: 503, body: '{"error": "Tuner je obsazený"}' });
    var tl = /\/dvb\/timeline\/([^/.?]+)$/.exec(url);
    if (tl) return timeline(decodeURIComponent(tl[1]));
    if (/\/dvb\/recording\/b[^/]+\/breaks$/.test(url)) {
      // one break in "Zprávy" of the sample timeline (positions from its start − 30 s), to see "Přeskočit reklamu"
      return Promise.resolve({ status: 200, body: '{"state": "done", "breaks": [{"start": 400, "end": 700}]}' });
    }
    return Promise.resolve({ status: 200, body: '#EXTM3U' });
  }

  var TIMELINE_GROWING = /[?&]timeline=growing\b/.test(root.location.search);

  /**
   * spec/fixtures/dvb/dvb_timeline.json moved so that its window ends an hour ago (`?timeline=growing`: ends now and
   * keeps growing); its `previous` id answers the same window 6 h earlier, whose `next` leads back.
   */
  function timeline(id) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open('GET', FIXTURES + 'dvb/dvb_timeline.json', true);
      xhr.onload = function () {
        if (xhr.status !== 200) return reject(new Error('no timeline fixture'));
        var body = JSON.parse(xhr.responseText);
        var last = body.pieces[body.pieces.length - 1];
        var end = Date.parse(last.time) + last.duration * 1000;
        var now = Date.now();
        var delta = (TIMELINE_GROWING ? now : now - 3600 * 1000) - end;
        if (TIMELINE_GROWING) body.complete = false;
        if (id === body.previous) {
          delta -= 6 * 3600 * 1000;
          body.next = body.id;
          body.previous = null;
          body.id = id;
          body.complete = true;
        } else if (id !== body.id) {
          return resolve({ status: 404, body: '{"error": "Nahrávka není k dispozici"}' });
        }
        var shiftedBody = JSON.stringify(body).replace(/"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)"/g, function (_, iso) {
          return '"' + new Date(Date.parse(iso) + delta).toISOString() + '"';
        });
        resolve({ status: 200, body: shiftedBody });
      };
      xhr.onerror = function () { reject(new Error('network')); };
      xhr.send();
    });
  }

  /**
   * A simulated player for timeline targets (nothing plays in the demo): the position runs with the clock, a
   * growing window grows with it, and the end of a finished window reports "stream completed".
   */
  function DemoBackend(cb) {
    this.cb = cb;
    this.target = null;
    this.base = 0;
    this.since = 0;
    this.playing = false;
    this.endedTimer = null;
  }

  DemoBackend.prototype.limit = function () {
    var tl = this.target.timeline;
    var d = CT.Timeline.duration(tl.pieces) * 1000;
    return tl.complete ? d : d + (Date.now() - this.openedAt);
  };

  DemoBackend.prototype.open = function (target, startMs) {
    var self = this;
    this.close();
    this.target = target;
    this.openedAt = Date.now();
    this.base = Math.max(0, startMs || 0);
    this.since = Date.now();
    this.playing = true;
    this.endedTimer = setInterval(function () {
      if (self.playing && self.target.timeline.complete && self.position() >= self.limit()) {
        self.base = self.limit();
        self.playing = false;
        self.cb.onEnded();
      }
    }, 250);
    setTimeout(function () { if (self.target === target) self.cb.onReady(); }, 300);
  };

  DemoBackend.prototype.close = function () {
    clearInterval(this.endedTimer);
    this.target = null;
    this.playing = false;
  };

  DemoBackend.prototype.position = function () {
    if (!this.target) return 0;
    var p = this.base + (this.playing ? Date.now() - this.since : 0);
    return Math.min(p, this.limit());
  };

  DemoBackend.prototype.duration = function () {
    return this.target ? this.limit() : 0;
  };

  DemoBackend.prototype.paused = function () {
    return !!this.target && !this.playing;
  };

  DemoBackend.prototype.togglePause = function () {
    this.base = this.position();
    this.since = Date.now();
    this.playing = !this.playing;
  };

  DemoBackend.prototype.seekTo = function (ms) {
    this.base = Math.max(0, Math.min(ms, this.limit() - (this.target.timeline.complete ? 0 : 10000)));
    this.since = Date.now();
  };

  DemoBackend.prototype.seekBy = function (deltaMs) {
    this.seekTo(this.position() + deltaMs);
  };

  DemoBackend.prototype.audioTracks = function () {
    return [{ index: 1, language: 'cs' }, { index: 2, language: 'en' }, { index: 3, language: 'de' }];
  };

  DemoBackend.prototype.currentAudio = function () {
    return this.audio || 1;
  };

  DemoBackend.prototype.selectAudio = function (index) {
    this.audio = index;
    return true;
  };

  /** `?demo&timeline`: opens the sample timeline on the Anténa channel named Nova once the channels are there. */
  function afterStart(app) {
    if (!/[?&]timeline\b/.test(root.location.search)) return;
    var tries = 0;
    var timer = setInterval(function () {
      var channels = app.dvbChannels || [];
      if (!channels.length && ++tries < 20) return;
      clearInterval(timer);
      var nova = channels.filter(function (c) { return /nova/i.test(c.name); })[0] || channels[0];
      app.play({
        kind: 'dvbRecorded', channelId: nova ? nova.id : 'nova1', title: 'Zprávy',
        recordingId: 'bnova1-1792832400-1792834200', fromStart: true,
        resumeKey: 'dvb:bnova1-1792832400-1792834200', lengthSeconds: 1800
      });
    }, 250);
  }

  /**
   * Demo images: a generated test card per recording id or fixture image URL (never a real broadcast still or
   * a request to CT's CDN; the fixtures' image hosts do not exist).
   */
  function blobTransport(request) {
    var m = /\/dvb\/recording\/([^/]+)\/thumb\.jpg$/.exec(request.url);
    var id = m ? decodeURIComponent(m[1]) : request.url;
    var canvas = document.createElement('canvas');
    canvas.width = 480;
    canvas.height = 270;
    var g = canvas.getContext('2d');
    var hue = 0;
    for (var i = 0; i < id.length; i++) hue = (hue * 31 + id.charCodeAt(i)) % 360;
    var bars = ['#c0c0c0', '#c0c000', '#00c0c0', '#00c000', '#c000c0', '#c00000', '#0000c0'];
    bars.forEach(function (c, i) {
      g.fillStyle = c;
      g.fillRect(i * 480 / bars.length, 0, 480 / bars.length + 1, 200);
    });
    g.fillStyle = 'hsl(' + hue + ', 60%, 30%)';
    g.fillRect(0, 200, 480, 70);
    g.fillStyle = '#fff';
    g.font = 'bold 28px sans-serif';
    g.fillText(m ? 'Ukázkový náhled' : 'Ukázkový obrázek ČT', 20, 245);
    return new Promise(function (resolve) {
      setTimeout(function () {
        canvas.toBlob(function (blob) { resolve({ status: 200, blob: blob, type: 'image/jpeg' }); }, 'image/jpeg', 0.8);
      }, 150);
    });
  }

  CT.Demo = {
    enabled: /[?&]demo\b/.test(root.location.search),
    transport: transport,
    blobTransport: blobTransport,
    backend: function (cb) { return new DemoBackend(cb); },
    afterStart: afterStart,
    /** The fake gateway's profiles, to play "another TV" from the browser console (change settings, version += 1). */
    profiles: profiles,
    /** The fake gateway's household default, e.g. `CT.Demo.defaults.channelPrefs = null; CT.Demo.defaults.version += 1`. */
    defaults: defaults,
    /** Playback URL of a Chamber stream in the demo: local and absent (nothing plays, nothing leaves the PC). */
    pspPlayUrl: function (stream) {
      return 'demo-psp-' + stream.id + '.m3u8';
    },
    dvbConfig: function () {
      return new CT.DvbConfig('http://cztvhub.local:8098', KEY);
    }
  };
})(window);
