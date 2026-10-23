/*
 * "TV průvodce": EPG grid with channels as rows and time on the horizontal axis (~2 h visible), a "now"
 * line, a day picker (7 days back, 2 ahead) and the focused programme's detail. The remote moves cell to
 * cell; up/down keep the focused moment, so focus walks straight down a column of time. Only the cells near
 * the screen have DOM (syncWindow), so a big Anténa line-up stays responsive on a TV.
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
    this.rows = []; // [{name, slots: [{start, end, item, el | null}], line | null, range | null}]
    this.axisStart = 0;
    this.axisEnd = 0;
    this.focusRow = 0;
    this.focusSlot = 0;
    this.anchor = 0; // the focused moment, kept when moving up/down
    this.liveMode = false; // today, before any left/right: up/down lands on what airs now
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
    else this.liveMode = this.isToday();
  };

  GuideScreen.prototype.isToday = function () {
    var now = this.app.now;
    return this.day === CT.Days.today(now) && now >= this.axisStart && now <= this.axisEnd;
  };

  /** Slot to focus in `rowIndex` after up/down: the airing programme in live mode, else under the anchor. */
  GuideScreen.prototype.verticalSlot = function (rowIndex) {
    if (this.liveMode) {
      var live = CT.GuideLayout.liveSlot(this.rows[rowIndex].slots, this.app.now);
      if (live >= 0) {
        this.anchor = this.app.now;
        return live;
      }
    }
    return this.slotAt(rowIndex, this.anchor);
  };

  /** `edge` ('start' | 'end'): "Across days", focus that row's first or last cell once the day is built. */
  GuideScreen.prototype.selectDay = function (day, edge) {
    var self = this;
    this.day = day;
    this.pendingEdge = edge || null;
    var today = CT.Days.today(this.app.now);
    this.days = CT.Days.guideDays(today);
    this.renderDays();
    $('grid-message').textContent = 'Načítání…';
    $('grid-rows').innerHTML = '';
    $('grid-ticks').innerHTML = '';
    // The old rows' DOM is gone: nothing may point at it while the new day loads.
    this.rows = [];
    this.win = null;
    this.focusEl = null;
    this.nowLine = null;
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

  /**
   * One row per channel, CT internet and "Anténa" mixed in this TV's order without hidden channels or
   * switched-off sources ("Kanály" in Nastavení); empty rows show as "Mimo vysílání".
   */
  GuideScreen.prototype.build = function (ct, dvb) {
    var rows = this.app.channelList(dvb ? dvb.channels : []).map(function (item) {
      var ch = item.channel;
      if (item.source === 'dvb') {
        var events = dvb.plan[ch.id] || [];
        return { name: ch.name, source: 'dvb', entries: events.map(function (e) { return CT.Entries.dvb(ch, e); }) };
      }
      var list = ct.plan ? ct.plan[ch.id] || [] : [];
      return { name: ch.name, source: 'ct', entries: list.map(function (p) { return CT.Entries.ct(ch, p); }) };
    });
    var all = [];
    rows.forEach(function (r) { all = all.concat(r.entries); });
    if (!rows.length) {
      $('grid-message').textContent = 'Všechny kanály jsou skryté. Zobrazíte je v Nastavení → Kanály.';
      this.rows = [];
      this.nowLine = null;
      this.win = null;
      this.focusEl = null;
      $('grid-rows').innerHTML = '';
      $('grid-ticks').innerHTML = '';
      this.showDetail(null);
      return;
    }
    var ctError = this.app.sourceOn('ct') ? ct.error : null;
    $('grid-message').textContent = ctError || (all.length ? '' : 'Pro tento den není program');

    // Exactly the broadcast day 05:00–05:00 Prague; programmes crossing 05:00 are clipped.
    var bounds = CT.Days.bounds(this.day);
    var axisStart = bounds[0];
    var axisEnd = bounds[1];
    this.axisStart = axisStart;
    this.axisEnd = axisEnd;

    var ticks = $('grid-ticks');
    ticks.innerHTML = '';
    ticks.style.width = px(axisEnd - axisStart) + 'px';
    var tickBox = document.createDocumentFragment();
    CT.GuideLayout.ticks(axisStart, axisEnd).forEach(function (t) {
      var tick = el('div', 'tick', CT.Days.time(t));
      tick.style.left = px(t - axisStart) + 'px';
      tickBox.appendChild(tick);
    });
    ticks.appendChild(tickBox);

    // Only data here: cells get DOM when they come near the screen (see syncWindow).
    this.rows = rows.map(function (row) {
      return { name: row.name, source: row.source, slots: CT.GuideLayout.row(row.entries, axisStart, axisEnd),
        line: null, nameEl: null, nameX: null, range: null };
    });
    var container = $('grid-rows');
    container.innerHTML = '';
    container.style.height = this.rows.length * ROW_HEIGHT + 'px';
    this.nowLine = el('div', 'now-line');
    container.appendChild(this.nowLine);
    this.win = null;
    this.focusEl = null;
    this.viewRows = this.visibleRows();
    this.viewPx = this.viewWidth();
    this.refreshLooks();

    // Open at "now" (or the same time of day on other days), half an hour of past on the left.
    var now = this.app.now;
    var anchor = now >= axisStart && now <= axisEnd ? now : CT.Days.atMinute(this.day, CT.Days.minuteOfDay(now));
    if (anchor < axisStart || anchor > axisEnd) anchor = axisStart;
    this.anchor = anchor;
    this.scrollX = Math.max(0, px(anchor - axisStart - 30 * MINUTE));
    this.focusRow = Math.min(this.focusRow, this.rows.length - 1);
    if (this.scrollRow > this.focusRow) this.scrollRow = this.focusRow;
    this.liveMode = this.isToday();
    this.focusSlot = this.liveMode ? this.verticalSlot(this.focusRow) : this.slotAt(this.focusRow, anchor);
    if (this.pendingEdge) {
      // Scrolled past the previous day's edge: continue at this day's start (or end), in the same row.
      var slots = this.rows[this.focusRow].slots;
      this.liveMode = false;
      this.focusSlot = this.pendingEdge === 'start' ? 0 : slots.length - 1;
      var edgeSlot = slots[this.focusSlot];
      this.anchor = this.pendingEdge === 'start' ? edgeSlot.start + MINUTE : edgeSlot.end - MINUTE;
      this.scrollX = this.pendingEdge === 'start' ? 0 : Math.max(0, px(axisEnd - axisStart) - this.viewPx + 20);
      this.pendingEdge = null;
    }
    this.zone = this.zone === 'days' && this.app.inContent() ? 'days' : 'grid';
    this.applyFocus();
  };

  /**
   * Gives DOM only to the cells in the window around the viewport (spec/apps.md, performance): a day of
   * dozens of Anténa channels is thousands of cells, far too many for a TV to lay out and restyle.
   */
  GuideScreen.prototype.syncWindow = function () {
    var L = CT.GuideLayout;
    var view = {
      row: this.scrollRow,
      rows: this.viewRows,
      start: this.axisStart + this.scrollX / PX_PER_MIN * MINUTE,
      end: this.axisStart + (this.scrollX + this.viewPx) / PX_PER_MIN * MINUTE
    };
    if (L.covers(this.win, view, this.rows.length, 1, 30 * MINUTE)) return;
    var old = this.win;
    var win = L.window(view, this.rows.length);
    var container = $('grid-rows');
    var r;
    if (old) {
      for (r = old.r0; r < old.r1; r++) if (r < win.r0 || r >= win.r1) this.dropRow(this.rows[r]);
    }
    for (r = win.r0; r < win.r1; r++) this.fillRow(this.rows[r], r, L.slotRange(this.rows[r].slots, win.t0, win.t1), container);
    this.win = win;
  };

  GuideScreen.prototype.dropRow = function (row) {
    if (!row || !row.line) return;
    if (row.line.parentNode) row.line.parentNode.removeChild(row.line);
    if (row.range) for (var i = row.range[0]; i < row.range[1]; i++) row.slots[i].el = row.slots[i].label = null;
    row.line = row.nameEl = row.nameX = row.range = null;
  };

  /** Creates the row's line once, then adds/removes cells so exactly `range` [from, to) has DOM. */
  GuideScreen.prototype.fillRow = function (row, index, range, container) {
    var i;
    if (!row.line) {
      row.line = el('div', 'row');
      row.line.style.top = index * ROW_HEIGHT + 'px';
      row.nameEl = el('div', 'row-name');
      row.nameEl.appendChild(CT.sourceIcon(row.source));
      row.nameEl.appendChild(el('span', 'row-name-text', row.name));
      row.line.appendChild(row.nameEl);
      row.track = el('div', 'row-track');
      row.track.style.width = px(this.axisEnd - this.axisStart) + 'px';
      row.line.appendChild(row.track);
      container.insertBefore(row.line, this.nowLine);
      row.range = [0, 0];
    }
    var had = row.range;
    for (i = had[0]; i < had[1]; i++) {
      if (i < range[0] || i >= range[1]) {
        row.track.removeChild(row.slots[i].el);
        row.slots[i].el = row.slots[i].label = null;
      }
    }
    var added = document.createDocumentFragment();
    for (i = range[0]; i < range[1]; i++) if (!row.slots[i].el) added.appendChild(this.cell(row.slots[i]));
    row.track.appendChild(added);
    row.range = range;
  };

  GuideScreen.prototype.cell = function (slot) {
    var cell = el('div', 'cell');
    cell.style.left = px(slot.start - this.axisStart) + 'px';
    cell.style.width = Math.max(1, px(slot.end - slot.start) - 4) + 'px';
    var label = el('div', 'cell-label');
    if (slot.item) {
      slot.look = CT.Entries.look(slot.item, this.app.now);
      cell.className = 'cell look-' + slot.look;
      label.appendChild(el('span', 'cell-time', CT.Days.time(slot.item.start)));
      label.appendChild(el('span', 'cell-title', slot.item.title));
    } else {
      cell.classList.add('filler');
      label.appendChild(el('span', 'cell-title', 'Mimo vysílání'));
    }
    cell.appendChild(label);
    slot.el = cell;
    slot.label = label;
    slot.shift = 0;
    return cell;
  };

  /** Re-colours the cells that have DOM (others get their look when created) and moves the now line; every 30 s. */
  GuideScreen.prototype.refreshLooks = function () {
    var now = this.app.now;
    this.rows.forEach(function (row) {
      if (!row.range) return;
      for (var i = row.range[0]; i < row.range[1]; i++) {
        var slot = row.slots[i];
        if (!slot.item) continue;
        var look = CT.Entries.look(slot.item, now);
        if (look === slot.look) continue;
        slot.look = look;
        slot.el.className = 'cell look-' + look + (slot.el.classList.contains('focused') ? ' focused' : '');
      }
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
    var slot = this.focused();
    if (slot) this.scrollTo(slot, this.verticalMove); // also gives the focused cell its DOM
    this.verticalMove = false;
    // Only the previous and the new focused cell change, not every cell of the grid.
    var target = active && this.zone === 'grid' && slot ? slot.el : null;
    if (this.focusEl && this.focusEl !== target) this.focusEl.classList.remove('focused');
    if (target) target.classList.add('focused');
    this.focusEl = target;
    this.showDetail(slot ? slot.item : null);
  };

  /**
   * Keeps the focused cell on screen; a cell wider than the view only scrolls when it is out of sight.
   * After up/down (`keepTime`) a cell that is partly on screen does not scroll the time axis: the rows'
   * cells start at different times, and scrolling for each would move (and rebuild) the window per press.
   */
  GuideScreen.prototype.scrollTo = function (slot, keepTime) {
    var view = this.viewPx;
    var left = px(slot.start - this.axisStart);
    var right = px(slot.end - this.axisStart);
    var total = px(this.axisEnd - this.axisStart);
    var x = this.scrollX;
    if (keepTime && right > x + 60 && left < x + view - 60) {
      // stays: the cell is visible where the viewer looks
    } else if (right - left > view - 120) {
      if (right < x + 120 || left > x + view - 120) x = left - 60;
    } else if (left < x) {
      x = left - 60;
    } else if (right > x + view) {
      x = right - view + 60;
    }
    this.scrollX = Math.max(0, Math.min(x, Math.max(0, total - view + 20)));

    var visible = this.viewRows;
    if (this.focusRow < this.scrollRow) this.scrollRow = this.focusRow;
    if (this.focusRow >= this.scrollRow + visible) this.scrollRow = this.focusRow - visible + 1;
    this.syncWindow();

    var tx = 'translate(' + (-this.scrollX) + 'px, ' + (-this.scrollRow * ROW_HEIGHT) + 'px)';
    $('grid-rows').style.transform = tx;
    $('grid-ticks').style.transform = 'translateX(' + (-this.scrollX) + 'px)';
    // Channel names stay put horizontally; titles of cells that began left of the view stay readable.
    // Only cells with DOM, and only when the value changes.
    var viewStart = this.scrollX;
    this.rows.forEach(function (row) {
      if (!row.range) return;
      if (row.nameX !== viewStart) {
        row.nameEl.style.transform = 'translateX(' + viewStart + 'px)';
        row.nameX = viewStart;
      }
      for (var i = row.range[0]; i < row.range[1]; i++) {
        var s = row.slots[i];
        var l = px(s.start - this.axisStart);
        var w = px(s.end - s.start);
        var shift = l < viewStart ? Math.min(viewStart - l, Math.max(0, w - 160)) : 0;
        if (shift !== s.shift) {
          s.label.style.transform = shift ? 'translateX(' + shift + 'px)' : '';
          s.shift = shift;
        }
      }
    }, this);
  };

  /** Shows the image of the detail panel: a URL, the antenna placeholder, or nothing. */
  function setDetailImage(url, placeholder) {
    var image = $('detail-image');
    image.style.backgroundImage = url ? 'url("' + url.replace(/"/g, '%22') + '")' : '';
    image.classList.toggle('placeholder', !url && !!placeholder);
    image.innerHTML = '';
    if (!url && placeholder) image.appendChild(CT.sourceIcon('dvb'));
    image.style.visibility = url || placeholder ? 'visible' : 'hidden';
  }

  /** Again for the same entry (e.g. the gateway's thumbnail switch changed). */
  GuideScreen.prototype.refreshDetail = function () {
    if (this.detailEntry !== undefined) this.showDetail(this.detailEntry);
  };

  GuideScreen.prototype.showDetail = function (entry) {
    var self = this;
    var image = $('detail-image');
    this.detailEntry = entry || null;
    clearTimeout(this.thumbTimer);
    if (!entry) {
      $('detail-title').textContent = '';
      $('detail-meta').textContent = '';
      $('detail-description').textContent = '';
      setDetailImage('', false);
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
    if (entry.source === 'dvb') {
      // Anténa: placeholder, then the gateway thumbnail once focus rests (spec/apps.md "Anténa thumbnails").
      setDetailImage('', true);
      var recordingId = CT.Thumbs.wanted(this.app.dvbThumbs, CT.ThumbsSwitch.on(), entry);
      if (!recordingId) return;
      var final = entry.item.recording.state === 'completed';
      this.thumbTimer = setTimeout(function () {
        self.app.loadThumb(recordingId, final).then(function (url) {
          if (url && self.detailEntry === entry) setDetailImage(url, true);
        });
      }, CT.Thumbs.DELAY_MS);
      return;
    }
    // Only https images from the API; nothing is cached by the app.
    var url = /^https:\/\//.test(entry.imageUrl) ? entry.imageUrl : '';
    setDetailImage(url, false);
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
        if (next < 0 || next >= slots.length) {
          // "Across days": past the first/last cell continues in the previous/next day (none at the range's ends).
          var other = CT.GuideLayout.neighbourDay(this.days, this.day, key === 'left' ? -1 : 1);
          if (other) {
            this.liveMode = false;
            this.selectDay(other, key === 'left' ? 'end' : 'start');
            return true;
          }
          break;
        }
        this.liveMode = false; // the viewer browses the timeline now
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
        this.focusSlot = this.verticalSlot(this.focusRow);
        this.verticalMove = true;
        break;
      case 'down':
        if (this.focusRow < this.rows.length - 1) {
          this.focusRow++;
          this.focusSlot = this.verticalSlot(this.focusRow);
          this.verticalMove = true;
        }
        break;
      case 'chup':
      case 'chdown': {
        var page = this.visibleRows() * (key === 'chup' ? -1 : 1);
        this.focusRow = Math.max(0, Math.min(this.rows.length - 1, this.focusRow + page));
        this.focusSlot = this.verticalSlot(this.focusRow);
        this.verticalMove = true;
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
