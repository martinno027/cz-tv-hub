/*
 * Weather on Domů (spec/apps.md "Domů", T59) from the gateway's GET /dvb/weather (spec/dvb-gateway.md "Weather").
 * The gateway does the WMO, air-quality, pollen and warning rules; the app shows its texts as they come and only
 * formats numbers, days and times. The location is rounded here to 0.1° before it leaves the TV and kept in memory.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var J = CT.Json;

  var WEEKDAYS = ['ne', 'po', 'út', 'st', 'čt', 'pá', 'so']; // Date#getUTCDay order
  var LEVELS = ['yellow', 'orange', 'red'];
  var ICONS = ['sun', 'partly', 'cloud', 'fog', 'drizzle', 'rain', 'snow', 'storm'];

  function number(v) {
    return typeof v === 'number' && isFinite(v) ? v : null;
  }

  function text(o, key) {
    var v = o ? o[key] : null;
    if (typeof v !== 'string') return null;
    var t = v.trim();
    return t ? t : null;
  }

  function icon(o) {
    var name = text(o, 'icon');
    return ICONS.indexOf(name) >= 0 ? name : 'cloud';
  }

  function isObject(v) {
    return v !== null && typeof v === 'object' && !Array.isArray(v);
  }

  /**
   * `value` rounded to `decimals` places, half away from zero (-2.5 → -3, where Math.round gives -2), never -0. The
   * other apps round the shortest decimal text instead of the binary value; for every two-decimal coordinate in the
   * gateway's range (48.00–51.50, 11.50–19.50) both give the same (checked when written: x.x5 × 10 lands on the tie
   * exactly or above), so the simple form is kept.
   */
  function roundHalfAway(value, decimals) {
    var scale = Math.pow(10, decimals);
    var out = Math.round(Math.abs(value) * scale) / scale;
    return value < 0 && out !== 0 ? -out : out;
  }

  /** "07:13" → "7:13"; anything else as it came. */
  function clock(t) {
    return /^0\d:\d\d$/.test(t) ? t.slice(1) : t;
  }

  var Weather = {
    MAX_AGE_MS: 30 * 60 * 1000,
    RAIN_FROM: 20,
    // the gateway's range for lat/lon (outside: 400); the TV then asks without its location
    LAT: [48.0, 51.5],
    LON: [11.5, 19.5],

    CONSENT_TITLE: 'Počasí podle polohy',
    CONSENT_TEXT: 'Počasí na Domů podle polohy této televize: poloha se zaokrouhlí asi na 10 km, pošle se jen vaší ' +
      'bráně a ta podle ní zjistí počasí u Open-Meteo. Nikde se neukládá.',
    CONSENT_ALLOW: 'Povolit',
    CONSENT_DENY: 'Nepoužívat',
    SETTING_TITLE: 'Počasí podle polohy této televize',
    SETTING_NOTE: 'Poloha zaokrouhlená asi na 10 km jde jen vaší bráně; bez ní platí místo nastavené na bráně. ' +
      'Nikde se neukládá.',
    WEEK_TITLE: 'Počasí na týden',

    roundHalfAway: roundHalfAway,

    /**
     * The device's location as sent to the gateway: [lat, lon] rounded to 0.1°, or null for anything that is not a
     * finite number or lies outside the gateway's range (checked before rounding, as the gateway does).
     */
    round: function (lat, lon) {
      if (number(lat) === null || number(lon) === null) return null;
      if (lat < Weather.LAT[0] || lat > Weather.LAT[1] || lon < Weather.LON[0] || lon > Weather.LON[1]) return null;
      return [roundHalfAway(lat, 1), roundHalfAway(lon, 1)];
    },

    /** `/dvb/weather` or `/dvb/weather?lat=50.1&lon=14.4` (`place` from round()). */
    path: function (place) {
      if (!place) return '/dvb/weather';
      return '/dvb/weather?lat=' + place[0].toFixed(1) + '&lon=' + place[1].toFixed(1);
    },

    /** Domů asks again: never asked, or the last answer (with weather or without) is older than 30 min. */
    due: function (answeredAt, now) {
      return answeredAt === null || answeredAt === undefined || now - answeredAt >= Weather.MAX_AGE_MS || now < answeredAt;
    },

    /**
     * The weather to show, or null: a 404 ("Počasí není zapnuté"), an answer with `error` or without `now` (Domů then
     * shows the date only). Unknown fields are ignored, missing optional ones tolerated. A 401 or another failure
     * throws (CT.DvbParser's errors) and is shown the same way, as nothing.
     */
    parse: function (status, body) {
      if (status === 404) return null;
      var o = CT.DvbParser.ensureOk(status, body);
      var now = J.obj(o, 'now');
      if (text(o, 'error') || !now || number(now.temperature) === null) return null;
      var sun = J.obj(o, 'sun');
      var air = J.obj(o, 'air');
      return {
        now: { temperature: now.temperature, text: text(now, 'text'), icon: icon(now) },
        sun: text(sun, 'rise') && text(sun, 'set') ? { rise: text(sun, 'rise'), set: text(sun, 'set') } : null,
        days: J.arr(o, 'days').filter(isObject).map(function (d) {
          var date = text(d, 'date');
          if (!date || !/^\d{4}-\d\d-\d\d$/.test(date)) return null;
          var chance = number(d.rainChance);
          return { date: date, text: text(d, 'text'), icon: icon(d), max: number(d.max), min: number(d.min),
            rainChance: chance === null ? null : Math.round(chance) };
        }).filter(Boolean),
        aqiText: text(air, 'aqiText'),
        pollen: J.arr(air, 'pollen').filter(isObject).map(function (p) {
          return text(p, 'name') && text(p, 'level') ? { name: text(p, 'name'), level: text(p, 'level') } : null;
        }).filter(Boolean),
        warnings: J.arr(o, 'warnings').filter(isObject).map(function (w) {
          if (!text(w, 'event')) return null;
          var level = text(w, 'level');
          // an undocumented level stays unknown (shown without a colour)
          return { level: LEVELS.indexOf(level) >= 0 ? level : null, event: text(w, 'event'), to: J.instant(w.to) };
        }).filter(Boolean),
        attribution: text(o, 'attribution')
      };
    },

    /** Whole degrees, half away from zero, never "-0". */
    degrees: function (value) {
      return String(roundHalfAway(value, 0));
    },

    /** "8 °C, polojasno" (the gateway's text with a lower-case first letter); "8 °C" without a text. */
    nowLine: function (w) {
      var t = Weather.degrees(w.now.temperature) + ' °C';
      var name = w.now.text;
      return name ? t + ', ' + name.charAt(0).toLowerCase() + name.slice(1) : t;
    },

    /** "Slunce 7:13–16:19", or null. */
    sunLine: function (w) {
      return w.sun ? 'Slunce ' + clock(w.sun.rise) + '–' + clock(w.sun.set) : null;
    },

    /** "Ovzduší: zhoršené", or null. */
    airLine: function (w) {
      return w.aqiText ? 'Ovzduší: ' + w.aqiText : null;
    },

    /** "Pyl: bříza střední, tráva nízká", or null. */
    pollenLine: function (w) {
      if (!w.pollen.length) return null;
      return 'Pyl: ' + w.pollen.map(function (p) { return p.name + ' ' + p.level; }).join(', ');
    },

    /** The warnings still in force at `now` (a cached answer may hold one that has just ended). */
    currentWarnings: function (w, now) {
      return w.warnings.filter(function (x) { return x.to === null || x.to > now; });
    },

    /**
     * "Výstraha ČHMÚ: Silný vítr do 18:00" (`to` in Prague time; on another day than `now`'s "do po 16. 11. 18:00");
     * without an end (fire risk, ozone) "Výstraha ČHMÚ: Riziko požárů (do odvolání)".
     */
    warningLine: function (warning, now) {
      var line = 'Výstraha ČHMÚ: ' + warning.event;
      if (warning.to === null) return line + ' (do odvolání)';
      var day = CT.Days.today(warning.to);
      var date = day === CT.Days.today(now) ? '' : Weather.weekdayDate(day) + ' ';
      return line + ' do ' + date + CT.Days.clock(warning.to);
    },

    /** "út 4. 11." of a day 'YYYY-MM-DD'. */
    weekdayDate: function (day) {
      var p = day.split('-').map(Number);
      var d = new Date(Date.UTC(p[0], p[1] - 1, p[2]));
      return WEEKDAYS[d.getUTCDay()] + ' ' + p[2] + '. ' + p[1] + '.';
    },

    /** "Dnes" for today (Prague), else "út 4. 11." (no "Zítra" on the week cards). */
    dayLabel: function (day, today) {
      return day === today ? 'Dnes' : Weather.weekdayDate(day);
    },

    /** "9° / 2°"; one alone when the other is missing, null without both. */
    rangeLine: function (day) {
      var parts = [day.max, day.min].filter(function (v) { return v !== null; });
      if (!parts.length) return null;
      return parts.map(function (v) { return Weather.degrees(v) + '°'; }).join(' / ');
    },

    /** "srážky 60 %" from 20 %, else null. */
    rainLine: function (day) {
      return day.rainChance !== null && day.rainChance >= Weather.RAIN_FROM ? 'srážky ' + day.rainChance + ' %' : null;
    }
  };

  /**
   * Domů's weather: one request at a time, asked again only when Domů is shown and the last answer is older than
   * 30 min (Weather.due), nothing in the background. `opts`: client() → CT.DvbClient or null (no gateway = no
   * weather), locate() → Promise of [lat, lon] (raw) or null (no location: the gateway's household place), clock.
   * `weather` is the parsed answer or null; `onChange` after every answer.
   */
  function WeatherSource(opts) {
    this.client = opts.client;
    this.locate = opts.locate;
    this.clock = opts.clock || Date.now;
    this.weather = null;
    this.answeredAt = null;
    this.busy = null;
    this.generation = 0;
    this.onChange = null;
  }

  /** Domů is shown: ask when due (`force` = even when not, e.g. the location switch changed). */
  WeatherSource.prototype.refresh = function (force) {
    var self = this;
    var client = this.client();
    if (!client) return Promise.resolve(null);
    if (this.busy) return this.busy;
    if (!force && !Weather.due(this.answeredAt, this.clock())) return Promise.resolve(this.weather);
    var generation = this.generation;
    this.busy = Promise.resolve().then(function () {
      return self.locate();
    }).then(function (raw) {
      return raw ? Weather.round(raw[0], raw[1]) : null;
    }, function () {
      return null;
    }).then(function (place) {
      return client.get(Weather.path(place));
    }).then(function (r) {
      return Weather.parse(r.status, r.body);
    }).catch(function () {
      return null; // unreachable, 401, not JSON: the date only, asked again after 30 min
    }).then(function (w) {
      self.busy = null;
      if (generation !== self.generation) return null; // the gateway changed meanwhile
      self.weather = w;
      self.answeredAt = self.clock();
      if (self.onChange) self.onChange(w);
      return w;
    });
    return this.busy;
  };

  /** Another gateway (or none), or the location switch changed: forget the answer, ask on the next show. */
  WeatherSource.prototype.reset = function () {
    this.generation++;
    this.busy = null;
    this.weather = null;
    this.answeredAt = null;
  };

  CT.Weather = Weather;
  CT.WeatherSource = WeatherSource;
})(typeof window !== 'undefined' ? window : global);
