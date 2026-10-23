/*
 * Horizontal layout of one guide row. Slots never overlap, follow start/end where the data allows,
 * and gaps (or a row without data) become "Mimo vysílání" filler slots so the row is never blank.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var MINUTE = 60 * 1000;

  var GuideLayout = {
    /** Smallest slot, so very short programmes stay visible and focusable. */
    MIN_SLOT_MS: 3 * MINUTE,
    /** Gaps shorter than this are absorbed instead of producing a sliver of filler. */
    MIN_FILLER_MS: MINUTE,

    /**
     * Slots {start, end, item} on [axisStart, axisEnd); `item` is null for a filler. Items need `start`
     * and `end`; items crossing the axis edges (the broadcast day's 05:00) are clipped to it.
     */
    row: function (items, axisStart, axisEnd) {
      var slots = [];
      var cursor = axisStart;
      items.slice().sort(function (a, b) { return a.start - b.start; }).forEach(function (p) {
        if (cursor >= axisEnd || p.end <= axisStart || p.start >= axisEnd) return; // outside the day
        if (p.end <= cursor && p.start >= cursor) return; // zero-length duplicate
        var start = Math.max(p.start, cursor);
        if (start - cursor >= GuideLayout.MIN_FILLER_MS) {
          slots.push({ start: cursor, end: start, item: null });
        } else {
          start = cursor; // absorb a tiny gap
        }
        // Overlaps push the start right; a minimum width pushes the end (and the next start) right.
        var end = Math.min(Math.max(p.end, start + GuideLayout.MIN_SLOT_MS), axisEnd);
        slots.push({ start: start, end: end, item: p });
        cursor = end;
      });
      if (axisEnd - cursor >= GuideLayout.MIN_FILLER_MS) slots.push({ start: cursor, end: axisEnd, item: null });
      return slots;
    },

    /**
     * "Live column first" (spec/apps.md): index of the slot whose programme airs at `now` (by its own
     * times, not the widened slot), else the slot (or filler) under `now`; -1 when `now` is off the row.
     */
    liveSlot: function (slots, now) {
      var i;
      for (i = 0; i < slots.length; i++) {
        var item = slots[i].item;
        if (item && item.start <= now && now < item.end) return i;
      }
      for (i = 0; i < slots.length; i++) if (slots[i].start <= now && now < slots[i].end) return i;
      return -1;
    },

    /**
     * The part of the guide that has DOM (rows r0..r1-1, time t0..t1): the viewport plus a buffer, so a
     * TV never lays out every cell of the day. `view` = {row, rows, start, end} (first visible row, number of
     * visible rows, visible time range); `opts` = {extraRows, extraMs} (defaults 4 rows, 2 h).
     */
    window: function (view, rowCount, opts) {
      var extraRows = opts && opts.extraRows !== undefined ? opts.extraRows : 4;
      var extraMs = opts && opts.extraMs !== undefined ? opts.extraMs : 120 * MINUTE;
      return {
        r0: Math.max(0, view.row - extraRows),
        r1: Math.min(rowCount, view.row + view.rows + extraRows),
        t0: view.start - extraMs,
        t1: view.end + extraMs
      };
    },

    /** The window still holds the viewport with at least `marginRows` rows / `marginMs` to spare on each side. */
    covers: function (win, view, rowCount, marginRows, marginMs) {
      if (!win) return false;
      var top = win.r0 === 0 || view.row - win.r0 >= marginRows;
      var bottom = win.r1 === rowCount || win.r1 - (view.row + view.rows) >= marginRows;
      return top && bottom && view.start - win.t0 >= marginMs && win.t1 - view.end >= marginMs;
    },

    /** [first, last + 1) of the slots overlapping [t0, t1); `slots` are sorted and do not overlap. */
    slotRange: function (slots, t0, t1) {
      var lo = 0;
      var hi = slots.length;
      while (lo < hi) { // first slot ending after t0
        var mid = (lo + hi) >> 1;
        if (slots[mid].end <= t0) lo = mid + 1; else hi = mid;
      }
      var end = lo;
      while (end < slots.length && slots[end].start < t1) end++;
      return [lo, end];
    },

    /** "Across days" (spec/apps.md): the day before/after `day` in the guide's `days`, or null at the range's ends. */
    neighbourDay: function (days, day, delta) {
      var i = days.indexOf(day);
      return i < 0 || i + delta < 0 || i + delta >= days.length ? null : days[i + delta];
    },

    /** Axis ticks every `stepMs` (default 30 min), starting at `axisStart`. */
    ticks: function (axisStart, axisEnd, stepMs) {
      var step = stepMs || 30 * MINUTE;
      var ticks = [];
      for (var t = axisStart; t <= axisEnd; t += step) ticks.push(t);
      return ticks;
    }
  };

  CT.GuideLayout = GuideLayout;
})(typeof window !== 'undefined' ? window : global);
