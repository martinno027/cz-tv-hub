/*
 * "Nastavení" is a choice of two sections (spec/apps.md); OK opens the chosen one full screen (the app's tabs are
 * hidden), Return goes back to the choice. "Kanály" = the source switches (Internetová TV, Anténa) and which
 * channels this TV shows in what order (one mixed list of rows [icon ☑ name] [▲] [▼]); "Server" = gateway address
 * and key for "Anténa" (saving tests the gateway right away) and "O aplikaci". The choice screen starts with the
 * version line (js/build-info.js, written by build.py; "vývojová verze" without it).
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
    // 0 address, 1 key, 2 save, 3 clear, 4 "Náhledy anténních pořadů" (only when the gateway offers them)
    this.items = ['set-address', 'set-key', 'set-save', 'set-clear', 'set-thumbs'].map($);
    this.focus = 0;
    this.section = 'channels'; // the choice item focused, or the section open: 'channels' | 'server'
    this.zone = 'choice'; // 'choice' | 'channels' | 'form' (the Server fields and buttons)
    // [{key, keys (the list's), controls: [name, up, down], enabled: [bool ×3]}] | {source, controls} | {reset, controls}
    this.chItems = [];
    this.chRow = 0;
    this.chCol = 0;
    var self = this;
    this.items.slice(0, 2).forEach(function (input) {
      // Leaving the on-screen keyboard (Done / Cancel / Enter) returns to remote navigation.
      input.addEventListener('blur', function () { self.applyFocus(); });
    });
  }

  /** "Verze 0.1.0 (build 123 · abc1234 · 23. 10. 15:20)" from the build; spec/apps.md "Version line". */
  SettingsScreen.versionText = function (info) {
    if (!info || !info.version) return 'Verze: vývojová verze';
    return 'Verze ' + info.version + ' (build ' + info.build + ' · ' + info.commit + ' · ' + info.time + ')';
  };

  SettingsScreen.prototype.show = function () {
    $('version-line').textContent = SettingsScreen.versionText(root.CT_BUILD_INFO);
    var s = CT.SettingsStore.load();
    $('set-address').value = s ? s.address : '';
    $('set-key').value = s ? s.key : '';
    this.closeSection();
    this.showStatus();
    this.renderChannels();
    this.renderThumbs();
  };

  /** The TV's thumbnail switch, shown only when the gateway has thumbnails on (spec/apps.md). */
  SettingsScreen.prototype.renderThumbs = function () {
    var toggle = $('set-thumbs');
    var visible = this.app.dvbThumbs === true;
    toggle.classList.toggle('hidden', !visible);
    toggle.textContent = (CT.ThumbsSwitch.on() ? '☑ ' : '☐ ') + 'Náhledy anténních pořadů';
    if (!visible && this.focus === 4) {
      this.focus = 2;
      this.applyFocus();
    }
  };

  SettingsScreen.prototype.toggleThumbs = function () {
    CT.ThumbsSwitch.set(!CT.ThumbsSwitch.on());
    this.renderThumbs();
    this.app.screens.guide.refreshDetail();
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

  /** "Kanály": source switches, then the mixed list; keeps focus on the same item and control across re-renders. */
  SettingsScreen.prototype.renderChannels = function () {
    var self = this;
    var app = this.app;
    var box = $('set-channels');
    var current = this.chItems[this.chRow];
    var focusKey = current ? this.itemKey(current) : null;
    box.innerHTML = '';
    this.chItems = [];

    var dvb = app.dvbConfig ? app.dvbState : null;
    var sources = [{ source: 'ct', label: 'Internetová TV (ČT)' }];
    if (dvb) sources.push({ source: 'dvb', label: 'Anténa' });
    sources.forEach(function (s) {
      var on = !CT.ChannelPrefs.isSourceOff(app.channelPrefs, s.source);
      var row = el('div', 'ch-row ch-source' + (on ? '' : ' off'));
      var name = el('div', 'ch-name');
      name.appendChild(document.createTextNode(on ? '☑ ' : '☐ '));
      name.appendChild(CT.sourceIcon(s.source));
      name.appendChild(document.createTextNode(s.label));
      row.appendChild(name);
      box.appendChild(row);
      self.chItems.push({ source: s.source, controls: [name], enabled: [true] });
    });

    box.appendChild(el('div', 'ch-group', 'Pořadí kanálů'));
    if (dvb && app.sourceOn('dvb') && !dvb.channels.length) {
      box.appendChild(dvb.error
        ? el('div', 'ch-message error', 'Kanály antény se nepodařilo načíst: ' + dvb.error)
        : el('div', 'ch-message', 'Načítám kanály antény…'));
    }
    var list = app.channelList(dvb ? dvb.channels : [], true);
    if (list.length) {
      this.listRows(box, list);
    } else {
      box.appendChild(el('div', 'ch-message', 'Žádný kanál: zapněte Internetovou TV nebo Anténu.'));
    }

    var reset = el('div', 'button', 'Obnovit výchozí pořadí');
    box.appendChild(reset);
    this.chItems.push({ reset: true, controls: [reset], enabled: [true] });

    var restored = -1;
    this.chItems.forEach(function (item, i) {
      if (self.itemKey(item) === focusKey) restored = i;
    });
    this.chRow = restored >= 0 ? restored : Math.min(this.chRow, this.chItems.length - 1);
    // After ▲/▼ focus stays on that button even when it is now disabled (moved to the edge).
    this.chCol = Math.min(this.chCol, this.chItems[this.chRow].controls.length - 1);
    this.applyFocus();
  };

  SettingsScreen.prototype.itemKey = function (item) {
    if (item.reset) return 'reset';
    return item.source && !item.key ? 'source:' + item.source : item.key;
  };

  SettingsScreen.prototype.listRows = function (box, list) {
    var self = this;
    var keys = list.map(function (item) { return item.key; });
    var prefs = this.app.channelPrefs;
    list.forEach(function (item, i) {
      var hidden = CT.ChannelPrefs.isHidden(prefs, item.key);
      var row = el('div', 'ch-row' + (hidden ? ' off' : ''));
      var name = el('div', 'ch-name');
      name.appendChild(document.createTextNode(hidden ? '☐ ' : '☑ '));
      name.appendChild(CT.sourceIcon(item.source));
      name.appendChild(document.createTextNode(item.channel.name));
      var up = el('div', 'ch-btn' + (i === 0 ? ' disabled' : ''), '▲');
      var down = el('div', 'ch-btn' + (i === list.length - 1 ? ' disabled' : ''), '▼');
      row.appendChild(name);
      row.appendChild(up);
      row.appendChild(down);
      box.appendChild(row);
      self.chItems.push({ key: item.key, keys: keys, controls: [name, up, down],
        enabled: [true, i > 0, i < list.length - 1] });
    });
  };

  /** OK on a "Kanály" control: switch a source, toggle the channel, move it, or reset everything. */
  SettingsScreen.prototype.activateChannel = function () {
    var item = this.chItems[this.chRow];
    var P = CT.ChannelPrefs;
    var prefs = this.app.channelPrefs;
    if (!item || !item.enabled[this.chCol]) return;
    if (item.reset) prefs = P.reset();
    else if (!item.key) prefs = P.setSource(prefs, item.source, P.isSourceOff(prefs, item.source));
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
    var app = this.app;
    new CT.DvbClient(config, this.app.transport).status().then(function (st) {
      if (app.dvbConfig === config) app.setDvbThumbs(st.thumbnails);
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
        if (this.focus === 4) this.focus = 2;
        else if (this.focus > 0) this.focus = this.focus === 3 ? 1 : this.focus - 1;
        break;
      case 'down':
        if (this.focus >= 2) {
          if (this.app.dvbThumbs === true) this.focus = 4;
        } else {
          this.focus += 1;
        }
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
        else if (this.focus === 3) this.clear();
        else this.toggleThumbs();
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
