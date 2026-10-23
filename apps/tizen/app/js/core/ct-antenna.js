/*
 * spec/apps.md "ČT images for Anténa ČT channels": which CT programme an Anténa event of a ČT channel is, so the
 * guide shows CT's official image (golden cases: spec/fixtures/ct_antenna_match.json). Pure, ES2017.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var DROP = ['hd', 'sd', 'uhd', '4k', 't2', 'hevc', 'pha', 'praha', 'jm', 'sm', 'brno', 'ostrava'];
  var SLUGS = {
    'ct1': ['ct1'], 'ct2': ['ct2'], 'ct24': ['ct24'], 'ctsport': ['sport'],
    'ct:d': ['decko'], 'ctd': ['decko'], 'ctart': ['art'], 'ct:d/art': ['decko', 'art'], 'ctd/art': ['decko', 'art']
  };

  function fold(text) {
    return String(text || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  }

  function normTitle(title) {
    return fold(title).replace(/[^0-9a-z]/g, '');
  }

  function overlap(start, end, p) {
    return Math.max(0, Math.min(end, p.end) - Math.max(start, p.start));
  }

  var CtAntenna = {
    /** CT schedule slugs of an Anténa channel name; [] for every non-ČT channel. */
    slugs: function (channelName) {
      var key = fold(channelName).split(/\s+/).filter(function (t) {
        return t && DROP.indexOf(t) < 0;
      }).join('');
      return SLUGS.hasOwnProperty(key) ? SLUGS[key].slice() : [];
    },

    /**
     * The CT programme the event is: same normalised title, else the one with the largest overlap if it covers
     * at least half of the event; else null. `bySlug` = {slug: [{title, start, end, ...}]} (times in ms).
     */
    programme: function (channelName, title, start, end, bySlug) {
      var candidates = [];
      CtAntenna.slugs(channelName).forEach(function (slug) {
        (bySlug[slug] || []).forEach(function (p) {
          if (p.end > start && p.start < end) candidates.push(p);
        });
      });
      if (!candidates.length) return null;
      var wanted = normTitle(title);
      for (var i = 0; i < candidates.length; i++) {
        if (normTitle(candidates[i].title) === wanted) return candidates[i];
      }
      var best = candidates[0];
      candidates.forEach(function (p) {
        if (overlap(start, end, p) > overlap(start, end, best)) best = p;
      });
      return overlap(start, end, best) * 2 >= end - start ? best : null;
    }
  };

  CT.CtAntenna = CtAntenna;
})(typeof window !== 'undefined' ? window : global);
