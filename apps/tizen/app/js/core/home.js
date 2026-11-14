/*
 * Domů (spec/apps.md "Domů", T58): what its blocks show, from data the app already has (nothing is fetched for it).
 * The header's calendar is CT.Calendar; the screen is ui/home.js.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var Home = {
    NOW_COUNT: 10,
    GATEWAY_DOWN: 'Brána antény není dostupná.',
    BAD_KEY: 'Brána antény odmítla klíč. Zkontrolujte ho v Nastavení → Server.',
    RECORDING_PAUSED: 'Nahrávání je pozastavené: na disku nahrávek dochází místo.',

    /**
     * The gateway notice, or null: only with a gateway configured; `status` = the last /dvb/status check
     * ({failed, badKey} or {recordingPaused}), null before the first one. A 401 (`badKey`) says so; any other
     * failure is "not available".
     */
    notice: function (configured, status) {
      if (!configured || !status) return null;
      if (status.failed) return status.badKey ? Home.BAD_KEY : Home.GATEWAY_DOWN;
      return status.recordingPaused ? Home.RECORDING_PAUSED : null;
    },

    /**
     * "Teď běží": the first 10 visible ČT and Anténa channels of `list` (channelList items, in this TV's order) as
     * [{item, number}]; the number stays the channel's number in Kanály. Chamber streams have their own block.
     */
    nowItems: function (list) {
      return list.map(function (item, i) {
        return { item: item, number: i + 1 };
      }).filter(function (x) {
        return x.item.source !== 'psp';
      }).slice(0, Home.NOW_COUNT);
    },

    /** "Sněmovna": the visible Chamber streams that are live now (`state(id)` = 'live' | 'off' | 'unknown'). */
    pspItems: function (list, state) {
      return list.map(function (item, i) {
        return { item: item, number: i + 1 };
      }).filter(function (x) {
        return x.item.source === 'psp' && state(x.item.channel.id) === 'live';
      });
    },

    /**
     * Focus after a key on rows of `lengths` (items per row): {row, col}, or null when the key leaves upwards (to the
     * tab bar). Up/down keep the column as far as the row allows.
     */
    move: function (lengths, at, key) {
      var row = at.row;
      var col = at.col;
      if (key === 'left') col = Math.max(0, col - 1);
      else if (key === 'right') col = Math.min(lengths[row] - 1, col + 1);
      else if (key === 'up') {
        if (row === 0) return null;
        row--;
      } else if (key === 'down') {
        if (row < lengths.length - 1) row++;
      }
      return { row: row, col: Math.min(col, lengths[row] - 1) };
    }
  };

  CT.Home = Home;
})(typeof window !== 'undefined' ? window : global);
