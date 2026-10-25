/*
 * Guide cells from either source (CT internet or "Anténa"): how they look and what selecting them does.
 * Entry = {source: 'ct' | 'dvb' | 'psp', channel, item, start, end, title, subtitle, description, genre, imageUrl}.
 * A Poslanecká sněmovna entry is one cell over the whole day; its item = {state: 'live' | 'off' | 'unknown',
 * today: the grid containing now} (no programme data).
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var Entries = {
    ct: function (channel, programme) {
      return {
        source: 'ct', channel: channel, item: programme, start: programme.start, end: programme.end,
        title: programme.title, subtitle: programme.episodeTitle, description: programme.description,
        genre: programme.genre, imageUrl: programme.imageUrl
      };
    },

    dvb: function (channel, event) {
      return {
        source: 'dvb', channel: channel, item: event, start: event.start, end: event.end,
        title: event.title, subtitle: event.subtitle, description: event.description,
        genre: event.genre, imageUrl: ''
      };
    },

    /** The single whole-day cell of a Chamber stream (`start`/`end` = the guide's day). Only the day containing now
     *  shows the live state; other days just say the source has live broadcasts only. */
    psp: function (channel, state, today, start, end) {
      var P = CT.Psp;
      var title = !today ? P.ONLY_LIVE_TEXT : state === 'live' ? P.LIVE_TEXT : state === 'off' ? P.OFF_TEXT : P.UNKNOWN_TEXT;
      return {
        source: 'psp', channel: channel, item: { state: state, today: today }, start: start, end: end,
        title: title, subtitle: '', description: '', genre: '', imageUrl: ''
      };
    },

    isAiring: function (entry, now) {
      return entry.start <= now && now < entry.end;
    },

    /** 'airing' | 'playable' | 'future' | 'dimmed' */
    look: function (entry, now) {
      if (entry.source === 'psp') {
        if (!entry.item.today) return 'dimmed';
        return entry.item.state === 'live' ? 'airing' : entry.item.state === 'off' ? 'dimmed' : 'future';
      }
      if (entry.source === 'ct') {
        if (Entries.isAiring(entry, now)) return 'airing';
        var p = entry.item;
        switch (CT.Decisions.decide(p.start, p.end, p.isPlayableNow, now).action) {
          case 'future': return 'future';
          case 'vod_unavailable': return 'dimmed';
          default: return 'playable';
        }
      }
      switch (CT.DvbDecisions.decide(entry.item, now)) {
        case 'live':
        case 'live_or_startover': return 'airing';
        case 'recording': return 'playable';
        case 'future': return 'future';
        default: return 'dimmed';
      }
    },

    /** Key and length under which "Pokračovat" remembers this entry, or null (nothing to resume). */
    resumeInfo: function (entry) {
      if (entry.source === 'psp') return null; // live only, nothing to resume
      var lengthSeconds = (entry.end - entry.start) / 1000;
      if (entry.source === 'ct') return { key: 'ct:' + entry.item.idec, lengthSeconds: lengthSeconds };
      var rec = entry.item.recording;
      return rec ? { key: 'dvb:' + rec.id, lengthSeconds: lengthSeconds } : null;
    },

    /**
     * What the Archiv row "Pokračovat ve sledování" shows for an entry remembered from the guide: its title (the
     * episode under the programme when there is one), CT's image or the recording's gateway thumbnail path; ČT
     * programmes with their start and CT channel, so the row resumes by the decision rules.
     */
    resumeMeta: function (entry) {
      if (entry.source === 'ct') {
        var image = /^https:\/\//.test(entry.imageUrl || '') ? entry.imageUrl : '';
        return CT.ResumeRules.meta(entry.title, entry.subtitle, image, 'ct', entry.start, entry.channel.id);
      }
      var rec = entry.item.recording;
      return CT.ResumeRules.meta(entry.title, entry.subtitle, rec ? CT.Thumbs.path(rec.id) : '', 'dvb');
    },

    /**
     * {type: 'notice', text} | {type: 'play', request} |
     * {type: 'choice', resume: request | null, resumeLabel, fromStart: request | null, live: request | null}
     * Airing: "Od začátku" / "Živě"; with a remembered position (`store`, optional) "Pokračovat od …" comes
     * first, also for ended programmes (spec/apps.md).
     */
    select: function (entry, now, store) {
      var base = Entries.baseSelect(entry, now);
      if (base.type === 'notice') return base;
      var info = Entries.resumeInfo(entry);
      var offer = info && store ? store.offer(info.key) : null;
      var tag = function (request) {
        if (!request || request.kind === 'live' || request.kind === 'dvbLive' || !info) return request;
        return Object.assign({}, request, {
          resumeKey: info.key, lengthSeconds: info.lengthSeconds, resumeMeta: Entries.resumeMeta(entry)
        });
      };
      var fromStart = tag(base.type === 'play' ? base.request : base.fromStart);
      if (offer) {
        return {
          type: 'choice',
          resume: Object.assign({}, fromStart, { resumeSeconds: offer.progress }),
          resumeLabel: offer.label,
          fromStart: fromStart,
          live: base.type === 'choice' ? base.live : null
        };
      }
      if (base.type === 'play') return { type: 'play', request: fromStart };
      return { type: 'choice', resume: null, resumeLabel: null, fromStart: fromStart, live: base.live };
    },

    baseSelect: function (entry, now) {
      var ch = entry.channel;
      if (entry.source === 'psp') {
        // Live only: off → the message (no player); on or not checked yet → try to play live.
        if (!entry.item.today) return { type: 'notice', text: CT.Psp.ONLY_LIVE_MESSAGE };
        if (entry.item.state === 'off') return { type: 'notice', text: CT.Psp.OFF_MESSAGE };
        return { type: 'play', request: { kind: 'pspLive', channelId: ch.id, title: ch.name } };
      }
      if (entry.source === 'ct') {
        var p = entry.item;
        if (now < p.start) return { type: 'notice', text: CT.Messages.FUTURE };
        if (Entries.isAiring(entry, now)) {
          return {
            type: 'choice',
            live: { kind: 'live', channelId: ch.id, title: ch.name },
            fromStart: { kind: 'startover', channelId: ch.id, title: p.title, start: p.start }
          };
        }
        return {
          type: 'play',
          request: {
            kind: 'programme', channelId: ch.id, title: p.title, idec: p.idec,
            start: p.start, end: p.end, isPlayableNow: p.isPlayableNow
          }
        };
      }
      var e = entry.item;
      var live = { kind: 'dvbLive', channelId: ch.id, title: ch.name };
      var recordingId = e.recording ? e.recording.id : null;
      switch (CT.DvbDecisions.decide(e, now)) {
        case 'future': return { type: 'notice', text: CT.Messages.FUTURE };
        case 'live': return { type: 'choice', live: live, fromStart: null };
        case 'live_or_startover':
          return {
            type: 'choice',
            live: live,
            fromStart: { kind: 'dvbRecorded', channelId: ch.id, title: e.title, recordingId: recordingId, fromStart: true }
          };
        case 'recording':
          return {
            type: 'play',
            request: { kind: 'dvbRecorded', channelId: ch.id, title: e.title, recordingId: recordingId, fromStart: false }
          };
        default: return { type: 'notice', text: CT.DvbMessages.NOT_RECORDED };
      }
    },

    /** Progress 0..1 of an airing item. */
    progress: function (item, now) {
      var span = item.end - item.start;
      if (span <= 0) return 0;
      return Math.min(1, Math.max(0, (now - item.start) / span));
    }
  };

  CT.Entries = Entries;
})(typeof window !== 'undefined' ? window : global);
