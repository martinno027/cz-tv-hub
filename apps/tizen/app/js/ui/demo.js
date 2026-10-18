/*
 * Development only: `index.html?demo` in a desktop browser answers every request from the synthetic
 * fixtures in spec/fixtures (served from the repository root), moved to the requested day, so the UI can
 * be checked without a TV, CT or a gateway. Nothing in it plays (the fixture URLs are not real).
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var FIXTURES = '../../../spec/fixtures/';
  var CT_DAY = '2026-10-16';
  var DVB_DAY = '2026-10-18';
  var DAY_MS = 24 * 3600 * 1000;
  var KEY = 'demo-key-demo-key-demo-key-demo-key';

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
    if (url.indexOf('/dvb/status') >= 0) return load('dvb/dvb_status.json');
    if (url.indexOf('/dvb/channels') >= 0) return load('dvb/dvb_channels.json');
    if (url.indexOf('/dvb/epg') >= 0) {
      var dvbDay = /date=([\d-]+)/.exec(url)[1];
      return load('dvb/dvb_epg_today.json').then(function (r) { return shifted(r, DVB_DAY, dvbDay); });
    }
    if (url.indexOf('/dvb/live/') >= 0) return Promise.resolve({ status: 503, body: '{"error": "Tuner je obsazený"}' });
    return Promise.resolve({ status: 200, body: '#EXTM3U' });
  }

  CT.Demo = {
    enabled: /[?&]demo\b/.test(root.location.search),
    transport: transport,
    dvbConfig: function () {
      return new CT.DvbConfig('http://cztvhub.local:8098', KEY);
    }
  };
})(window);
