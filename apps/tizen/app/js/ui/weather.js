/*
 * Weather on Domů (spec/apps.md "Domů", T59): the header's weather now and block 8 "Počasí na týden" from the
 * gateway's answer (CT.Weather), and the TV's location for it (the platform's geolocation, CT.Platform.geolocation;
 * many Samsung TVs have none, then the gateway's household place). The location goes no further than one request.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var W = CT.Weather;
  var LOCATE_TIMEOUT_MS = 20000; // also covers a browser's permission prompt left open

  // Drawn here (no icon font, no images): stroke follows the icon's colour (app.css .wx-…)
  var CLOUD = 'M7 18h10a4 4 0 0 0 .6-7.96A5.5 5.5 0 0 0 7.1 9.1 4.5 4.5 0 0 0 7 18z';
  var CLOUD_HIGH = 'M7 14h10a3.5 3.5 0 0 0 .5-6.97A5 5 0 0 0 7.9 5.8 4 4 0 0 0 7 14z';
  var ICONS = {
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8"/>',
    partly: '<path class="wx-sun-part" d="M8.5 3v1.6M3 8.5h1.6M4.6 4.6l1.1 1.1M12.4 4.6l-1.1 1.1M5.6 11.2a3.5 3.5 0 1 1 5.6-4.2"/><path d="M9 20h8.5a3.5 3.5 0 0 0 .5-6.97A5 5 0 0 0 8.4 12.3 3.9 3.9 0 0 0 9 20z"/>',
    cloud: '<path d="' + CLOUD + '"/>',
    fog: '<path d="M4 9h16M3 13h18M5 17h14M8 21h8"/>',
    drizzle: '<path d="' + CLOUD_HIGH + '"/><path d="M8 17.5v.5M12 19v.5M16 17.5v.5M10 21.5v.5M14 21.5v.5"/>',
    rain: '<path d="' + CLOUD_HIGH + '"/><path d="M8.5 16.5l-1.5 4M12.5 16.5l-1.5 4M16.5 16.5l-1.5 4"/>',
    snow: '<path d="' + CLOUD_HIGH + '"/><path d="M8 17v4M6.3 18l3.4 2M6.3 20l3.4-2M16 17v4M14.3 18l3.4 2M14.3 20l3.4-2"/>',
    storm: '<path d="' + CLOUD_HIGH + '"/><path d="M12.5 14l-2.5 4h3.5l-2 4"/>'
  };

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }

  /** The icon of a gateway `icon` key (CT.Weather has already turned an unknown one into `cloud`). */
  function icon(name, title) {
    var span = el('span', 'wx-icon wx-' + name);
    span.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">' + (ICONS[name] || ICONS.cloud) + '</svg>';
    if (title) span.title = title;
    return span;
  }

  /** The attribution line; the platform's links (CT.Platform.attributionLinks) inside it as <a>, the rest text. */
  function attribution(text) {
    var line = el('div', 'wx-attribution');
    var links = (CT.Platform && CT.Platform.attributionLinks) || [];
    var rest = text;
    links.forEach(function (link) {
      var at = rest.indexOf(link.text);
      if (at < 0) return;
      if (at > 0) line.appendChild(document.createTextNode(rest.slice(0, at)));
      var a = el('a', 'wx-link', link.text);
      a.href = link.url;
      a.target = '_blank';
      a.rel = 'noopener';
      line.appendChild(a);
      rest = rest.slice(at + link.text.length);
    });
    if (rest) line.appendChild(document.createTextNode(rest));
    return line;
  }

  var WeatherView = {
    icon: icon,
    attribution: attribution,

    /**
     * The header's weather: icon, "8 °C, polojasno", the sun, then each only when present the air, pollen and one
     * line per warning still in force (in its colour), and the attribution. Null without weather.
     */
    now: function (w, now) {
      if (!w) return null;
      var box = el('div', 'home-weather');
      var head = box.appendChild(el('div', 'wx-now'));
      head.appendChild(icon(w.now.icon, w.now.text));
      head.appendChild(el('span', 'wx-temp', W.nowLine(w)));
      [W.sunLine(w), W.airLine(w), W.pollenLine(w)].forEach(function (line) {
        if (line) box.appendChild(el('div', 'wx-line', line));
      });
      W.currentWarnings(w, now).forEach(function (warning) { // in the gateway's order, never sorted
        box.appendChild(el('div', 'wx-warning' + (warning.level ? ' wx-' + warning.level : ''), W.warningLine(warning, now)));
      });
      if (w.attribution) box.appendChild(attribution(w.attribution));
      return box;
    },

    /** Block 8 "Počasí na týden": a card per day from today (not focusable); null without days. */
    week: function (w, today) {
      var days = w ? w.days.filter(function (d) { return d.date >= today; }) : [];
      if (!days.length) return null;
      var row = el('div', 'home-row home-row-weather');
      row.appendChild(el('div', 'home-heading', W.WEEK_TITLE));
      var strip = row.appendChild(el('div', 'wx-week'));
      days.forEach(function (d) {
        var card = strip.appendChild(el('div', 'wx-day'));
        card.appendChild(el('div', 'wx-day-label', W.dayLabel(d.date, today)));
        card.appendChild(icon(d.icon, d.text));
        var range = W.rangeLine(d);
        if (range) card.appendChild(el('div', 'wx-day-range', range));
        var rain = W.rainLine(d);
        if (rain) card.appendChild(el('div', 'wx-day-rain', rain));
      });
      return row;
    }
  };

  /**
   * One position from `geo` (navigator.geolocation or a stand-in) as [lat, lon], or null (refused, none, an error, too
   * slow). Nothing is kept: the caller rounds it and sends it once.
   */
  function locate(geo) {
    return new Promise(function (resolve) {
      var done = false;
      var timer = setTimeout(function () { finish(null); }, LOCATE_TIMEOUT_MS);
      function finish(value) {
        if (done) return;
        done = true;
        clearTimeout(timer);
        resolve(value);
      }
      try {
        geo.getCurrentPosition(function (p) {
          finish(p && p.coords ? [p.coords.latitude, p.coords.longitude] : null);
        }, function () {
          finish(null);
        }, { enableHighAccuracy: false, timeout: LOCATE_TIMEOUT_MS / 2, maximumAge: W.MAX_AGE_MS });
      } catch (e) {
        finish(null);
      }
    });
  }

  CT.WeatherView = WeatherView;
  CT.locate = locate;
})(window);
