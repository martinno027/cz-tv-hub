/*
 * "Nastavení" is a choice of two sections (spec/apps.md); OK opens the chosen one full screen (the app's tabs are
 * hidden), Return goes back to the choice. "Kanály" = which channels this TV shows, and in what order (rows
 * [☑ name] [▲] [▼] per group); "Server" = gateway address and key for "Anténa" (saving tests the gateway right
 * away) and "O aplikaci".
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  function $(id) {
    return document.getElementById(id);
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function SettingsScreen(app) {
    this.app = app;
    this.items = ['set-address', 'set-key', 'set-save', 'set-clear'].map($);
    this.focus = 0;
    this.section = 'channels'; // the choice item focused, or the section open: 'channels' | 'server'
    this.zone = 'choice'; // 'choice' | 'channels' | 'form' (the Server fields and buttons)
    this.chItems = []; // [{key, keys (the group's), controls: [name, up, down], enabled: [bool ×3]}] | {reset, controls}
    this.chRow = 0;
    this.chCol = 0;
    var self = this;
    this.items.slice(0, 2).forEach(function (input) {
      // Leaving the on-screen keyboard (Done / Cancel / Enter) returns to remote navigation.
      input.addEventListener('blur', function () { self.applyFocus(); });
    });
  }

  SettingsScreen.prototype.show = function () {
    var s = CT.SettingsStore.load();
    $('set-address').value = s ? s.address : '';
    $('set-key').value = s ? s.key : '';
    this.closeSection();
    this.showStatus();
    this.renderChannels();
  };

  /** Opens a section full screen, focus on its first control. */
  SettingsScreen.prototype.openSection = function (section) {
    this.section = section;
    this.setOpen(section);
    if (section === 'channels') {
      this.zone = 'channels';
      this.chRow = 0;
      this.chCol = 0;
    } else {
      this.zone = 'form';
      this.focus = 0;
    }
    this.applyFocus();
  };

  /** Back to the choice, focus on the item of the section just left. */
  SettingsScreen.prototype.closeSection = function () {
    this.zone = 'choice';
    this.setOpen(null);
    this.applyFocus();
  };

  SettingsScreen.prototype.setOpen = function (section) {
    $('stage').classList.toggle('section-open', section !== null);
    $('set-section-channels').classList.toggle('active', section === 'channels');
    $('set-section-server').classList.toggle('active', section === 'server');
    $('screen-settings').scrollTop = 0;
  };

  /** Called by the app before its own Return handling; true = handled (a section was closed). */
  SettingsScreen.prototype.onBack = function () {
    if (this.zone === 'choice') return false;
    this.closeSection();
    return true;
  };

  /** The "Kanály" lists; keeps focus on the same channel and control across re-renders. */
  SettingsScreen.prototype.renderChannels = function () {
    var app = this.app;
    var box = $('set-channels');
    var current = this.chItems[this.chRow];
    var focusKey = current ? (current.reset ? 'reset' : current.key) : null;
    box.innerHTML = '';
    this.chItems = [];

    var dvb = app.dvbConfig ? app.dvbState : null;
    var ctList = app.ctChannels(true);
    if (dvb) box.appendChild(el('div', 'ch-group', 'Internet ČT'));
    this.groupRows(box, ctList.map(function (ch) { return { key: 'ct:' + ch.id, name: ch.name }; }));

    if (dvb) {
      box.appendChild(el('div', 'ch-group', 'Anténa'));
      var dvbList = app.dvbChannelList(dvb.channels, true);
      if (dvbList.length) {
        this.groupRows(box, dvbList.map(function (ch) { return { key: 'dvb:' + ch.id, name: ch.name }; }));
      } else if (dvb.error) {
        box.appendChild(el('div', 'ch-message error', 'Kanály antény se nepodařilo načíst: ' + dvb.error));
      } else {
        box.appendChild(el('div', 'ch-message', 'Načítám kanály antény…'));
      }
    }

    var reset = el('div', 'button', 'Obnovit výchozí pořadí');
    box.appendChild(reset);
    this.chItems.push({ reset: true, controls: [reset], enabled: [true] });

    var restored = -1;
    this.chItems.forEach(function (item, i) {
      if ((item.reset && focusKey === 'reset') || (!item.reset && item.key === focusKey)) restored = i;
    });
    this.chRow = restored >= 0 ? restored : Math.min(this.chRow, this.chItems.length - 1);
    // After ▲/▼ focus stays on that button even when it is now disabled (moved to the edge).
    this.chCol = Math.min(this.chCol, this.chItems[this.chRow].controls.length - 1);
    this.applyFocus();
  };

  SettingsScreen.prototype.groupRows = function (box, channels) {
    var self = this;
    var keys = channels.map(function (c) { return c.key; });
    var prefs = this.app.channelPrefs;
    channels.forEach(function (c, i) {
      var hidden = CT.ChannelPrefs.isHidden(prefs, c.key);
      var row = el('div', 'ch-row' + (hidden ? ' off' : ''));
      var name = el('div', 'ch-name', (hidden ? '☐ ' : '☑ ') + c.name);
      var up = el('div', 'ch-btn' + (i === 0 ? ' disabled' : ''), '▲');
      var down = el('div', 'ch-btn' + (i === channels.length - 1 ? ' disabled' : ''), '▼');
      row.appendChild(name);
      row.appendChild(up);
      row.appendChild(down);
      box.appendChild(row);
      self.chItems.push({ key: c.key, keys: keys, controls: [name, up, down],
        enabled: [true, i > 0, i < channels.length - 1] });
    });
  };

  /** OK on a "Kanály" control: toggle the channel, move it, or reset everything. */
  SettingsScreen.prototype.activateChannel = function () {
    var item = this.chItems[this.chRow];
    var P = CT.ChannelPrefs;
    var prefs = this.app.channelPrefs;
    if (!item || !item.enabled[this.chCol]) return;
    if (item.reset) prefs = P.reset();
    else if (this.chCol === 0) prefs = P.toggle(prefs, item.key);
    else prefs = P.move(item.keys, prefs, item.key, this.chCol === 1 ? -1 : 1);
    this.app.setChannelPrefs(prefs); // re-renders this list; focus follows the row (same key, same control)
  };

  /** Arrow keys in "Kanály" (Return closes the section, see onBack). */
  SettingsScreen.prototype.onChannelsKey = function (key) {
    var item = this.chItems[this.chRow];
    var col;
    switch (key) {
      case 'up':
      case 'down': {
        var next = this.chRow + (key === 'up' ? -1 : 1);
        if (next < 0 || next >= this.chItems.length) break;
        this.chRow = next;
        var target = this.chItems[next];
        // a disabled ▲/▼ (first/last row) or the single-control reset row: fall back to the name
        if (this.chCol >= target.controls.length || !target.enabled[this.chCol]) this.chCol = 0;
        break;
      }
      case 'left':
        for (col = this.chCol - 1; col >= 0; col--) if (item.enabled[col]) { this.chCol = col; break; }
        break;
      case 'right':
        for (col = this.chCol + 1; col < item.controls.length; col++) if (item.enabled[col]) { this.chCol = col; break; }
        break;
      case 'enter':
        this.activateChannel();
        return true;
      default:
        break;
    }
    this.applyFocus();
    return true;
  };

  SettingsScreen.prototype.showStatus = function () {
    var box = $('set-status');
    var config = this.app.dvbConfig;
    if (!config) {
      box.textContent = 'Anténa není nastavená.';
      box.className = '';
      return;
    }
    box.textContent = 'Zjišťuji stav brány…';
    box.className = '';
    new CT.DvbClient(config, this.app.transport).status().then(function (st) {
      var parts = ['Brána je v pořádku'];
      if (!st.tvheadendReachable) parts = ['Brána odpovídá, ale TVHeadend ne'];
      if (st.tuners.length) {
        var busy = st.tuners.filter(function (t) { return t.inUse; }).length;
        parts.push('tunery: ' + st.tuners.length + ' (obsazené ' + busy + ')');
      }
      if (st.retentionDays) parts.push('záznamy se mažou po ' + st.retentionDays + ' dnech');
      box.textContent = parts.join(' · ');
      box.className = st.tvheadendReachable ? 'ok' : 'error';
    }, function (e) {
      box.textContent = e.userMessage || CT.DvbMessages.UNREACHABLE;
      box.className = 'error';
    });
  };

  SettingsScreen.prototype.save = function () {
    var address = $('set-address').value;
    var key = $('set-key').value;
    var config;
    try {
      config = CT.DvbConfig.fromSettings(address, key);
    } catch (e) {
      $('set-status').textContent = e.userMessage;
      $('set-status').className = 'error';
      return;
    }
    CT.SettingsStore.save(address.trim(), key.trim());
    this.app.setDvbConfig(config);
    this.showStatus();
  };

  SettingsScreen.prototype.clear = function () {
    CT.SettingsStore.clear();
    $('set-address').value = '';
    $('set-key').value = '';
    this.app.setDvbConfig(null);
    this.showStatus();
  };

  SettingsScreen.prototype.applyFocus = function () {
    var self = this;
    var active = this.app.inContent();
    Array.prototype.forEach.call(document.querySelectorAll('#set-choice .choice'), function (t) {
      t.classList.toggle('focused', active && self.zone === 'choice' && t.dataset.section === self.section);
    });
    this.items.forEach(function (item, i) {
      item.classList.toggle('focused', active && self.zone === 'form' && i === self.focus);
    });
    var focusedEl = null;
    this.chItems.forEach(function (item, r) {
      item.controls.forEach(function (control, c) {
        var on = active && self.zone === 'channels' && r === self.chRow && c === self.chCol;
        control.classList.toggle('focused', on);
        if (on) focusedEl = control;
      });
    });
    if (!active) return;
    if (focusedEl) CT.scrollIntoView(focusedEl);
    else $('screen-settings').scrollTop = 0;
  };

  SettingsScreen.prototype.editing = function () {
    return document.activeElement && document.activeElement.tagName === 'INPUT';
  };

  /** Returns false when the key leaves the screen upwards (to the tabs). */
  SettingsScreen.prototype.onKey = function (key) {
    if (this.editing()) {
      // While typing, only keys that end editing are handled here; the rest go to the input.
      if (key === 'done' || key === 'cancel' || key === 'enter' || key === 'back' || key === 'up' || key === 'down') {
        document.activeElement.blur();
        if (key === 'down' || key === 'done' || key === 'enter') this.focus = Math.min(this.focus + 1, this.items.length - 1);
        if (key === 'up') this.focus = Math.max(0, this.focus - 1);
        this.applyFocus();
        return true;
      }
      return 'input';
    }
    if (this.zone === 'choice') return this.onChoiceKey(key);
    if (this.zone === 'channels') return this.onChannelsKey(key);
    switch (key) {
      case 'up':
        if (this.focus > 0) this.focus = this.focus === 3 ? 1 : this.focus - 1;
        break;
      case 'down':
        this.focus = Math.min(this.focus === 2 ? 2 : this.focus + 1, this.items.length - 1);
        break;
      case 'left':
        if (this.focus === 3) this.focus = 2;
        break;
      case 'right':
        if (this.focus === 2) this.focus = 3;
        break;
      case 'enter':
        if (this.focus < 2) {
          this.items[this.focus].focus(); // opens the TV's on-screen keyboard
          return true;
        }
        if (this.focus === 2) this.save();
        else this.clear();
        break;
      default:
        break;
    }
    this.applyFocus();
    return true;
  };

  /** The choice: up/down between Kanály and Server (up from Kanály leaves to the app tabs), OK opens it. */
  SettingsScreen.prototype.onChoiceKey = function (key) {
    switch (key) {
      case 'up':
        if (this.section === 'channels') return false;
        this.section = 'channels';
        break;
      case 'down':
        this.section = 'server';
        break;
      case 'enter':
        this.openSection(this.section);
        return true;
      default:
        break;
    }
    this.applyFocus();
    return true;
  };

  CT.SettingsScreen = SettingsScreen;
})(window);
