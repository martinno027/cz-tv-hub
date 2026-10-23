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

    /** Start of the time axis: the earliest item, floored to a half hour; null without items. */
    axisStart: function (items) {
      if (!items.length) return null;
      var first = Math.min.apply(null, items.map(function (i) { return i.start; }));
      return first - first % (30 * MINUTE);
    },

    /** Slots {start, end, item}; `item` is null for a filler. Items need `start` and `end`. */
    row: function (items, axisStart, axisEnd) {
      var slots = [];
      var cursor = axisStart;
      items.slice().sort(function (a, b) { return a.start - b.start; }).forEach(function (p) {
        if (p.end <= cursor && p.start >= cursor) return; // zero-length duplicate
        var start = Math.max(p.start, cursor);
        if (start - cursor >= GuideLayout.MIN_FILLER_MS) {
          slots.push({ start: cursor, end: start, item: null });
        } else {
          start = cursor; // absorb a tiny gap
        }
        // Overlaps push the start right; a minimum width pushes the end (and the next start) right.
        var end = Math.max(p.end, start + GuideLayout.MIN_SLOT_MS);
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
