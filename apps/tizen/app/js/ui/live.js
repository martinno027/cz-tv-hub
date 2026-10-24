/*
 * "Živě": the channels with the programme on air now, CT internet and "Anténa" in one mixed list, in this TV's
 * order without hidden channels or switched-off sources ("Kanály" in Nastavení); each card shows its source icon.
 * Poslanecká sněmovna cards show the live check: a red "● ŽIVĚ" badge, dimmed "Nevysílá se" or "Zjišťuji…".
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
    this.cards = []; // [{key, el, item}]
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
    this.group(list, function (item, i) {
      var ch = item.channel;
      if (item.source === 'psp') return self.pspCard(item, i + 1);
      var onAir = item.source === 'dvb' ? dvb.onAir : ct.onAir;
      return self.card(item, i + 1, ch.name, onAir ? onAir[ch.id] : null, now);
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
    channels.forEach(function (ch, i) { grid.appendChild(makeCard(ch, i)); });
    if (this.cards.length > start) this.groups.push({ start: start, end: this.cards.length });
    this.root.appendChild(grid);
  };

  /** `number` = the channel's number on this TV (spec/apps.md "Channel numbers and zapping"). */
  LiveScreen.prototype.card = function (item, number, name, info, now) {
    var card = this.cardHead(item, number, '');
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
    return card;
  };

  /** The card with its number, source icon and channel name, registered for focus and OK. */
  LiveScreen.prototype.cardHead = function (item, number, extraClass) {
    var card = el('div', 'card' + extraClass);
    var channel = el('div', 'card-channel');
    channel.appendChild(el('span', 'channel-number', String(number)));
    channel.appendChild(CT.sourceIcon(item.source));
    channel.appendChild(el('span', 'card-channel-name', item.channel.name));
    card.appendChild(channel);
    this.cards.push({ key: item.key, el: card, item: item });
    return card;
  };

  /** Poslanecká sněmovna: on = red "● ŽIVĚ" and "Přímý přenos"; off = dimmed "Nevysílá se"; not checked yet = "Zjišťuji…". */
  LiveScreen.prototype.pspCard = function (item, number) {
    var P = CT.Psp;
    var state = this.app.pspState(item.channel.id);
    var card = this.cardHead(item, number, ' card-psp psp-' + state);
    if (state === 'live') {
      card.appendChild(el('div', 'live-badge', '● ŽIVĚ'));
      card.appendChild(el('div', 'card-title', P.LIVE_TEXT));
    } else {
      card.appendChild(el('div', 'card-title muted', state === 'off' ? P.OFF_TEXT : P.UNKNOWN_TEXT));
    }
    return card;
  };

  /** Focus the card of `key` (the channel that was playing when the player closed), if it is listed. */
  LiveScreen.prototype.focusKey = function (key) {
    var i = this.cards.findIndex(function (c) { return c.key === key; });
    if (i < 0) return;
    this.focus = i;
    this.applyFocus();
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
        this.app.playItem(this.cards[i].item);
        break;
      default:
        break;
    }
    this.applyFocus();
    return true;
  };

  CT.LiveScreen = LiveScreen;
})(window);
