/*
 * The programme page (spec/apps.md "Programme page (T15)"): which buttons it offers, its state line and the TMDb
 * rating line (golden cases: programme_page_cases.json), plus the details client for GET /dvb/details
 * (spec/dvb-gateway.md "Programme details"). Posters are gateway paths only, fetched like thumbnails.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var J = CT.Json;

  var MIN_VOTES = 20;
  var POSTER_PATH = /^\/dvb\/details\/image\/w342\/[A-Za-z0-9_-]+\.(jpg|png)$/;

  function isNumber(v) {
    return typeof v === 'number' && isFinite(v);
  }

  /** `1234567` → `1 234 567` (a plain space, as in the golden cases). */
  function thousands(n) {
    var s = String(Math.floor(n));
    var out = '';
    while (s.length > 3) {
      out = ' ' + s.slice(-3) + out;
      s = s.slice(0, -3);
    }
    return s + out;
  }

  var Programme = {
    MIN_VOTES: MIN_VOTES,

    /** 'future' | 'airing' | 'ended' */
    state: function (entry, now) {
      if (now < entry.start) return 'future';
      return now < entry.end ? 'airing' : 'ended';
    },

    /**
     * The golden cases' inputs for an entry: {source, state, startable, playable}. CT: airing always has the
     * "Zpět v čase" buffer; ended = the decision rules (buffer or archive) say it plays. Anténa: airing from the
     * start only while recording, ended only with a completed recording.
     */
    facts: function (entry, now) {
      var state = Programme.state(entry, now);
      var f = { source: entry.source, state: state, startable: false, playable: false };
      if (entry.source === 'ct') {
        var p = entry.item;
        f.startable = state === 'airing';
        f.playable = state === 'ended' && CT.Decisions.decide(p.start, p.end, p.isPlayableNow, now).action !== 'vod_unavailable';
      } else if (entry.source === 'dvb') {
        var rec = entry.item.recording;
        f.startable = state === 'airing' && !!rec && rec.state === 'recording';
        f.playable = state === 'ended' && !!rec && rec.state === 'completed';
      }
      return f;
    },

    /**
     * Button ids in order (golden cases `buttons`): resume, startOver, live, openOfficialApp. `c` = {source,
     * state, startable, playable, resume}; `officialApp` = whether the platform offers "Otevřít v iVysílání"
     * (Samsung does not). "Pokračovat" only together with a playable start. The page adds Zpět after these.
     */
    buttons: function (c, officialApp) {
      var ids = [];
      if (c.state === 'airing') {
        if (c.resume && c.startable) ids.push('resume');
        if (c.startable) ids.push('startOver');
        ids.push('live');
      } else if (c.state === 'ended' && (c.playable || c.source === 'ct')) {
        // an ended CT programme is always offered: playing it shows CT's own text (licence, DRM) when it cannot play
        if (c.resume) ids.push('resume');
        ids.push('startOver');
      }
      if (officialApp && c.source === 'ct' && c.state !== 'future') ids.push('openOfficialApp');
      return ids;
    },

    /**
     * The page's buttons for an entry on this TV: [{id, label, request}] (no "Otevřít v iVysílání" on Samsung).
     * The requests are those of the old choice (Entries.select), "Pokračovat" carrying the remembered position.
     */
    actions: function (entry, now, store) {
      var s = CT.Entries.select(entry, now, store);
      var facts = Programme.facts(entry, now);
      facts.resume = s.type === 'choice' && !!s.resume;
      var fromStart = s.type === 'play' ? s.request : s.type === 'choice' ? s.fromStart : null;
      var live = s.type === 'choice' ? s.live : null;
      return Programme.buttons(facts, false).map(function (id) {
        if (id === 'resume') return { id: id, label: 'Pokračovat od ' + s.resumeLabel, request: s.resume };
        if (id === 'startOver') return { id: id, label: 'Od začátku', request: fromStart };
        return { id: id, label: 'Živě', request: live };
      }).filter(function (b) { return !!b.request; });
    },

    /** The state line, as in the guide detail of the other apps: what the buttons do, or why there are none. */
    stateLine: function (entry, now) {
      var f = Programme.facts(entry, now);
      if (f.state === 'future') return 'Ještě nezačalo';
      if (entry.source === 'dvb') {
        if (f.state === 'airing') return f.startable ? 'Právě běží – od začátku nebo živě' : 'Právě běží – živě';
        return f.playable ? 'Přehrát záznam' : CT.DvbMessages.NOT_RECORDED;
      }
      if (f.state === 'airing') return 'Právě běží – od začátku nebo živě';
      var p = entry.item;
      var action = CT.Decisions.decide(p.start, p.end, p.isPlayableNow, now).action;
      if (action === 'startover') return 'Zpět v čase';
      return action === 'vod' ? 'Přehrát ze záznamu' : 'Ze záznamu zřejmě nedostupné';
    },

    /** Czech word for `n` votes (CLDR cs: 1 one, 2–4 few, else other). */
    votesWord: function (n) {
      if (n === 1) return 'hlas';
      return n >= 2 && n <= 4 ? 'hlasy' : 'hlasů';
    },

    /** "Hodnocení TMDB 7,4 (1 234 hlasů)", or null without a rating or below 20 votes. */
    ratingLine: function (rating) {
      if (!rating || !isNumber(rating.value) || !isNumber(rating.votes) || rating.votes < MIN_VOTES) return null;
      // one decimal, half up (the epsilon keeps 6.35 from becoming 6.3 through binary rounding)
      var tenths = Math.floor(rating.value * 10 + 0.5 + 1e-9);
      var value = Math.floor(tenths / 10) + ',' + (tenths % 10);
      return 'Hodnocení TMDB ' + value + ' (' + thousands(rating.votes) + ' ' + Programme.votesWord(rating.votes) + ')';
    },

    /** Length in whole minutes ("110 min"). */
    minutes: function (entry) {
      return Math.round((entry.end - entry.start) / 60000);
    },

    /**
     * Path of GET /dvb/details for an entry, or null (Poslanecká sněmovna, no id). CT programmes go by channel slug,
     * the broadcast day of the schedule they came from (`day`, YYYY-MM-DD) and idec; Anténa by the EPG event id.
     */
    detailsPath: function (entry, day) {
      if (entry.source === 'dvb') {
        return entry.item && entry.item.id ? '/dvb/details?source=dvb&id=' + encodeURIComponent(entry.item.id) : null;
      }
      if (entry.source !== 'ct' || !entry.item || !entry.item.idec || !entry.channel.slug || !day) return null;
      return '/dvb/details?source=ct&channel=' + encodeURIComponent(entry.channel.slug) + '&date=' + day +
        '&idec=' + encodeURIComponent(entry.item.idec);
    },

    /**
     * A /dvb/details answer → {match: {...} | null, attribution} or null (404: unknown programme or details off;
     * any other failure). Defensive: an unexpected field is left out, a poster outside the gateway's own image
     * path is dropped, a rating below 20 votes counts as none.
     */
    parseDetails: function (status, body) {
      if (status !== 200) return null;
      var top = J.parse(body);
      if (!top) return null;
      var attribution = J.str(top, 'attribution') || '';
      var m = J.obj(top, 'match');
      if (!m || !J.str(m, 'title')) return { match: null, attribution: attribution };
      var rating = J.obj(m, 'rating');
      var poster = typeof m.poster === 'string' && POSTER_PATH.test(m.poster) ? m.poster : null;
      return {
        match: {
          kind: m.kind === 'tv' ? 'tv' : m.kind === 'movie' ? 'movie' : null,
          title: J.str(m, 'title'),
          originalTitle: J.str(m, 'originalTitle') || '',
          year: isNumber(m.year) ? m.year : null,
          overview: J.str(m, 'overview') || '',
          genres: J.arr(m, 'genres').filter(function (g) { return typeof g === 'string' && g; }),
          runtimeMinutes: isNumber(m.runtimeMinutes) ? m.runtimeMinutes : null,
          rating: rating && isNumber(rating.value) && isNumber(rating.votes) && rating.votes >= MIN_VOTES
            ? { value: rating.value, votes: rating.votes } : null,
          poster: poster,
          tmdbId: isNumber(m.tmdbId) ? m.tmdbId : null
        },
        attribution: attribution
      };
    },

    /** GET /dvb/details through a DvbClient (key header): the parsed answer, null on 404 or any failure. */
    fetchDetails: function (client, entry, day) {
      var path = client ? Programme.detailsPath(entry, day) : null;
      if (!path) return Promise.resolve(null);
      return client.get(path).then(function (r) {
        return Programme.parseDetails(r.status, r.body);
      }, function () {
        return null;
      });
    },

    /** "Původní název: …" only when it differs from the shown title (casefold). */
    originalTitleLine: function (match, shownTitle) {
      if (!match || !match.originalTitle) return '';
      var a = match.originalTitle.trim().toLowerCase();
      if (a === String(shownTitle || '').trim().toLowerCase() || a === match.title.trim().toLowerCase()) return '';
      return 'Původní název: ' + match.originalTitle;
    }
  };

  CT.Programme = Programme;
})(typeof window !== 'undefined' ? window : global);
