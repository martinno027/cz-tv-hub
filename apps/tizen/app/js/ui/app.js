/*
 * App shell: tabs, key routing, periodic refresh and the shared services (CT API, schedule cache,
 * planner, optional "Anténa" gateway). Back from a screen goes to the tabs, back on the tabs exits.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var TABS = ['live', 'guide', 'settings'];
  var REFRESH_MS = 30 * 1000;

  function $(id) {
    return document.getElementById(id);
  }

  /** Scrolls an element into view inside its scroll container (Chromium 56 has no scrollIntoView options). */
  CT.scrollIntoView = function (element) {
    if (element.scrollIntoViewIfNeeded) element.scrollIntoViewIfNeeded(false);
    else element.scrollIntoView(false);
  };

  var ICONS = {
    // globe = CT internet channel, antenna = "Anténa" (DVB-T2); stroke follows the text colour
    ct: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.2 3 14.8 0 18M12 3c-3 3.2-3 14.8 0 18"/></svg>',
    dvb: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 10v11M8.5 21h7M12 10l-3.5 11M12 10l3.5 11"/><circle cx="12" cy="8" r="1.6"/><path d="M8 4.5a5 5 0 0 0 0 7M16 4.5a5 5 0 0 1 0 7M5.2 2.2a9 9 0 0 0 0 11.6M18.8 2.2a9 9 0 0 1 0 11.6"/></svg>'
  };

  /** Small icon telling where a channel comes from (spec/apps.md "Source icon"). */
  CT.sourceIcon = function (source) {
    var icon = document.createElement('span');
    icon.className = 'source-icon source-' + source;
    icon.title = source === 'dvb' ? 'Anténa' : 'Internetová TV';
    icon.innerHTML = ICONS[source] || '';
    return icon;
  };

  /** localStorage as a ResumeStore backend; a failing storage just forgets (private, per TV). */
  function localBackend(key) {
    return {
      load: function () {
        try {
          return JSON.parse(root.localStorage.getItem(key) || 'null');
        } catch (e) {
          return null;
        }
      },
      save: function (data) {
        try {
          root.localStorage.setItem(key, JSON.stringify(data));
        } catch (e) {
          // Storage full or unavailable: positions are a convenience only.
        }
      }
    };
  }

  var App = {
    now: Date.now(),
    minute: -1, // last minute tick() handled
    tab: 'live',
    zone: 'tabs', // 'tabs' | 'content'
    dvbConfig: null,
    dvbRepo: null,
    dvbChannels: [],
    ctState: { onAir: null, error: null },
    dvbState: null,
    dialog: null,
    channelPrefs: null,
    dvbThumbs: false, // the gateway has thumbnails on (from /dvb/status)
    images: null, // guide images (CT and gateway thumbnails), memory only

    start: function () {
      var self = this;
      this.transport = CT.Demo.enabled ? CT.Demo.transport : CT.xhrTransport(20000);
      this.blobTransport = CT.Demo.enabled ? CT.Demo.blobTransport : CT.xhrBlobTransport(10000);
      this.images = new CT.ImageLoader({
        fetch: function (request) { return self.fetchImage(request); },
        revoke: function (url) { root.URL.revokeObjectURL(url); }
      });
      this.api = new CT.CtApi(this.transport);
      this.schedules = new CT.ScheduleRepository(this.api);
      this.live = new CT.LiveResolver(this.api);
      this.planner = new CT.Planner(this.api, this.live, Date.now, function () {
        return self.dvbRepo ? self.dvbRepo.client : null;
      });
      this.screens = {
        live: new CT.LiveScreen(this),
        guide: new CT.GuideScreen(this),
        settings: new CT.SettingsScreen(this)
      };
      this.resumeStore = new CT.ResumeStore(localBackend('cztvhub.resume'));
      this.channelPrefs = CT.ChannelPrefsStore.load();
      this.player = new CT.Player(this);
      this.numberEntry = new CT.NumberEntry(function (n) { self.playNumber(n); });
      this.setDvbConfig(CT.Demo.enabled ? CT.Demo.dvbConfig() : CT.SettingsStore.config(), true);

      CT.Keys.register();
      document.addEventListener('keydown', function (e) { self.onKeyDown(e); });
      document.addEventListener('visibilitychange', function () {
        if (document.hidden) {
          self.player.suspend();
          return;
        }
        self.player.resume();
        // Back from the background (e.g. the next morning): the guide opens at now again.
        if (self.tab === 'guide' && !self.player.isOpen()) {
          self.now = Date.now();
          self.screens.guide.show();
        }
      });
      this.fitStage();
      root.addEventListener('resize', function () { self.fitStage(); });

      this.zone = 'content';
      this.showTab('live');
      this.refresh();
      setInterval(function () { self.refresh(); }, REFRESH_MS);
      setInterval(function () { self.tick(); }, 1000);
      this.tick();
    },

    /** The UI is laid out for 1920×1080 (the TV); a desktop browser window scales it to fit. */
    fitStage: function () {
      var scale = Math.min(root.innerWidth / 1920, root.innerHeight / 1080);
      $('stage').style.transform = root.tizen || Math.abs(scale - 1) < 0.01 ? '' : 'scale(' + scale + ')';
    },

    /** Every second: the clock; every minute also `now`, so "Dnes" follows midnight in the guide. */
    tick: function () {
      var now = Date.now();
      $('clock').textContent = CT.Days.time(now);
      var minute = Math.floor(now / 60000);
      if (minute === this.minute) return;
      this.minute = minute;
      this.now = now;
      this.screens.guide.onMinute();
    },

    inContent: function () {
      return this.zone === 'content' && !this.dialog;
    },

    setDvbConfig: function (config, initial) {
      this.dvbConfig = config;
      this.dvbRepo = config ? new CT.DvbRepository(new CT.DvbClient(config, this.transport)) : null;
      this.dvbChannels = [];
      this.dvbState = config ? { channels: [], onAir: {}, error: null } : null;
      this.dvbThumbs = false;
      this.images.clear('gw');
      this.checkThumbs();
      if (initial) return;
      this.refresh();
      this.screens.guide.reload();
    },

    /** Whether the gateway offers thumbnails (asked on setup and every few minutes; off when it does not answer). */
    checkThumbs: function () {
      var self = this;
      var repo = this.dvbRepo;
      if (!repo) return;
      this.thumbsCheckedAt = Date.now();
      repo.client.status().then(function (st) {
        if (self.dvbRepo === repo) self.setDvbThumbs(st.thumbnails);
      }, function () {
        if (self.dvbRepo === repo) self.setDvbThumbs(false);
      });
    },

    setDvbThumbs: function (on) {
      if (this.dvbThumbs === on) return;
      this.dvbThumbs = on;
      if (!on) this.images.clear('gw');
      this.screens.guide.refreshDetail();
      if (this.tab === 'settings') this.screens.settings.renderThumbs();
    },

    /** Thumbnails shown on this TV: the gateway offers them and this TV's switch is on. */
    thumbsOn: function () {
      return this.dvbThumbs && CT.ThumbsSwitch.on();
    },

    /**
     * One guide image as an object URL (ImageLoader's fetch): a CT image from CT's CDN (no key), or a recording's
     * gateway thumbnail with the key header. Resolves {status, url}; url null unless an image came back.
     */
    fetchImage: function (request) {
      var gateway = request.kind === 'gw';
      var config = this.dvbConfig;
      if (gateway && !config) return Promise.resolve({ status: 0, url: null });
      var call = gateway ? { url: config.url(CT.Thumbs.path(request.recordingId)), headers: config.authHeaders() } : { url: request.url };
      return this.blobTransport(call).then(function (r) {
        if (r.status !== 200) return { status: r.status, url: null };
        if (!r.blob || !(gateway ? /^image\/jpeg/ : /^image\//).test(r.type)) return { status: 415, url: null };
        return { status: 200, url: root.URL.createObjectURL(r.blob) };
      });
    },

    /** Every 30 s: now, what is on air (CT and Anténa), guide colours. */
    refresh: function () {
      var self = this;
      this.now = Date.now();
      this.schedules.onAir(this.now).then(function (onAir) {
        self.ctState = { onAir: onAir, error: null };
      }, function (e) {
        // Keep showing the last good data if a later refresh fails.
        self.ctState = { onAir: self.ctState.onAir, error: e.userMessage || CT.Messages.SCHEDULE_FAILED };
      }).then(function () { self.renderLive(); });

      var repo = this.dvbRepo;
      if (repo) {
        Promise.all([repo.channels(), repo.onAir(this.now)]).then(function (r) {
          if (self.dvbRepo !== repo) return;
          self.dvbChannels = r[0];
          self.dvbState = { channels: r[0], onAir: r[1], error: null };
        }, function (e) {
          if (self.dvbRepo !== repo) return;
          self.dvbState = {
            channels: self.dvbState ? self.dvbState.channels : [],
            onAir: self.dvbState ? self.dvbState.onAir : {},
            error: e.userMessage || CT.DvbMessages.GENERIC
          };
        }).then(function () { self.renderLive(); });
      }
      this.screens.guide.refreshLooks();
      if (this.dvbRepo && Date.now() - (this.thumbsCheckedAt || 0) > 5 * 60 * 1000) this.checkThumbs();
    },

    renderLive: function () {
      this.screens.live.render(this.ctState, this.dvbState, this.now);
      if (this.tab === 'settings') this.screens.settings.renderChannels();
    },

    /** A whole source ('ct' Internetová TV, 'dvb' Anténa) is on; Anténa also needs a gateway. */
    sourceOn: function (source) {
      if (source === 'dvb' && !this.dvbConfig) return false;
      return !CT.ChannelPrefs.isSourceOff(this.channelPrefs, source);
    },

    /**
     * "Kanály": one mixed list of the sources that are on, in this TV's order: [{key, source, channel}]
     * (`ct:…` / `dvb:…`). `dvbChannels` = the gateway's channels; `all` includes the hidden ones.
     */
    channelList: function (dvbChannels, all) {
      var byKey = {};
      var keys = [];
      function add(source, channel) {
        var key = source + ':' + channel.id;
        byKey[key] = { key: key, source: source, channel: channel };
        keys.push(key);
      }
      if (this.sourceOn('ct')) CT.Channels.all.forEach(function (ch) { add('ct', ch); });
      if (this.sourceOn('dvb')) (dvbChannels || []).forEach(function (ch) { add('dvb', ch); });
      var arranged = CT.ChannelPrefs.arrange(keys, this.channelPrefs);
      return (all ? arranged.all : arranged.visible).map(function (k) { return byKey[k]; });
    },

    /** Live request for a channelList item (Živě card, number entry, zapping). */
    liveRequest: function (item) {
      var ch = item.channel;
      return { kind: item.source === 'dvb' ? 'dvbLive' : 'live', channelId: ch.id, title: ch.name };
    },

    /** The programme airing now on a channelList item, or null (from the guide data kept fresh by refresh). */
    airingNow: function (item) {
      var state = item.source === 'dvb' ? this.dvbState : this.ctState;
      var info = state && state.onAir ? state.onAir[item.channel.id] : null;
      return info ? info.current : null;
    },

    /** Number entry on Živě and the guide: that channel live, or "Kanál <n> není". */
    playNumber: function (number) {
      var list = this.channelList(this.dvbChannels);
      var key = CT.ChannelNumbers.resolve(list.map(function (item) { return item.key; }), number);
      if (!key) {
        this.numberEntry.notFound(number);
        return;
      }
      this.play(this.liveRequest(list[number - 1]));
    },

    /** Stores new "Kanály" prefs and redraws Živě and the guide. */
    setChannelPrefs: function (prefs) {
      this.channelPrefs = prefs;
      CT.ChannelPrefsStore.save(prefs);
      this.renderLive();
      this.screens.guide.reload();
    },

    showTab: function (tab) {
      this.tab = tab;
      Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (t) {
        t.classList.toggle('selected', t.dataset.tab === tab);
      });
      Array.prototype.forEach.call(document.querySelectorAll('.screen'), function (s) {
        s.classList.toggle('active', s.id === 'screen-' + tab);
      });
      var screen = this.screens[tab];
      if (screen.show) screen.show();
      this.applyFocus();
    },

    applyFocus: function () {
      var self = this;
      Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (t) {
        t.classList.toggle('focused', self.zone === 'tabs' && t.dataset.tab === self.tab);
      });
      this.screens[this.tab].applyFocus();
    },

    onKeyDown: function (event) {
      var key = CT.Keys.name(event);
      if (this.player.isOpen()) {
        if (key) {
          event.preventDefault();
          this.player.onKey(key);
        }
        return;
      }
      if (this.dialog) {
        if (key) event.preventDefault();
        this.onDialogKey(key);
        return;
      }
      if (!key) return;
      if (this.onNumberKey(key, event)) return;
      if (this.zone === 'content') {
        var screen = this.screens[this.tab];
        if (key === 'back' && !(screen.editing && screen.editing())) {
          event.preventDefault();
          if (screen.onBack && screen.onBack()) return; // e.g. closing a Nastavení section
          this.zone = 'tabs';
          this.applyFocus();
          return;
        }
        var handled = screen.onKey(key);
        if (handled === 'input') return; // typing into a field
        event.preventDefault();
        if (handled === false) {
          this.zone = 'tabs';
          this.applyFocus();
        }
        return;
      }
      event.preventDefault();
      var i = TABS.indexOf(this.tab);
      switch (key) {
        case 'left':
          if (i > 0) this.showTab(TABS[i - 1]);
          break;
        case 'right':
          if (i < TABS.length - 1) this.showTab(TABS[i + 1]);
          break;
        case 'down':
        case 'enter':
          this.zone = 'content';
          this.applyFocus();
          break;
        case 'back':
          CT.Keys.exitApp();
          break;
        default:
          break;
      }
    },

    /** Digits (and OK/Return while typing) on Živě and the guide; true = handled. Other screens ignore digits. */
    onNumberKey: function (key, event) {
      var digit = CT.Keys.digit(key);
      var screen = this.screens[this.tab];
      if (digit < 0) {
        if (!this.numberEntry.active() || (key !== 'enter' && key !== 'back')) return false;
        event.preventDefault();
        if (key === 'enter') this.numberEntry.commit();
        else this.numberEntry.cancel();
        return true;
      }
      if (screen.editing && screen.editing()) return false; // typing into a field
      event.preventDefault();
      if (this.tab === 'live' || this.tab === 'guide') this.numberEntry.digit(digit, this.channelList(this.dvbChannels).length);
      return true;
    },

    /** What selecting a guide cell does (CT or Anténa). */
    select: function (entry) {
      var s = CT.Entries.select(entry, Date.now(), this.resumeStore);
      if (s.type === 'notice') return this.toast(s.text);
      if (s.type === 'play') return this.play(s.request);
      if (!s.fromStart && !s.resume) return this.play(s.live);
      var buttons = [];
      if (s.resume) buttons.push({ label: 'Pokračovat od ' + s.resumeLabel, request: s.resume });
      if (s.fromStart) buttons.push({ label: 'Od začátku', request: s.fromStart });
      if (s.live) buttons.push({ label: 'Živě', request: s.live });
      this.openDialog(entry.title, buttons);
    },

    play: function (request) {
      this.numberEntry.cancel();
      this.player.open(request);
    },

    /** `request` = what was playing last (after zapping: the channel switched to). */
    onPlayerClosed: function (request) {
      if (request && (request.kind === 'live' || request.kind === 'dvbLive')) {
        this.screens.live.focusKey((request.kind === 'dvbLive' ? 'dvb:' : 'ct:') + request.channelId);
      }
      this.refresh();
    },

    openDialog: function (title, buttons) {
      var box = $('dialog-buttons');
      box.innerHTML = '';
      buttons.forEach(function (b) {
        var el = document.createElement('div');
        el.className = 'button';
        el.textContent = b.label;
        box.appendChild(el);
        b.el = el;
      });
      $('dialog-title').textContent = title;
      $('dialog').classList.remove('hidden');
      this.dialog = { buttons: buttons, focus: 0 };
      this.applyDialogFocus();
      this.applyFocus();
    },

    applyDialogFocus: function () {
      var d = this.dialog;
      d.buttons.forEach(function (b, i) { b.el.classList.toggle('focused', i === d.focus); });
    },

    closeDialog: function () {
      this.dialog = null;
      $('dialog').classList.add('hidden');
      this.applyFocus();
    },

    onDialogKey: function (key) {
      var d = this.dialog;
      switch (key) {
        case 'left':
          d.focus = Math.max(0, d.focus - 1);
          break;
        case 'right':
          d.focus = Math.min(d.buttons.length - 1, d.focus + 1);
          break;
        case 'enter': {
          var request = d.buttons[d.focus].request;
          this.closeDialog();
          this.play(request);
          return;
        }
        case 'back':
          this.closeDialog();
          return;
        default:
          break;
      }
      this.applyDialogFocus();
    },

    toast: function (text) {
      var t = $('toast');
      t.textContent = text;
      t.classList.add('visible');
      clearTimeout(this.toastTimer);
      this.toastTimer = setTimeout(function () { t.classList.remove('visible'); }, 3000);
    }
  };

  CT.App = App;
  root.addEventListener('load', function () { App.start(); });
})(window);
