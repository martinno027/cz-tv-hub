/*
 * "TV průvodce": EPG grid with channels as rows and time on the horizontal axis (~2 h visible), a "now"
 * line, a day picker (7 days back, 2 ahead) and the focused programme's detail. The remote moves cell to
 * cell; up/down keep the focused moment, so focus walks straight down a column of time.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var PX_PER_MIN = 12; // 30 min = 360 px, about two hours on screen
  var ROW_HEIGHT = 76;
  var MINUTE = 60 * 1000;

  function $(id) {
    return document.getElementById(id);
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function px(ms) {
    return ms / MINUTE * PX_PER_MIN;
  }

  function GuideScreen(app) {
    this.app = app;
    this.day = null;
    this.days = [];
    this.dayFocus = 0;
    this.zone = 'grid'; // 'days' | 'grid'
    this.rows = []; // [{name, slots: [{start, end, item, el}]}]
    this.axisStart = 0;
    this.axisEnd = 0;
    this.focusRow = 0;
    this.focusSlot = 0;
    this.anchor = 0; // the focused moment, kept when moving up/down
    this.scrollX = 0;
    this.scrollRow = 0;
    this.plans = {}; // day -> {ct: plan | null, ctError, dvb: plan | null}
    this.pickedInitialDay = false;
  }

  GuideScreen.prototype.visibleRows = function () {
    return Math.max(1, Math.floor($('grid-body').clientHeight / ROW_HEIGHT));
  };

  GuideScreen.prototype.viewWidth = function () {
    return $('grid-body').clientWidth - 220;
  };

  /** Opens on today (or yesterday's broadcast day after midnight) the first time it is shown. */
  GuideScreen.prototype.show = function () {
    if (!this.day) this.selectDay(CT.Days.today(this.app.now));
  };

  GuideScreen.prototype.selectDay = function (day) {
    var self = this;
    this.day = day;
    var today = CT.Days.today(this.app.now);
    this.days = CT.Days.guideDays(today);
    this.renderDays();
    $('grid-message').textContent = 'Načítání…';
    $('grid-rows').innerHTML = '';
    $('grid-ticks').innerHTML = '';
    var app = this.app;
    var ct = app.schedules.day(day).then(function (plan) {
      return { plan: plan };
    }, function (e) {
      return { error: e.userMessage || CT.Messages.SCHEDULE_FAILED };
    });
    // Days outside the gateway's range answer 400; the CT rows still show.
    var repo = app.dvbRepo;
    var dvb = repo ? Promise.all([repo.channels(), repo.day(day)]).then(function (r) {
      return { channels: r[0], plan: r[1] };
    }, function () {
      return { channels: app.dvbChannels || [], plan: {} };
    }) : Promise.resolve(null);
    Promise.all([ct, dvb]).then(function (r) {
      if (self.day !== day) return;
      if (self.maybeStartOnYesterday(day, r[0])) return;
      self.build(r[0], r[1]);
    });
  };

  /** After midnight the programme on air still belongs to yesterday's broadcast day. */
  GuideScreen.prototype.maybeStartOnYesterday = function (day, ct) {
    if (this.pickedInitialDay) return false;
    this.pickedInitialDay = true;
    if (!ct.plan || day !== CT.Days.today(this.app.now)) return false;
    var starts = [];
    Object.keys(ct.plan).forEach(function (k) { ct.plan[k].forEach(function (p) { starts.push(p.start); }); });
    if (!starts.length || this.app.now >= Math.min.apply(null, starts)) return false;
    this.selectDay(CT.Days.addDays(day, -1));
    return true;
  };

  GuideScreen.prototype.renderDays = function () {
    var self = this;
    var today = CT.Days.today(this.app.now);
    var box = $('days');
    box.innerHTML = '';
    this.dayFocus = Math.max(0, this.days.indexOf(this.day));
    this.days.forEach(function (d, i) {
      var chip = el('div', 'day', CT.Days.label(d, today));
      if (d === self.day) chip.classList.add('selected');
      chip.dataset.index = String(i);
      box.appendChild(chip);
    });
    this.applyFocus();
  };

  /** CT internet channels first, then the "Anténa" channels (empty rows show as "Mimo vysílání"). */
  GuideScreen.prototype.build = function (ct, dvb) {
    var self = this;
    var rows = CT.Channels.all.map(function (ch) {
      var list = ct.plan ? ct.plan[ch.id] || [] : [];
      return { name: ch.name, entries: list.map(function (p) { return CT.Entries.ct(ch, p); }) };
    });
    if (dvb) {
      dvb.channels.forEach(function (ch) {
        var list = dvb.plan[ch.id] || [];
        rows.push({ name: ch.name, entries: list.map(function (e) { return CT.Entries.dvb(ch, e); }) });
      });
    }
    var all = [];
    rows.forEach(function (r) { all = all.concat(r.entries); });
    $('grid-message').textContent = ct.error ? ct.error : (all.length ? '' : 'Pro tento den není program');
    if (!all.length && !rows.length) return;

    var axisStart = CT.GuideLayout.axisStart(all);
    if (axisStart === null) axisStart = CT.Days.atMinute(this.day, 5 * 60);
    var axisEnd = all.length ? Math.max.apply(null, all.map(function (e) { return e.end; })) : axisStart + 24 * 60 * MINUTE;
    this.axisStart = axisStart;
    this.axisEnd = axisEnd;

    var ticks = $('grid-ticks');
    ticks.innerHTML = '';
    ticks.style.width = px(axisEnd - axisStart) + 'px';
    CT.GuideLayout.ticks(axisStart, axisEnd).forEach(function (t) {
      var tick = el('div', 'tick', CT.Days.time(t));
      tick.style.left = px(t - axisStart) + 'px';
      ticks.appendChild(tick);
    });

    var container = $('grid-rows');
    container.innerHTML = '';
    this.rows = rows.map(function (row) {
      var line = el('div', 'row');
      line.appendChild(el('div', 'row-name', row.name));
      var track = el('div', 'row-track');
      track.style.width = px(axisEnd - axisStart) + 'px';
      var slots = CT.GuideLayout.row(row.entries, axisStart, axisEnd);
      slots.forEach(function (slot) {
        var cell = el('div', 'cell');
        cell.style.left = px(slot.start - axisStart) + 'px';
        cell.style.width = Math.max(1, px(slot.end - slot.start) - 4) + 'px';
        var label = el('div', 'cell-label');
        if (slot.item) {
          label.appendChild(el('span', 'cell-time', CT.Days.time(slot.item.start)));
          label.appendChild(el('span', 'cell-title', slot.item.title));
        } else {
          cell.classList.add('filler');
          label.appendChild(el('span', 'cell-title', 'Mimo vysílání'));
        }
        cell.appendChild(label);
        track.appendChild(cell);
        slot.el = cell;
        slot.label = label;
      });
      line.appendChild(track);
      container.appendChild(line);
      return { name: row.name, slots: slots, track: track };
    });
    this.nowLine = el('div', 'now-line');
    container.appendChild(this.nowLine);
    this.refreshLooks();

    // Open at "now" (or the same time of day on other days), half an hour of past on the left.
    var now = this.app.now;
    var anchor = now >= axisStart && now <= axisEnd ? now : CT.Days.atMinute(this.day, CT.Days.minuteOfDay(now));
    if (anchor < axisStart || anchor > axisEnd) anchor = axisStart;
    this.anchor = anchor;
    this.scrollX = Math.max(0, px(anchor - axisStart - 30 * MINUTE));
    this.focusRow = Math.min(this.focusRow, this.rows.length - 1);
    this.focusSlot = this.slotAt(this.focusRow, anchor);
    this.zone = this.zone === 'days' && this.app.inContent() ? 'days' : 'grid';
    this.applyFocus();
  };

  /** Re-colours cells and moves the now line; called every 30 s. */
  GuideScreen.prototype.refreshLooks = function () {
    var now = this.app.now;
    this.rows.forEach(function (row) {
      row.slots.forEach(function (slot) {
        if (!slot.item) return;
        var look = CT.Entries.look(slot.item, now);
        slot.el.className = 'cell look-' + look + (slot.el.classList.contains('focused') ? ' focused' : '');
      });
    });
    if (this.nowLine) {
      var inRange = now >= this.axisStart && now <= this.axisEnd;
      this.nowLine.style.display = inRange ? 'block' : 'none';
      this.nowLine.style.left = 220 + px(now - this.axisStart) + 'px';
    }
  };

  GuideScreen.prototype.slotAt = function (rowIndex, moment) {
    var slots = this.rows[rowIndex] ? this.rows[rowIndex].slots : [];
    for (var i = 0; i < slots.length; i++) if (slots[i].start <= moment && moment < slots[i].end) return i;
    return Math.max(0, slots.length - 1);
  };

  GuideScreen.prototype.focused = function () {
    var row = this.rows[this.focusRow];
    return row ? row.slots[this.focusSlot] || null : null;
  };

  GuideScreen.prototype.applyFocus = function () {
    var self = this;
    var active = this.app.inContent();
    Array.prototype.forEach.call($('days').children, function (chip, i) {
      chip.classList.toggle('focused', active && self.zone === 'days' && i === self.dayFocus);
    });
    this.rows.forEach(function (row, r) {
      row.slots.forEach(function (slot, s) {
        slot.el.classList.toggle('focused', active && self.zone === 'grid' && r === self.focusRow && s === self.focusSlot);
      });
    });
    var slot = this.focused();
    if (slot) this.scrollTo(slot);
    this.showDetail(slot ? slot.item : null);
  };

  /** Keeps the focused cell on screen; a cell wider than the view only scrolls when it is out of sight. */
  GuideScreen.prototype.scrollTo = function (slot) {
    var view = this.viewWidth();
    var left = px(slot.start - this.axisStart);
    var right = px(slot.end - this.axisStart);
    var total = px(this.axisEnd - this.axisStart);
    var x = this.scrollX;
    if (right - left > view - 120) {
      if (right < x + 120 || left > x + view - 120) x = left - 60;
    } else if (left < x) {
      x = left - 60;
    } else if (right > x + view) {
      x = right - view + 60;
    }
    this.scrollX = Math.max(0, Math.min(x, Math.max(0, total - view + 20)));

    var visible = this.visibleRows();
    if (this.focusRow < this.scrollRow) this.scrollRow = this.focusRow;
    if (this.focusRow >= this.scrollRow + visible) this.scrollRow = this.focusRow - visible + 1;

    var tx = 'translate(' + (-this.scrollX) + 'px, ' + (-this.scrollRow * ROW_HEIGHT) + 'px)';
    $('grid-rows').style.transform = tx;
    $('grid-ticks').style.transform = 'translateX(' + (-this.scrollX) + 'px)';
    // Channel names stay put horizontally.
    Array.prototype.forEach.call(document.querySelectorAll('#grid-rows .row-name'), function (name) {
      name.style.transform = 'translateX(' + this.scrollX + 'px)';
    }, this);
    // Titles of cells that began left of the view stay readable.
    var viewStart = this.scrollX;
    this.rows.forEach(function (row) {
      row.slots.forEach(function (s) {
        var l = px(s.start - this.axisStart);
        var w = px(s.end - s.start);
        var shift = l < viewStart ? Math.min(viewStart - l, Math.max(0, w - 160)) : 0;
        s.label.style.transform = shift ? 'translateX(' + shift + 'px)' : '';
      }, this);
    }, this);
  };

  GuideScreen.prototype.showDetail = function (entry) {
    var image = $('detail-image');
    if (!entry) {
      $('detail-title').textContent = '';
      $('detail-meta').textContent = '';
      $('detail-description').textContent = '';
      image.style.backgroundImage = '';
      image.style.visibility = 'hidden';
      return;
    }
    $('detail-title').textContent = entry.title + (entry.subtitle ? ' · ' + entry.subtitle : '');
    var meta = [entry.channel.name, CT.Days.label(CT.Days.today(entry.start), CT.Days.today(this.app.now)),
      CT.Days.timeRange(entry.start, entry.end)];
    if (entry.genre) meta.push(entry.genre);
    var look = CT.Entries.look(entry, this.app.now);
    if (look === 'dimmed') meta.push(entry.source === 'dvb' ? CT.DvbMessages.NOT_RECORDED : 'Nedostupné');
    $('detail-meta').textContent = meta.join('  ·  ');
    $('detail-description').textContent = entry.description;
    // Only https images from the API; nothing is cached by the app.
    var url = /^https:\/\//.test(entry.imageUrl) ? entry.imageUrl : '';
    image.style.backgroundImage = url ? 'url("' + url.replace(/"/g, '%22') + '")' : '';
    image.style.visibility = url ? 'visible' : 'hidden';
  };

  /** Returns false when the key leaves the screen upwards (to the tabs). */
  GuideScreen.prototype.onKey = function (key) {
    if (this.zone === 'days') return this.onDaysKey(key);
    var slot = this.focused();
    if (!slot) {
      if (key === 'up') this.zone = 'days';
      this.applyFocus();
      return true;
    }
    var viewStartTime = this.axisStart + this.scrollX / PX_PER_MIN * MINUTE;
    switch (key) {
      case 'left':
      case 'right': {
        var slots = this.rows[this.focusRow].slots;
        var next = this.focusSlot + (key === 'left' ? -1 : 1);
        if (next < 0 || next >= slots.length) break;
        this.focusSlot = next;
        this.anchor = Math.max(slots[next].start, viewStartTime) + MINUTE;
        break;
      }
      case 'up':
        if (this.focusRow === 0) {
          this.zone = 'days';
          break;
        }
        this.focusRow--;
        this.focusSlot = this.slotAt(this.focusRow, this.anchor);
        break;
      case 'down':
        if (this.focusRow < this.rows.length - 1) {
          this.focusRow++;
          this.focusSlot = this.slotAt(this.focusRow, this.anchor);
        }
        break;
      case 'chup':
      case 'chdown': {
        var page = this.visibleRows() * (key === 'chup' ? -1 : 1);
        this.focusRow = Math.max(0, Math.min(this.rows.length - 1, this.focusRow + page));
        this.focusSlot = this.slotAt(this.focusRow, this.anchor);
        break;
      }
      case 'enter':
        if (slot.item) this.app.select(slot.item);
        break;
      default:
        break;
    }
    this.applyFocus();
    return true;
  };

  GuideScreen.prototype.onDaysKey = function (key) {
    switch (key) {
      case 'left':
        this.dayFocus = Math.max(0, this.dayFocus - 1);
        break;
      case 'right':
        this.dayFocus = Math.min(this.days.length - 1, this.dayFocus + 1);
        break;
      case 'enter':
        this.zone = 'grid';
        this.selectDay(this.days[this.dayFocus]);
        return true;
      case 'down':
        this.zone = 'grid';
        break;
      case 'up':
        return false;
      default:
        break;
    }
    this.applyFocus();
    return true;
  };

  /** Called when "Anténa" settings change: rebuild the current day with the new rows. */
  GuideScreen.prototype.reload = function () {
    if (this.day) this.selectDay(this.day);
  };

  CT.GuideScreen = GuideScreen;
})(window);
