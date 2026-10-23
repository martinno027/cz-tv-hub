/*
 * Broadcast days and Czech labels. CT plans its days in Prague time, whatever the TV's zone.
 * Days are 'YYYY-MM-DD' strings, moments are epoch milliseconds. Prague offsets follow the EU rule
 * (summer time from the last Sunday of March to the last Sunday of October, 01:00 UTC), so no
 * time-zone database is needed on the TV.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var HOUR = 3600 * 1000;
  var DAY = 24 * HOUR;
  var WEEKDAYS = ['ne', 'po', 'út', 'st', 'čt', 'pá', 'so']; // Date#getUTCDay order

  function pad2(n) {
    return n < 10 ? '0' + n : String(n);
  }

  /** 01:00 UTC on the last Sunday of `month` (0-based). */
  function lastSunday(year, month) {
    var last = new Date(Date.UTC(year, month + 1, 0));
    return Date.UTC(year, month, last.getUTCDate() - last.getUTCDay(), 1);
  }

  function offsetMs(ms) {
    var year = new Date(ms).getUTCFullYear();
    return ms >= lastSunday(year, 2) && ms < lastSunday(year, 9) ? 2 * HOUR : HOUR;
  }

  /** A Date whose UTC fields are the Prague wall-clock fields of `ms`. */
  function local(ms) {
    return new Date(ms + offsetMs(ms));
  }

  function parseDay(day) {
    var p = day.split('-');
    return Date.UTC(+p[0], +p[1] - 1, +p[2]);
  }

  function formatDay(utcMidnight) {
    var d = new Date(utcMidnight);
    return d.getUTCFullYear() + '-' + pad2(d.getUTCMonth() + 1) + '-' + pad2(d.getUTCDate());
  }

  var Days = {
    DAYS_BACK: 7,
    DAYS_AHEAD: 2,
    HOUR: HOUR,

    today: function (now) {
      var d = local(now);
      return formatDay(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
    },

    addDays: function (day, n) {
      return formatDay(parseDay(day) + n * DAY);
    },

    /** Days offered in the guide's day picker, oldest first. */
    guideDays: function (today) {
      var days = [];
      for (var i = -Days.DAYS_BACK; i <= Days.DAYS_AHEAD; i++) days.push(Days.addDays(today, i));
      return days;
    },

    label: function (day, today) {
      if (day === today) return 'Dnes';
      if (day === Days.addDays(today, -1)) return 'Včera';
      if (day === Days.addDays(today, 1)) return 'Zítra';
      var d = new Date(parseDay(day));
      return WEEKDAYS[d.getUTCDay()] + ' ' + d.getUTCDate() + '. ' + (d.getUTCMonth() + 1) + '.';
    },

    time: function (ms) {
      var d = local(ms);
      return pad2(d.getUTCHours()) + ':' + pad2(d.getUTCMinutes());
    },

    timeRange: function (start, end) {
      return Days.time(start) + '–' + Days.time(end);
    },

    /** Moment of Prague wall-clock `minutes` after midnight on `day`. */
    atMinute: function (day, minutes) {
      var wall = parseDay(day) + minutes * 60 * 1000;
      return wall - offsetMs(wall - HOUR);
    },

    /**
     * The broadcast day as [start, end): 05:00 to 05:00 Prague, 23 or 25 h on DST days
     * (spec/apps.md "Across days": the guide's time axis is exactly this).
     */
    bounds: function (day) {
      return [Days.atMinute(day, 5 * 60), Days.atMinute(Days.addDays(day, 1), 5 * 60)];
    },

    /** Minutes after Prague midnight of `ms`. */
    minuteOfDay: function (ms) {
      var d = local(ms);
      return d.getUTCHours() * 60 + d.getUTCMinutes();
    },

    /** The schedule API's date format, MM.DD.YYYY. */
    graphqlDate: function (day) {
      var p = day.split('-');
      return p[1] + '.' + p[2] + '.' + p[0];
    }
  };

  CT.Days = Days;
})(typeof window !== 'undefined' ? window : global);
