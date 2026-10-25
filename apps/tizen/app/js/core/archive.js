/*
 * "Archiv" (spec/apps.md "Archiv", spec/dvb-gateway.md "Archive"): the gateway's /dvb/archive answers parsed
 * defensively (unknown values stay unknown, broken cards and episodes are left out), the card and episode lines,
 * and the play requests of episodes. Images are only CT image URLs (https) or the gateway's own poster and
 * thumbnail paths; they are fetched like the guide's into blob URLs kept in memory (never stored).
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var J = CT.Json;

  var THUMB_PATH = /^\/dvb\/recording\/([A-Za-z0-9_-]{1,80})\/thumb\.jpg$/;
  var SOURCES = ['ct', 'rec', 'both'];
  var KINDS = ['movie', 'series'];

  function isNumber(v) {
    return typeof v === 'number' && isFinite(v);
  }

  function isObject(v) {
    return v !== null && typeof v === 'object' && !Array.isArray(v);
  }

  /** Strings of a list field (a single string counts as a list of one). */
  function strings(o, key) {
    var v = o ? o[key] : null;
    if (typeof v === 'string') v = [v];
    return Array.isArray(v) ? v.filter(function (s) { return typeof s === 'string' && s.trim(); }) : [];
  }

  function positiveInt(v) {
    return isNumber(v) && v > 0 ? Math.floor(v) : null;
  }

  function nextPage(o) {
    return positiveInt(o.nextPage);
  }

  /** CT's GraphQL sometimes uses names, the gateway may pass names or slugs: "ČT1" / "ct1" → the CT channel. */
  function ctChannel(name) {
    var n = String(name || '').trim().toLowerCase().replace(/\s+/g, ' ');
    if (!n) return null;
    return CT.Channels.all.filter(function (c) { return c.slug === n || c.name.toLowerCase() === n; })[0] || null;
  }

  /**
   * An episode's ČT channel (CT API id) for the decision rules: the gateway's `channelId` when it is a known `CH_…`;
   * null, missing or unknown → the display name ("ČT1" → CH_1); still unknown → null (VOD by idec only).
   */
  function episodeChannel(o) {
    if (o.source !== 'ct') return null;
    if (typeof o.channelId === 'string' && CT.Channels.byId(o.channelId)) return o.channelId;
    var named = ctChannel(J.str(o, 'channel'));
    return named ? named.id : null;
  }

  var Archive = {
    MIN_QUERY: 2,
    MAX_QUERY: 100,
    DEBOUNCE_MS: 500,
    NO_GATEWAY: 'Archiv potřebuje připojení k serveru. Nastavte ho v Nastavení → Server.',
    FAILED: 'Archiv se nepodařilo načíst',
    NOTHING_FOUND: 'Nic nenalezeno',
    /** TMDb's required notice; used when an answer does not carry its own `attribution`. */
    ATTRIBUTION: 'This application uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB.',
    SORTS: [
      { id: 'popular', title: 'Nejsledovanější' },
      { id: 'newest', title: 'Naposledy vysílané' },
      { id: 'az', title: 'A–Z' }
    ],

    /** The search text to send (trimmed, at most 100 characters), or null below 2 characters. */
    query: function (text) {
      var q = String(text || '').trim().replace(/\s+/g, ' ').slice(0, Archive.MAX_QUERY);
      return q.length >= Archive.MIN_QUERY ? q : null;
    },

    /**
     * An image request for the app's ImageLoader, or null: a CT image URL (https, no key), a TMDb poster or a
     * recording thumbnail through the gateway (with the key; the thumbnail only while thumbnails are on).
     * The keys are the guide's and the programme page's, so the memory cache is shared.
     */
    image: function (value) {
      if (typeof value !== 'string' || !value) return null;
      if (/^https:\/\/[^\s"]+$/.test(value)) return { key: 'ct:' + value, kind: 'ct', final: true, url: value };
      if (CT.Programme.POSTER_PATH.test(value)) return { key: 'gw:poster:' + value, kind: 'gw', final: true, path: value };
      var thumb = THUMB_PATH.exec(value);
      if (thumb) return { key: 'gw:' + thumb[1], kind: 'gw', final: true, recordingId: thumb[1], thumb: true };
      return null;
    },

    /**
     * card {id, source, title, kind, year, image, rating, genres, episodeCount, durationMinutes, tmdb} or null. `kind`
     * may be null (ČT's search gives none); `tmdb` = the card shows TMDb data (the logo and notice go on screen).
     */
    parseCard: function (o) {
      if (!isObject(o)) return null;
      var id = J.str(o, 'id');
      var title = J.str(o, 'title');
      if (!id || !title || !title.trim()) return null;
      var rating = J.obj(o, 'rating');
      var year = positiveInt(o.year);
      return {
        id: id,
        source: SOURCES.indexOf(o.source) >= 0 ? o.source : null,
        title: title.trim(),
        kind: KINDS.indexOf(o.kind) >= 0 ? o.kind : null,
        year: year && year >= 1880 && year <= 2200 ? year : null,
        image: typeof o.image === 'string' ? o.image : '',
        rating: rating && CT.Programme.ratingValue(rating) !== null ? { value: rating.value, votes: rating.votes } : null,
        genres: strings(o, 'genres'),
        episodeCount: positiveInt(o.episodeCount),
        durationMinutes: positiveInt(o.durationMinutes),
        tmdb: o.tmdb === true
      };
    },

    /**
     * episode {source, idec | recordingId, title, description, durationSeconds, aired, channel, channelId, playable}
     * or null. `channelId` = the CT API id of its ČT channel for the decision rules (the gateway's, else from the
     * display name `channel`) or null (VOD by idec only).
     */
    parseEpisode: function (o) {
      if (!isObject(o)) return null;
      var source = o.source === 'rec' ? 'rec' : o.source === 'ct' ? 'ct' : null;
      var idec = J.str(o, 'idec');
      var recordingId = J.str(o, 'recordingId');
      if (!source || (source === 'ct' && !idec) || (source === 'rec' && !recordingId)) return null;
      return {
        source: source,
        idec: source === 'ct' ? idec : null,
        recordingId: source === 'rec' ? recordingId : null,
        title: (J.str(o, 'title') || '').trim(),
        description: J.str(o, 'description') || '',
        durationSeconds: isNumber(o.durationSeconds) && o.durationSeconds > 0 ? o.durationSeconds : null,
        aired: J.instant(o.aired),
        channel: (J.str(o, 'channel') || '').trim(),
        channelId: episodeChannel(o),
        playable: o.playable === true
      };
    },

    cards: function (o) {
      return J.arr(o, 'cards').map(Archive.parseCard).filter(Boolean);
    },

    /**
     * GET /dvb/archive → {rows: [{id, title, cards, more}], attribution, error}; rows without cards are left out.
     * `error` = the gateway's text when iVysílání did not answer (a 200 with our recordings still listed).
     */
    parseHome: function (status, body) {
      var top = CT.DvbParser.ensureOk(status, body);
      return {
        rows: J.arr(top, 'rows').filter(isObject).map(function (r) {
          var id = J.str(r, 'id');
          var title = J.str(r, 'title');
          if (!id || !title) return null;
          return { id: id, title: title, cards: Archive.cards(r), more: r.more === true };
        }).filter(function (r) { return r && r.cards.length; }),
        attribution: J.str(top, 'attribution') || '',
        error: J.str(top, 'error') || ''
      };
    },

    /** GET /dvb/archive/category/<slug> → {title, genres: [{id, title}], cards, nextPage, attribution, error}. */
    parseCategory: function (status, body) {
      var top = CT.DvbParser.ensureOk(status, body);
      return {
        title: J.str(top, 'title') || '',
        genres: J.arr(top, 'genres').filter(isObject).map(function (g) {
          var id = J.str(g, 'id');
          var title = J.str(g, 'title');
          return id && title ? { id: id, title: title } : null;
        }).filter(Boolean),
        cards: Archive.cards(top),
        nextPage: nextPage(top),
        attribution: J.str(top, 'attribution') || '',
        error: J.str(top, 'error') || ''
      };
    },

    /** GET /dvb/archive/search → {cards, nextPage, attribution, error}. */
    parseSearch: function (status, body) {
      var top = CT.DvbParser.ensureOk(status, body);
      return {
        cards: Archive.cards(top), nextPage: nextPage(top), attribution: J.str(top, 'attribution') || '',
        error: J.str(top, 'error') || ''
      };
    },

    /**
     * GET /dvb/archive/show/<id> → {card, description, creators, countries, seasons: [{id, title}],
     * groups: [{title, season, nextPage, episodes}], attribution}; a show without a usable card is an error. A ČT
     * group carries its `season` (id or null) and `nextPage` (more of its episodes by ?season=&page=); the
     * "Nahráno z antény" group has neither. `error` = the gateway's text when iVysílání did not answer (a show in both
     * sources then comes with only our recorded episodes).
     */
    parseShow: function (status, body) {
      var top = CT.DvbParser.ensureOk(status, body);
      var card = Archive.parseCard(J.obj(top, 'card'));
      if (!card) throw new CT.CtError('archive show: no card', Archive.FAILED);
      return {
        card: card,
        description: J.str(top, 'description') || '',
        creators: strings(top, 'creators'),
        countries: strings(top, 'countries'),
        seasons: J.arr(top, 'seasons').filter(isObject).map(function (s) {
          var id = J.str(s, 'id');
          var title = J.str(s, 'title');
          return id && title ? { id: id, title: title } : null;
        }).filter(Boolean),
        groups: J.arr(top, 'groups').filter(isObject).map(function (g) {
          return {
            title: J.str(g, 'title') || '',
            season: J.str(g, 'season'),
            nextPage: nextPage(g),
            episodes: J.arr(g, 'episodes').map(Archive.parseEpisode).filter(Boolean)
          };
        }).filter(function (g) { return g.episodes.length; }),
        attribution: J.str(top, 'attribution') || '',
        error: J.str(top, 'error') || ''
      };
    },

    /** Source icons of a card: globe = iVysílání, antenna = our recording, both for `both`. */
    sources: function (card) {
      if (card.source === 'ct') return ['ct'];
      if (card.source === 'rec') return ['dvb'];
      return card.source === 'both' ? ['ct', 'dvb'] : [];
    },

    kindWord: function (kind) {
      return kind === 'movie' ? 'film' : kind === 'series' ? 'seriál' : '';
    },

    /** "★ 7,4" for a TMDb rating with at least 20 votes, else null. */
    ratingShort: function (rating) {
      var value = CT.Programme.ratingValue(rating);
      return value === null ? null : '★ ' + value;
    },

    /** A card's lines: {title, meta: "2012 · film", rating: "★ 7,4" | null, sources}. */
    cardLines: function (card) {
      return {
        title: card.title,
        meta: [card.year ? String(card.year) : '', Archive.kindWord(card.kind)].filter(Boolean).join(' · '),
        rating: Archive.ratingShort(card.rating),
        sources: Archive.sources(card)
      };
    },

    /** Whether a card shows TMDb data (the gateway's `tmdb` flag): the TMDB logo and notice go on screen. */
    showsTmdb: function (card) {
      return card.tmdb === true;
    },

    /** Czech word for `n` episodes (1 díl, 2–4 díly, else dílů). */
    episodesWord: function (n) {
      if (n === 1) return 'díl';
      return n >= 2 && n <= 4 ? 'díly' : 'dílů';
    },

    /** The show page's facts: source · year · kind · countries · episode count. */
    showFacts: function (show) {
      var card = show.card;
      var source = card.source === 'ct' ? 'iVysílání' : card.source === 'rec' ? 'Z antény'
        : card.source === 'both' ? 'iVysílání a z antény' : '';
      var count = card.episodeCount ? card.episodeCount + ' ' + Archive.episodesWord(card.episodeCount) : '';
      return [source, card.year ? String(card.year) : '', Archive.kindWord(card.kind), show.countries.join(', '), count]
        .filter(Boolean);
    },

    /** `9. 1.` in Prague, with the year when it is not this year's (`9. 1. 2005`). */
    shortDate: function (ms, now) {
      var day = CT.Days.today(ms).split('-');
      var text = Number(day[2]) + '. ' + Number(day[1]) + '.';
      return day[0] === CT.Days.today(now).split('-')[0] ? text : text + ' ' + day[0];
    },

    /** "1/26 Od začátku · 53 min · ČT1 9. 1." (the parts that are known). */
    episodeLine: function (episode, now) {
      var minutes = episode.durationSeconds ? Math.round(episode.durationSeconds / 60) + ' min' : '';
      var aired = [episode.channel, episode.aired !== null ? Archive.shortDate(episode.aired, now) : ''].filter(Boolean).join(' ');
      return [episode.title, minutes, aired].filter(Boolean).join(' · ');
    },

    /** The "Pokračovat" key of an episode (as in the guide: `ct:<idec>`, `dvb:<recording id>`). */
    resumeKey: function (episode) {
      return episode.source === 'ct' ? 'ct:' + episode.idec : 'dvb:' + episode.recordingId;
    },

    /**
     * The play request of an episode of `show` (a parsed show). iVysílání: with a known CT channel and broadcast
     * time the guide's own path (decision rules by time: the buffer within 3 h, else VOD by idec), else VOD by
     * idec; our recordings: the Anténa player (a continuous-recording id opens the T05 timeline).
     * `resumeSeconds` (optional) continues there.
     */
    episodeRequest: function (episode, show, resumeSeconds) {
      var card = show.card;
      var image = card.image;
      var episodeTitle = episode.title && episode.title !== card.title ? episode.title : '';
      var length = episode.durationSeconds || 0;
      var request;
      var channel = null;
      if (episode.source === 'ct') {
        channel = episode.channelId ? CT.Channels.byId(episode.channelId) : null;
        if (channel && episode.aired !== null && length) {
          request = {
            kind: 'programme', channelId: channel.id, title: card.title, idec: episode.idec, start: episode.aired,
            end: episode.aired + length * 1000, isPlayableNow: episode.playable
          };
        } else {
          request = { kind: 'archive', idec: episode.idec, title: card.title, channelName: episode.channel };
          if (episode.aired !== null) request.start = episode.aired;
        }
      } else {
        request = {
          kind: 'dvbRecorded', channelId: '', channelName: episode.channel, title: card.title,
          recordingId: episode.recordingId, fromStart: false
        };
        if (!Archive.image(image)) image = CT.Thumbs.path(episode.recordingId);
      }
      request.episodeTitle = episodeTitle;
      request.resumeKey = Archive.resumeKey(episode);
      request.lengthSeconds = length;
      request.resumeMeta = episode.source === 'ct'
        ? CT.ResumeRules.meta(card.title, episodeTitle, image, 'ct', channel ? episode.aired : null, channel ? channel.id : null)
        : CT.ResumeRules.meta(card.title, episodeTitle, image, 'dvb');
      if (resumeSeconds) request.resumeSeconds = resumeSeconds;
      return request;
    },

    /**
     * A card of "Pokračovat ve sledování" plays at once from its position: `item` = a ResumeStore.continueRow()
     * entry. ČT with a known start and channel by the decision rules (the buffer within 3 h of the start, else VOD),
     * without them (older entries) VOD by idec; our recordings through the Anténa player.
     */
    continueRequest: function (item) {
      var title = item.showTitle || item.title;
      var episodeTitle = item.showTitle ? item.title : '';
      var meta = CT.ResumeRules.meta(title, episodeTitle, item.image, item.source, item.start, item.channel);
      var id = item.key.slice(item.key.indexOf(':') + 1);
      var request;
      if (item.key.indexOf('dvb:') === 0) {
        request = { kind: 'dvbRecorded', channelId: '', title: title, recordingId: id, fromStart: false };
      } else if (item.start !== null && item.start !== undefined && CT.Channels.byId(item.channel)) {
        // isPlayableNow: it played here before; a programme not playable after all still gets CT's own text
        request = {
          kind: 'programme', channelId: item.channel, title: title, idec: id, start: item.start,
          end: item.start + item.length * 1000, isPlayableNow: true
        };
      } else {
        request = { kind: 'archive', idec: id, title: title };
      }
      request.episodeTitle = episodeTitle;
      request.resumeKey = item.key;
      request.lengthSeconds = item.length;
      request.resumeMeta = meta;
      request.resumeSeconds = item.progress;
      return request;
    },

    /** Whether a show group is iVysílání's (it has ČT episodes; "Nahráno z antény" has only ours). */
    isCtGroup: function (group) {
      return group.episodes.some(function (e) { return e.source === 'ct'; });
    },

    /**
     * Another season chosen (page 1 of `?season=`): its iVysílání groups instead of the old ones, in front; the
     * recorded groups stay.
     */
    withSeason: function (groups, fresh) {
      return fresh.filter(Archive.isCtGroup).concat(groups.filter(function (g) { return !Archive.isCtGroup(g); }));
    },

    /**
     * The next page of group `index` (`?season=<its season>&page=<its nextPage>`): the answer's iVysílání episodes
     * appended to it, its nextPage from the answer's group (of the same season when there is one).
     */
    withPage: function (groups, index, fresh) {
      var group = groups[index];
      var ct = fresh.filter(Archive.isCtGroup);
      var same = ct.filter(function (g) { return g.season === group.season; })[0] || ct[0] || null;
      var out = groups.slice();
      out[index] = {
        title: group.title, season: group.season, nextPage: same ? same.nextPage : null,
        episodes: group.episodes.concat(same ? same.episodes.filter(function (e) { return e.source === 'ct'; }) : [])
      };
      return out;
    },

    /** The film's episode to play from a show page: its first iVysílání episode, else its first episode. */
    filmEpisode: function (show) {
      var all = [];
      show.groups.forEach(function (g) { all = all.concat(g.episodes); });
      return all.filter(function (e) { return e.source === 'ct'; })[0] || all[0] || null;
    }
  };

  /** Path segments: the ids are ours (`ct:<id>`, `rec:<key>`, category slugs); the colon stays readable. */
  function segment(id) {
    return encodeURIComponent(id).replace(/%3A/gi, ':');
  }

  /** The /dvb/archive calls through a DvbClient (the key goes as the header). */
  function ArchiveClient(dvb) {
    this.dvb = dvb;
  }

  /** A gateway GET whose failures (also a transport that throws at once) come as a rejected promise. */
  ArchiveClient.prototype.get = function (path) {
    var dvb = this.dvb;
    return new Promise(function (resolve) { resolve(dvb.get(path)); });
  };

  ArchiveClient.prototype.home = function () {
    return this.get('/dvb/archive').then(function (r) { return Archive.parseHome(r.status, r.body); });
  };

  /** `sort` = popular | newest | az, `genre` = a sub-genre id or null, `page` from 1. */
  ArchiveClient.prototype.category = function (slug, sort, genre, page) {
    var path = '/dvb/archive/category/' + segment(slug) + '?sort=' + encodeURIComponent(sort || 'popular') +
      (genre ? '&genre=' + encodeURIComponent(genre) : '') + '&page=' + (page || 1);
    return this.get(path).then(function (r) { return Archive.parseCategory(r.status, r.body); });
  };

  ArchiveClient.prototype.search = function (query, page) {
    var path = '/dvb/archive/search?q=' + encodeURIComponent(query) + '&page=' + (page || 1);
    return this.get(path).then(function (r) { return Archive.parseSearch(r.status, r.body); });
  };

  /** A show; with `season` (and `page`) the iVysílání episodes of that season; a later page without one too. */
  ArchiveClient.prototype.show = function (id, season, page) {
    var query = season ? '?season=' + encodeURIComponent(season) + '&page=' + (page || 1) : page > 1 ? '?page=' + page : '';
    return this.get('/dvb/archive/show/' + segment(id) + query).then(function (r) {
      return Archive.parseShow(r.status, r.body);
    });
  };

  Archive.ctChannel = ctChannel;
  CT.Archive = Archive;
  CT.ArchiveClient = ArchiveClient;
})(typeof window !== 'undefined' ? window : global);
