/* "Živě": the six CT channels with the programme on air now, then the "Anténa" group if set up. */
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
    this.focus = 0;
  }

  /** Data: ct = {onAir, error}; dvb = null | {channels, onAir, error}. */
  LiveScreen.prototype.render = function (ct, dvb, now) {
    var self = this;
    var focusedKey = this.cards[this.focus] ? this.cards[this.focus].key : null;
    this.root.innerHTML = '';
    this.cards = [];

    if (dvb) this.root.appendChild(el('h2', 'group', 'Internet ČT'));
    if (ct.error && !ct.onAir) this.root.appendChild(el('div', 'group-error', ct.error));
    var grid = el('div', 'cards');
    CT.Channels.all.forEach(function (ch) {
      var info = ct.onAir ? ct.onAir[ch.id] : null;
      grid.appendChild(self.card('ct:' + ch.id, ch.name, info, now, { kind: 'live', channelId: ch.id, title: ch.name }));
    });
    this.root.appendChild(grid);

    if (dvb) {
      this.root.appendChild(el('h2', 'group', 'Anténa'));
      if (dvb.error) this.root.appendChild(el('div', 'group-error', dvb.error));
      var dgrid = el('div', 'cards');
      dvb.channels.forEach(function (ch) {
        var info = dvb.onAir ? dvb.onAir[ch.id] : null;
        dgrid.appendChild(self.card('dvb:' + ch.id, ch.name, info, now, { kind: 'dvbLive', channelId: ch.id, title: ch.name }));
      });
      this.root.appendChild(dgrid);
    }

    var restored = this.cards.findIndex(function (c) { return c.key === focusedKey; });
    this.focus = restored >= 0 ? restored : Math.min(this.focus, this.cards.length - 1);
    this.applyFocus();
  };

  LiveScreen.prototype.card = function (key, name, info, now, request) {
    var card = el('div', 'card');
    card.appendChild(el('div', 'card-channel', name));
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
    var ctCount = CT.Channels.all.length;
    var i = this.focus;
    // Two groups of rows: CT (indices < ctCount) and Anténa; rows hold PER_ROW cards each.
    var group = i < ctCount ? 0 : 1;
    var base = group === 0 ? 0 : ctCount;
    var groupEnd = group === 0 ? ctCount : this.cards.length;
    var col = (i - base) % PER_ROW;
    switch (key) {
      case 'left':
        if (i > 0) this.focus = i - 1;
        break;
      case 'right':
        if (i < this.cards.length - 1) this.focus = i + 1;
        break;
      case 'up':
        if (i - PER_ROW >= base) this.focus = i - PER_ROW;
        else if (group === 1) this.focus = Math.min(ctCount - 1, col);
        else return false;
        break;
      case 'down':
        if (i + PER_ROW < groupEnd) this.focus = i + PER_ROW;
        else if (group === 0 && this.cards.length > ctCount) this.focus = Math.min(this.cards.length - 1, ctCount + col);
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
