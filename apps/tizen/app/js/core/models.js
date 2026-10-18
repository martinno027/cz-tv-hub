/*
 * Shared namespace `CT` for the core modules. Plain scripts (no ES modules: Tizen 4.0 = Chromium 56),
 * loaded in order by index.html and by test/load.js in Node.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  /** A channel of the CT live API. `slug` is its name in the programme (GraphQL) API. */
  // CH_7 (ČT3) returned 404 on 2026-10-17; re-check before adding it back.
  CT.Channels = {
    all: [
      { id: 'CH_1', name: 'ČT1', slug: 'ct1' },
      { id: 'CH_2', name: 'ČT2', slug: 'ct2' },
      { id: 'CH_24', name: 'ČT24', slug: 'ct24' },
      { id: 'CH_4', name: 'ČT sport', slug: 'sport' },
      { id: 'CH_5', name: 'ČT :D', slug: 'decko' },
      { id: 'CH_6', name: 'ČT art', slug: 'art' }
    ],
    byId: function (id) {
      return CT.Channels.all.filter(function (c) { return c.id === id; })[0] || null;
    }
  };

  CT.Messages = {
    GENERIC: 'Stream se nepodařilo načíst',
    BLOCKED: 'Vysílání je blokováno',
    VIDEO_UNAVAILABLE: 'Video není k dispozici',
    SCHEDULE_FAILED: 'Program se nepodařilo načíst',
    FUTURE: 'Pořad ještě nezačal',
    NO_TIMESHIFT: 'Zpět v čase není pro tento kanál k dispozici',
    DRM_HINT: 'Pořad můžete pustit v oficiální aplikaci iVysílání.',
    STILL_LIVE: 'Pořad se právě vysílá',
    PLAYBACK: 'Přehrávání se nezdařilo'
  };

  CT.DvbMessages = {
    UNREACHABLE: 'Brána antény není dostupná',
    BAD_KEY: 'Chybný klíč brány',
    NOT_RECORDED: 'Pořad nebyl nahrán',
    GENERIC: 'Anténu se nepodařilo načíst',
    HOST_NOT_ALLOWED: 'Adresa brány musí být v domácí síti nebo v tailnetu',
    KEY_TOO_SHORT: 'Klíč brány má alespoň 32 znaků'
  };

  /**
   * Nothing playable. `userMessage` is short Czech text for the viewer (CT's own wording when the
   * API provides one); `status` is CT's `message.status.name` or the gateway's HTTP status.
   */
  class CtError extends Error {
    constructor(message, userMessage, status) {
      super(message);
      this.name = 'CtError';
      this.userMessage = userMessage || CT.Messages.GENERIC;
      this.status = status || null;
    }
  }

  /** The archive item is airing right now on `channelId`; play it from the rewind buffer instead. */
  class StillLiveError extends CtError {
    constructor(idec, channelId) {
      super(idec + ' is airing live on ' + channelId, CT.Messages.STILL_LIVE);
      this.name = 'StillLiveError';
      this.idec = idec;
      this.channelId = channelId;
    }
  }

  CT.CtError = CtError;
  CT.StillLiveError = StillLiveError;

  /** Defensive JSON access: the APIs are undocumented, so every field is optional. */
  CT.Json = {
    parse: function (body) {
      try {
        var value = JSON.parse(body);
        return value !== null && typeof value === 'object' && !Array.isArray(value) ? value : null;
      } catch (e) {
        return null;
      }
    },
    obj: function (o, key) {
      var v = o ? o[key] : null;
      return v !== null && typeof v === 'object' && !Array.isArray(v) ? v : null;
    },
    arr: function (o, key) {
      var v = o ? o[key] : null;
      return Array.isArray(v) ? v : [];
    },
    /** Non-empty string (numbers and booleans as text, like the other apps), else null. */
    str: function (o, key) {
      var v = o ? o[key] : null;
      if (typeof v === 'string') return v === '' ? null : v;
      if (typeof v === 'number' || typeof v === 'boolean') return String(v);
      return null;
    },
    bool: function (o, key) {
      var v = o ? o[key] : null;
      return v === true || v === 'true';
    },
    /** Epoch ms of an ISO-8601 timestamp with a zone, else null. */
    instant: function (value) {
      if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d(\.\d+)?)?(Z|[+-]\d\d:\d\d)$/.test(value)) {
        return null;
      }
      var ms = Date.parse(value);
      return isNaN(ms) ? null : ms;
    }
  };
})(typeof window !== 'undefined' ? window : global);
