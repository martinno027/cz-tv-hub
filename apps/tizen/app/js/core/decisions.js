/* Decision rules of spec/apps.md for selecting a programme in the guide (golden cases: decisions.json). */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var Decisions = {
    /** Length of CT's "Zpět v čase" buffer. */
    TIMESHIFT_WINDOW_MS: 3 * 3600 * 1000,
    /** Start a bit early rather than miss the beginning. */
    START_MARGIN_MS: 30 * 1000,

    /**
     * Decided by time only: anything that began less than the window ago plays from the buffer.
     * Returns {action: 'future' | 'startover' | 'vod' | 'vod_unavailable', offsetSeconds?}.
     */
    decide: function (start, end, isPlayableNow, now) {
      if (now < start) return { action: 'future' };
      if (now < end || now - start < Decisions.TIMESHIFT_WINDOW_MS) {
        return { action: 'startover', offsetSeconds: Decisions.startoverOffsetSeconds(start, now) };
      }
      return { action: isPlayableNow ? 'vod' : 'vod_unavailable' };
    },

    /** `max(0, (start − M) − (now − W))` in seconds. */
    startoverOffsetSeconds: function (start, now) {
      var bufferStart = now - Decisions.TIMESHIFT_WINDOW_MS;
      return Math.max(0, Math.floor((start - Decisions.START_MARGIN_MS - bufferStart) / 1000));
    }
  };

  /** How an "Anténa" programme can be played (spec/dvb-gateway.md). */
  var DvbDecisions = {
    /** 'future' | 'live' | 'live_or_startover' | 'recording' | 'not_recorded' */
    decide: function (event, now) {
      var state = event.recording ? event.recording.state : null;
      if (now < event.start) return 'future';
      if (now < event.end) return state === 'recording' ? 'live_or_startover' : 'live';
      return state === 'completed' ? 'recording' : 'not_recorded';
    }
  };

  CT.Decisions = Decisions;
  CT.DvbDecisions = DvbDecisions;
})(typeof window !== 'undefined' ? window : global);
