/*
 * Audio choice for Anténa (spec/apps.md "Audio choice for Anténa"): the gateway offers every broadcast audio track
 * as an HLS rendition; on the Samsung ▲ switches to the next one and the OSD names it.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var NAMES = {
    cs: 'Čeština', ces: 'Čeština', cze: 'Čeština', sk: 'Slovenština', slk: 'Slovenština', slo: 'Slovenština',
    en: 'Angličtina', eng: 'Angličtina', de: 'Němčina', deu: 'Němčina', ger: 'Němčina',
    fr: 'Francouzština', fra: 'Francouzština', fre: 'Francouzština', pl: 'Polština', pol: 'Polština',
    ru: 'Ruština', rus: 'Ruština', uk: 'Ukrajinština', ukr: 'Ukrajinština', es: 'Španělština', spa: 'Španělština',
    it: 'Italština', ita: 'Italština', hu: 'Maďarština', hun: 'Maďarština'
  };

  CT.AudioTracks = {
    /** Czech name of a track: from its language code, else "Zvuk N". */
    label: function (track, position) {
      var lang = String(track && track.language || '').toLowerCase().split('-')[0];
      return NAMES[lang] || 'Zvuk ' + (position + 1);
    },

    /** Position (in `tracks`) of the track after the one with `currentIndex`, wrapping round; -1 if none. */
    next: function (tracks, currentIndex) {
      if (!tracks.length) return -1;
      var at = -1;
      for (var i = 0; i < tracks.length; i++) if (tracks[i].index === currentIndex) at = i;
      return (at + 1) % tracks.length;
    },

    /** OSD text after switching: "Zvuk: Angličtina (2/2)". */
    osd: function (tracks, position) {
      return 'Zvuk: ' + CT.AudioTracks.label(tracks[position], position) + ' (' + (position + 1) + '/' + tracks.length + ')';
    },

    /** AVPlay's getTotalTrackInfo() entries → [{index, language}] of the audio tracks. */
    fromAvPlay: function (infos) {
      var out = [];
      (infos || []).forEach(function (t) {
        if (!t || t.type !== 'AUDIO') return;
        var extra = {};
        try {
          extra = typeof t.extra_info === 'string' ? JSON.parse(t.extra_info) : (t.extra_info || {});
        } catch (e) {
          extra = {};
        }
        out.push({ index: t.index, language: extra.language || extra.track_lang || '' });
      });
      return out;
    }
  };
})(typeof window !== 'undefined' ? window : global);
