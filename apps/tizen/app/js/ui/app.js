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
    tab: 'live',
    zone: 'tabs', // 'tabs' | 'content'
    dvbConfig: null,
    dvbRepo: null,
    dvbChannels: [],
    ctState: { onAir: null, error: null },
    dvbState: null,
    dialog: null,

    start: function () {
      var self = this;
      this.transport = CT.Demo.enabled ? CT.Demo.transport : CT.xhrTransport(20000);
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
      this.player = new CT.Player(this);
      this.setDvbConfig(CT.Demo.enabled ? CT.Demo.dvbConfig() : CT.SettingsStore.config(), true);

      CT.Keys.register();
      document.addEventListener('keydown', function (e) { self.onKeyDown(e); });
      document.addEventListener('visibilitychange', function () {
        if (document.hidden) self.player.suspend();
        else self.player.resume();
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

    tick: function () {
      $('clock').textContent = CT.Days.time(Date.now());
    },

    inContent: function () {
      return this.zone === 'content' && !this.dialog;
    },

    setDvbConfig: function (config, initial) {
      this.dvbConfig = config;
      this.dvbRepo = config ? new CT.DvbRepository(new CT.DvbClient(config, this.transport)) : null;
      this.dvbChannels = [];
      this.dvbState = config ? { channels: [], onAir: {}, error: null } : null;
      if (initial) return;
      this.refresh();
      this.screens.guide.reload();
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
    },

    renderLive: function () {
      this.screens.live.render(this.ctState, this.dvbState, this.now);
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
      if (this.zone === 'content') {
        var screen = this.screens[this.tab];
        if (key === 'back' && !(screen.editing && screen.editing())) {
          event.preventDefault();
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
      this.player.open(request);
    },

    onPlayerClosed: function () {
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
