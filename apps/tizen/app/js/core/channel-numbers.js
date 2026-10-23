/*
 * Channel numbers and zapping (spec/apps.md "Channel numbers and zapping"): a channel's number is its 1-based
 * position in `visible` of "Kanály". Pure functions over key lists (`ct:CH_1`, `dvb:<id>`).
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var MAX_DIGITS = 3;

  CT.ChannelNumbers = {
    /** Number entry waits this long for another digit. */
    ENTRY_TIMEOUT_MS: 2000,
    /** Quick ▲/▼ presses: the stream is resolved only after this pause. */
    ZAP_DELAY_MS: 600,

    /** The neighbour `by` (±1) of `current` in `visible`, wrapping; not in the list: next = first, previous = last. */
    step: function (visible, current, by) {
      var n = visible.length;
      if (!n) return null;
      var i = visible.indexOf(current);
      if (i < 0) return by > 0 ? visible[0] : visible[n - 1];
      return visible[((i + by) % n + n) % n];
    },

    /** One digit into the buffer → {buffer, commit}; commit = no further digit could give a valid number. */
    type: function (buffer, digit, count) {
      if (buffer === '' && digit === 0) return { buffer: '', commit: false }; // a leading 0 is ignored
      var next = buffer + String(digit);
      return { buffer: next, commit: parseInt(next, 10) * 10 > count || next.length >= MAX_DIGITS };
    },

    /** The channel key with `number` (1-based), or null. */
    resolve: function (visible, number) {
      if (number < 1 || number > visible.length) return null;
      return visible[number - 1];
    }
  };
})(typeof window !== 'undefined' ? window : global);
