/*
 * "Domů" (spec/apps.md "Domů", T58): the first tab. The date with the name day and calendar lines and the weather
 * now (T59), the gateway notice, then rows of cards: "Pokračovat" (Archiv's row), "Teď běží" (the first channels as
 * on Živě, then "Další kanály") and "Sněmovna" (the Chamber streams live now), and "Počasí na týden" (not a focus
 * stop). A row with nothing to show is left out. Nothing is fetched for the rows: the app's on-air data, the resume
 * store and the last /dvb/status check; the weather is asked for when Domů is shown (app.weather, at most every 30 min).
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var H = CT.Home;
  var Cards = CT.ArchiveCards;
  var Menu = Cards.Menu;
  var REVEAL_MARGIN = 16; // px kept beside the focused card (its scale and border)

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function HomeScreen(app) {
    var self = this;
    this.app = app;
    this.root = document.getElementById('screen-home');
    this.rows = []; // [{id, items: [{key, el, action, entry?}], strip}]
    this.at = { row: 0, col: 0 };
    this.visible = false;
    this.header = null;
    this.consentAsked = false; // the location question was on screen in this run (Return leaves it undecided)
    // OK on a "Pokračovat" card: short = play from the position, long = "Odebrat z řady" (as in Archiv)
    this.press = new Cards.LongPress(function () { self.activate(); }, function () { self.offerRemove(); });
  }

  /** The tab is shown: opened anew on the first card of the first row, a redraw keeps the focus. */
  HomeScreen.prototype.show = function () {
    var opening = !this.visible;
    this.visible = true;
    this.render(opening);
    if (opening) this.root.scrollTop = 0;
    if (opening && this.offerLocation()) return; // the weather waits for the answer
    this.app.weather.refresh();
  };

  /**
   * Once, before the first OS prompt (spec/apps.md "Location for weather"): Povolit = the switch on and the location
   * asked for, Nepoužívat = never asked, the gateway's household place. True while the question is on screen.
   */
  HomeScreen.prototype.offerLocation = function () {
    var self = this;
    var app = this.app;
    if (this.consentAsked || Menu.isOpen() || !app.weatherConsentDue()) return false;
    this.consentAsked = true;
    app.zone = 'content'; // the question has the keys
    app.askWeatherLocation(function (chosen) {
      if (!chosen && self.visible) app.weather.refresh(); // Return: no answer, without the location until the next start
    });
    return true;
  };

  HomeScreen.prototype.hide = function () {
    this.visible = false;
    Menu.close();
  };

  /** Everything again (data changed); the focus stays on the same card, else as near as the rows allow. */
  HomeScreen.prototype.render = function (fresh) {
    var app = this.app;
    var focused = fresh ? null : this.focused();
    var focusedRow = fresh ? null : (this.rows[this.at.row] || {}).id;
    // where the screen and each row were scrolled (a redraw every 30 s must not move what the viewer looks at)
    var scrolled = { top: fresh ? 0 : this.root.scrollTop, rows: {} };
    if (!fresh) this.rows.forEach(function (row) { scrolled.rows[row.id] = row.strip.scrollLeft; });
    this.root.innerHTML = '';
    this.rows = [];

    this.header = this.root.appendChild(el('div', 'home-header'));
    this.renderHeader();
    var notice = H.notice(!!app.dvbConfig, app.dvbStatus);
    if (notice) this.root.appendChild(el('div', 'home-notice', notice));

    var list = app.channelList(app.dvbChannels);
    // exactly Archiv's row: the same entries, no rule of its own
    this.addRow('continue', 'Pokračovat', app.resumeStore.continueRow().map(function (entry) {
      return {
        key: entry.key, entry: entry, el: Cards.element(app, Cards.fromResume(entry)),
        action: function () { app.play(CT.Archive.continueRequest(entry)); }
      };
    }));
    var ct = app.ctState;
    var ctFailed = !!ct.error && !ct.onAir;
    var now = app.now;
    var channels = H.nowItems(list).map(function (x) {
      var state = x.item.source === 'dvb' ? app.dvbState : ct;
      var info = state && state.onAir ? state.onAir[x.item.channel.id] : null;
      return {
        key: x.item.key, el: CT.LiveScreen.channelCard(x.item, x.number, info || null, now, x.item.source === 'ct' && ctFailed),
        action: function () { app.playItem(x.item); }
      };
    });
    if (channels.length) {
      channels.push({ key: 'more', el: el('div', 'card home-more', 'Další kanály'), action: function () { app.showTab('live'); } });
    }
    this.addRow('now', 'Teď běží', channels);
    this.addRow('psp', 'Sněmovna', H.pspItems(list, function (id) { return app.pspState(id); }).map(function (x) {
      return {
        key: x.item.key, el: CT.LiveScreen.pspCard(x.item, x.number, 'live'),
        action: function () { app.playItem(x.item); }
      };
    }));
    // block 8: shown, never focused (not in this.rows)
    var week = CT.WeatherView.week(app.weather.weather, CT.Days.today(Date.now()));
    if (week) this.root.appendChild(week);

    if (!this.restore(focused, focusedRow)) this.at = { row: 0, col: 0 };
    this.root.scrollTop = scrolled.top;
    this.rows.forEach(function (row) { row.strip.scrollLeft = scrolled.rows[row.id] || 0; });
    this.applyFocus(fresh || this.focused() !== focused); // scrolled only when the focus is new or had to move
  };

  /**
   * Today's date, name day and calendar lines, and the weather now beside them (also on every minute: the day may
   * have changed, a warning may have ended).
   */
  HomeScreen.prototype.renderHeader = function () {
    if (!this.header) return;
    var now = Date.now();
    var day = CT.Calendar.today(now);
    var source = this.app.weather;
    var weather = source.weather;
    var key = JSON.stringify(day) + '|' + source.answeredAt + '|' +
      (weather ? CT.Weather.currentWarnings(weather, now).length : '');
    if (this.header.dataset.key === key) return;
    this.header.dataset.key = key;
    this.header.innerHTML = '';
    var calendar = this.header.appendChild(el('div', 'home-calendar'));
    calendar.appendChild(el('div', 'home-date', day.fullDate));
    if (day.nameLine) calendar.appendChild(el('div', 'home-name', day.nameLine));
    day.lines.forEach(function (line) { calendar.appendChild(el('div', 'home-line', line)); });
    var box = CT.WeatherView.now(weather, now);
    if (box) this.header.appendChild(box);
  };

  HomeScreen.prototype.onMinute = function () {
    if (this.visible) this.renderHeader();
  };

  HomeScreen.prototype.addRow = function (id, title, items) {
    if (!items.length) return;
    var row = el('div', 'home-row home-row-' + id);
    row.appendChild(el('div', 'home-heading', title));
    var strip = row.appendChild(el('div', 'home-strip'));
    items.forEach(function (item) {
      item.el.classList.add('home-item');
      strip.appendChild(item.el);
    });
    this.root.appendChild(row);
    this.rows.push({ id: id, items: items, strip: strip });
  };

  /** The focus on the card of `key` (else the same column of the row `rowId`); false when neither is there. */
  HomeScreen.prototype.restore = function (key, rowId) {
    for (var r = 0; r < this.rows.length; r++) {
      for (var c = 0; c < this.rows[r].items.length; c++) {
        if (key && this.rows[r].items[c].key === key) {
          this.at = { row: r, col: c };
          return true;
        }
      }
    }
    for (r = 0; r < this.rows.length; r++) {
      if (this.rows[r].id !== rowId) continue;
      this.at = { row: r, col: Math.min(this.at.col, this.rows[r].items.length - 1) };
      return true;
    }
    return false;
  };

  /** The key of the focused card, or null. */
  HomeScreen.prototype.focused = function () {
    var row = this.rows[this.at.row];
    var item = row ? row.items[this.at.col] : null;
    return item ? item.key : null;
  };

  HomeScreen.prototype.focusedItem = function () {
    var row = this.rows[this.at.row];
    return row ? row.items[this.at.col] || null : null;
  };

  /** Back from the player: the card that was played (a live channel after zapping: that channel's card, if listed). */
  HomeScreen.prototype.onPlayerClosed = function (request) {
    if (!this.visible) return;
    var key = request && CT.LIVE_PREFIX[request.kind] ? CT.LIVE_PREFIX[request.kind] + request.channelId : null;
    if (key) this.restore(key, null);
    this.render(); // a "Pokračovat" position may have been saved or cleared
    this.applyFocus(); // and the played card in view
  };

  /** `reveal` false = the focus is drawn where it is without scrolling to it (a redraw of the same card). */
  HomeScreen.prototype.applyFocus = function (reveal) {
    var self = this;
    var on = this.app.inContent() && this.app.tab === 'home' && !Menu.isOpen();
    this.rows.forEach(function (row, r) {
      row.items.forEach(function (item, c) {
        item.el.classList.toggle('focused', on && r === self.at.row && c === self.at.col);
      });
    });
    var item = this.focusedItem();
    if (item && on && reveal !== false) this.reveal(item.el);
  };

  /**
   * The focused card in view: its row whole (heading and the focused card's border; the first row with the date
   * above it), and the card within its row's strip.
   */
  HomeScreen.prototype.reveal = function (card) {
    var strip = card.parentNode;
    var left = card.offsetLeft - REVEAL_MARGIN;
    var right = card.offsetLeft + card.offsetWidth + REVEAL_MARGIN;
    if (left < strip.scrollLeft) strip.scrollLeft = left;
    else if (right > strip.scrollLeft + strip.clientWidth) strip.scrollLeft = right - strip.clientWidth;
    var row = strip.parentNode;
    var box = this.root;
    if (this.at.row === 0) {
      box.scrollTop = 0;
      return;
    }
    if (this.at.row === this.rows.length - 1) {
      box.scrollTop = box.scrollHeight; // the last focus stop: "Počasí na týden" under it comes into view too
      return;
    }
    var bottom = row.offsetTop + row.offsetHeight;
    if (bottom > box.scrollTop + box.clientHeight) box.scrollTop = bottom - box.clientHeight;
    if (row.offsetTop < box.scrollTop) box.scrollTop = row.offsetTop;
  };

  /** Returns false when the key leaves the screen upwards (to the tabs). */
  HomeScreen.prototype.onKey = function (key) {
    if (Menu.isOpen()) {
      if (!this.press.active()) Menu.onKey(key); // not the held OK that opened it
      this.applyFocus();
      return true;
    }
    if (!this.rows.length) return key !== 'up';
    if (key === 'enter') {
      if (this.rows[this.at.row].id === 'continue') this.press.down();
      else this.activate();
      return true;
    }
    var next = H.move(this.rows.map(function (r) { return r.items.length; }), this.at, key);
    if (!next) return false;
    this.at = next;
    this.applyFocus();
    return true;
  };

  HomeScreen.prototype.onKeyUp = function (key) {
    if (key === 'enter') this.press.up();
  };

  /** Return: closes the menu; else the tabs get the focus. */
  HomeScreen.prototype.onBack = function () {
    if (!Menu.isOpen()) return false;
    Menu.close();
    this.applyFocus();
    return true;
  };

  HomeScreen.prototype.activate = function () {
    var item = this.focusedItem();
    if (item) item.action();
  };

  HomeScreen.prototype.offerRemove = function () {
    var self = this;
    var item = this.focusedItem();
    if (!item || !item.entry) return;
    var entry = item.entry;
    Menu.open(entry.showTitle ? entry.showTitle + ' · ' + entry.title : entry.title, [
      { label: 'Odebrat z řady', action: function () {
        self.app.resumeStore.remove(entry.key);
        self.render();
      } },
      { label: 'Zrušit', action: null }
    ]);
    this.applyFocus();
  };

  /** The web's click: the card element becomes the focus; false when it is not one of Domů's cards. */
  HomeScreen.prototype.focusElement = function (element) {
    for (var r = 0; r < this.rows.length; r++) {
      for (var c = 0; c < this.rows[r].items.length; c++) {
        if (this.rows[r].items[c].el !== element) continue;
        this.at = { row: r, col: c };
        return true;
      }
    }
    return false;
  };

  CT.HomeScreen = HomeScreen;
})(window);
