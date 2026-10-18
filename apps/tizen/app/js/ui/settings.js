/* "Nastavení": gateway address and key for "Anténa"; saving tests the gateway right away. */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  function $(id) {
    return document.getElementById(id);
  }

  function SettingsScreen(app) {
    this.app = app;
    this.items = ['set-address', 'set-key', 'set-save', 'set-clear'].map($);
    this.focus = 0;
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
    this.showStatus();
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
    this.items.forEach(function (item, i) {
      item.classList.toggle('focused', active && i === self.focus);
    });
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
    switch (key) {
      case 'up':
        if (this.focus === 0) return false;
        this.focus = this.focus === 3 ? 1 : this.focus - 1;
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

  CT.SettingsScreen = SettingsScreen;
})(window);
