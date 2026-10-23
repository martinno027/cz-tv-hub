/*
 * "Živě": the channels with the programme on air now, CT internet and "Anténa" in one mixed list, in this TV's
 * order without hidden channels or switched-off sources ("Kanály" in Nastavení); each card shows its source icon.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var PER_ROW = 6;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function LiveScreen(app) {
    this.app = app;
    this.root = document.getElementById('screen-live');
    this.cards = []; // [{el, request}]
    this.groups = []; // [{start, end}] card index ranges (one: the mixed list)
    this.focus = 0;
  }

  /** Data: ct = {onAir, error}; dvb = null | {channels, onAir, error}. */
  LiveScreen.prototype.render = function (ct, dvb, now) {
    var self = this;
    var focusedKey = this.cards[this.focus] ? this.cards[this.focus].key : null;
    this.root.innerHTML = '';
    this.cards = [];
    this.groups = [];
    var app = this.app;
    var dvbOn = dvb && app.sourceOn('dvb');
    var list = app.channelList(dvb ? dvb.channels : []);

    if (app.sourceOn('ct') && ct.error && !ct.onAir) this.root.appendChild(el('div', 'group-error', ct.error));
    if (dvbOn && dvb.error) this.root.appendChild(el('div', 'group-error', dvb.error));
    this.group(list, function (item) {
      var ch = item.channel;
      if (item.source === 'dvb') {
        return self.card(item.key, 'dvb', ch.name, dvb.onAir ? dvb.onAir[ch.id] : null, now,
          { kind: 'dvbLive', channelId: ch.id, title: ch.name });
      }
      return self.card(item.key, 'ct', ch.name, ct.onAir ? ct.onAir[ch.id] : null, now,
        { kind: 'live', channelId: ch.id, title: ch.name });
    });

    // While the Anténa channels are still loading the list may be empty without anything being hidden.
    var dvbLoading = dvbOn && !dvb.channels.length && !dvb.error;
    if (!this.cards.length && !dvbLoading) {
      this.root.appendChild(el('div', 'group-error', 'Všechny kanály jsou skryté. Zobrazíte je v Nastavení → Kanály.'));
    }

    var restored = this.cards.findIndex(function (c) { return c.key === focusedKey; });
    this.focus = restored >= 0 ? restored : Math.max(0, Math.min(this.focus, this.cards.length - 1));
    this.applyFocus();
  };

  LiveScreen.prototype.group = function (channels, makeCard) {
    var grid = el('div', 'cards');
    var start = this.cards.length;
    channels.forEach(function (ch) { grid.appendChild(makeCard(ch)); });
    if (this.cards.length > start) this.groups.push({ start: start, end: this.cards.length });
    this.root.appendChild(grid);
  };

  LiveScreen.prototype.card = function (key, source, name, info, now, request) {
    var card = el('div', 'card');
    var channel = el('div', 'card-channel');
    channel.appendChild(CT.sourceIcon(source));
    channel.appendChild(el('span', 'card-channel-name', name));
    card.appendChild(channel);
    var current = info ? info.current : null;
    var next = info ? info.next : null;
    if (current) {
      card.appendChild(el('div', 'card-title', current.title));
      card.appendChild(el('div', 'card-time', CT.Days.timeRange(current.start, current.end)));
      var bar = el('div', 'progress');
      var fill = el('div', 'progress-fill');
      fill.style.width = Math.round(CT.Entries.progress(current, now) * 100) + '%';
      bar.appendChild(fill);
      card.appendChild(bar);
    } else {
      card.appendChild(el('div', 'card-title muted', info ? 'Mimo vysílání' : ''));
    }
    if (next) card.appendChild(el('div', 'card-next', 'Potom ' + CT.Days.time(next.start) + ' ' + next.title));
    this.cards.push({ key: key, el: card, request: request });
    return card;
  };

  LiveScreen.prototype.applyFocus = function () {
    var self = this;
    this.cards.forEach(function (c, i) {
      c.el.classList.toggle('focused', self.app.inContent() && i === self.focus);
    });
    var focused = this.cards[this.focus];
    if (focused && this.app.inContent()) CT.scrollIntoView(focused.el);
  };

  /** Returns false when the key leaves the screen upwards (to the tabs). */
  LiveScreen.prototype.onKey = function (key) {
    if (!this.cards.length) return key !== 'up';
    var i = this.focus;
    // Rows of PER_ROW cards; there is one group (the mixed list), the ranges would handle more.
    var g = 0;
    while (g < this.groups.length - 1 && i >= this.groups[g].end) g++;
    var base = this.groups[g].start;
    var groupEnd = this.groups[g].end;
    var col = (i - base) % PER_ROW;
    switch (key) {
      case 'left':
        if (i > 0) this.focus = i - 1;
        break;
      case 'right':
        if (i < this.cards.length - 1) this.focus = i + 1;
        break;
      case 'up':
        if (i - PER_ROW >= base) {
          this.focus = i - PER_ROW;
        } else if (g > 0) {
          // last row of the group above, same column if it has one
          var prev = this.groups[g - 1];
          var lastRow = prev.start + Math.floor((prev.end - 1 - prev.start) / PER_ROW) * PER_ROW;
          this.focus = Math.min(prev.end - 1, lastRow + col);
        } else {
          return false;
        }
        break;
      case 'down':
        if (base + (Math.floor((i - base) / PER_ROW) + 1) * PER_ROW < groupEnd) this.focus = Math.min(groupEnd - 1, i + PER_ROW);
        else if (g < this.groups.length - 1) this.focus = Math.min(this.groups[g + 1].end - 1, this.groups[g + 1].start + col);
        break;
      case 'enter':
        this.app.play(this.cards[i].request);
        break;
      default:
        break;
    }
    this.applyFocus();
    return true;
  };

  CT.LiveScreen = LiveScreen;
})(window);
