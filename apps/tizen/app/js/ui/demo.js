/*
 * Development only: `index.html?demo` in a desktop browser answers every request from the synthetic
 * fixtures in spec/fixtures (served from the repository root), moved to the requested day, so the UI can
 * be checked without a TV, CT or a gateway. Nothing in it plays (the fixture URLs are not real).
 * `?demo&channels=60` adds generated Anténa channels up to that count (for checking a big guide).
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

  function transport(request) {
    var url = request.url;
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
    return Promise.resolve({ status: 200, body: '#EXTM3U' });
  }

  /** Demo thumbnails: a generated test card per recording id (never a real broadcast still). */
  function blobTransport(request) {
    var m = /\/dvb\/recording\/([^/]+)\/thumb\.jpg$/.exec(request.url);
    if (!m) return Promise.resolve({ status: 404, blob: null, type: '' });
    var id = decodeURIComponent(m[1]);
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
    g.fillText('Ukázkový náhled', 20, 245);
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
    dvbConfig: function () {
      return new CT.DvbConfig('http://cztvhub.local:8098', KEY);
    }
  };
})(window);
