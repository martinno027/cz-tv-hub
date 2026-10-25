/*
 * Archiv cards and lazy lists (spec/apps.md "Archiv"): a card = landscape image, title, "2012 · film", "★ 7,4" and
 * the source icons. The grid and the episode lists build only the lines around the focus (absolutely placed by
 * index, so the space of the others stays exact); images are asked for only for built cards, through the app's
 * ImageLoader into blob URLs (memory only).
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var Cards = {};

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  Cards.el = el;

  /** Card model of a gateway card. */
  Cards.fromCard = function (card) {
    var lines = CT.Archive.cardLines(card);
    return { title: lines.title, meta: lines.meta, rating: lines.rating, sources: lines.sources, image: card.image, progress: null };
  };

  /** Card model of a "Pokračovat ve sledování" entry: the show under the episode, how far it got. */
  Cards.fromResume = function (item) {
    return {
      title: item.title, meta: item.showTitle || ('Pokračovat od ' + item.label), rating: null,
      sources: [item.source], image: item.image, progress: item.length > 0 ? item.progress / item.length : null
    };
  };

  /**
   * Puts `image` (a card's image value) into `box` as a background once loaded; the source icon stays as the
   * placeholder until then (and when there is no image, or thumbnails are off on the gateway or this TV).
   */
  Cards.loadImage = function (app, box, image) {
    var request = CT.Archive.image(image);
    if (!request || (request.thumb && !app.thumbsOn())) return;
    request.keep = true; // not dropped when the guide queues its own window
    var show = function (url) {
      box.style.backgroundImage = 'url("' + url.replace(/"/g, '%22') + '")';
      box.classList.add('loaded');
    };
    var cached = app.images.get(request.key);
    if (cached) return show(cached);
    app.images.load(request, function (url, status) {
      if (!box.parentNode) return; // the card was dropped meanwhile
      if (url) show(url);
      // CT's CDN refused the blob request: let the element load it, as in the guide detail
      else if (request.kind === 'ct' && status === 0) show(request.url);
    });
  };

  /** The card element of a model {title, meta, rating, sources, image, progress}. */
  Cards.element = function (app, model) {
    var card = el('div', 'ar-card');
    var image = el('div', 'ar-image');
    var icons = el('div', 'ar-placeholder');
    model.sources.forEach(function (s) { icons.appendChild(CT.sourceIcon(s)); });
    image.appendChild(icons);
    if (model.progress !== null) {
      var bar = el('div', 'progress ar-progress');
      var fill = el('div', 'progress-fill');
      fill.style.width = Math.round(Math.min(1, Math.max(0, model.progress)) * 100) + '%';
      bar.appendChild(fill);
      image.appendChild(bar);
    }
    card.appendChild(image);
    card.appendChild(el('div', 'ar-title', model.title));
    var meta = el('div', 'ar-meta');
    model.sources.forEach(function (s) { meta.appendChild(CT.sourceIcon(s)); });
    meta.appendChild(el('span', 'ar-meta-text', model.meta));
    if (model.rating) meta.appendChild(el('span', 'ar-rating', model.rating));
    card.appendChild(meta);
    Cards.loadImage(app, image, model.image);
    return card;
  };

  /**
   * A vertical list of lines built lazily (grid rows of cards, episode lines): `rows` lines of `step` px, `visible`
   * of them on screen; `make(r)` → the line's element. `sync(row, focusFn)` keeps `row` on screen.
   */
  function Lines(track, rows, step, visible, make) {
    this.track = track;
    this.rows = rows;
    this.step = step;
    this.visible = visible;
    this.make = make;
    this.top = 0;
    this.built = {};
  }

  Lines.prototype.setRows = function (rows) {
    this.rows = rows;
  };

  Lines.prototype.sync = function (row) {
    if (row !== null) {
      if (row < this.top) this.top = row;
      if (row > this.top + this.visible - 1) this.top = row - this.visible + 1;
    }
    this.top = Math.max(0, Math.min(this.top, Math.max(0, this.rows - this.visible)));
    var from = Math.max(0, this.top - 2);
    var to = Math.min(this.rows - 1, this.top + this.visible + 2);
    var self = this;
    Object.keys(this.built).forEach(function (k) {
      var r = Number(k);
      if (r < from || r > to) {
        self.track.removeChild(self.built[k]);
        delete self.built[k];
      }
    });
    for (var r = from; r <= to; r++) {
      if (this.built[r]) continue;
      var e = this.make(r);
      e.style.top = r * this.step + 'px';
      this.built[r] = e;
      this.track.appendChild(e);
    }
    this.track.style.transform = 'translateY(' + -this.top * this.step + 'px)';
  };

  /** Drops every built line (the data changed). */
  Lines.prototype.clear = function () {
    this.track.innerHTML = '';
    this.built = {};
  };

  var LONG_OK_MS = 800;
  var RELEASE_FALLBACK_MS = 800; // no keyup came: the key counts as released this long after its last keydown

  /**
   * OK that can be held: `down()` on every keydown of OK (repeats included), `up()` on its keyup. A short press
   * calls `short()` on release, holding 0.8 s calls `long()` at once (its release does nothing).
   */
  function LongPress(short, long) {
    this.short = short;
    this.long = long;
    this.ok = null; // {at, long, timer}
  }

  LongPress.prototype.active = function () {
    return !!this.ok;
  };

  LongPress.prototype.down = function () {
    var self = this;
    var now = Date.now();
    if (!this.ok) this.ok = { at: now, long: false, timer: null };
    var ok = this.ok;
    if (!ok.long && now - ok.at >= LONG_OK_MS) {
      ok.long = true;
      this.long();
    }
    clearTimeout(ok.timer);
    ok.timer = setTimeout(function () { if (self.ok === ok) self.up(); }, RELEASE_FALLBACK_MS);
  };

  LongPress.prototype.up = function () {
    var ok = this.ok;
    if (!ok) return;
    clearTimeout(ok.timer);
    this.ok = null;
    if (!ok.long) this.short();
  };

  /**
   * The small menu over the Archiv (long OK): a title and buttons [{label, action}], ←/→ and OK; Return closes it.
   * One element (#ar-menu) above the tab and the show page.
   */
  var Menu = {
    items: null,
    focus: 0,

    isOpen: function () {
      return !!Menu.items;
    },

    open: function (title, items) {
      Menu.items = items;
      Menu.focus = 0;
      document.getElementById('ar-menu-title').textContent = title;
      var box = document.getElementById('ar-menu-buttons');
      box.innerHTML = '';
      items.forEach(function (item) { box.appendChild(el('div', 'button', item.label)); });
      document.getElementById('ar-menu').classList.remove('hidden');
      Menu.render();
    },

    close: function () {
      Menu.items = null;
      document.getElementById('ar-menu').classList.add('hidden');
    },

    render: function () {
      Array.prototype.forEach.call(document.getElementById('ar-menu-buttons').childNodes, function (b, i) {
        b.classList.toggle('focused', i === Menu.focus);
      });
    },

    /** Keys while open; OK runs the focused item's action after closing. */
    onKey: function (key) {
      if (key === 'left') Menu.focus = Math.max(0, Menu.focus - 1);
      else if (key === 'right') Menu.focus = Math.min(Menu.items.length - 1, Menu.focus + 1);
      else if (key === 'back') Menu.close();
      else if (key === 'enter') {
        var action = Menu.items[Menu.focus].action;
        Menu.close();
        if (action) action();
        return;
      }
      if (Menu.isOpen()) Menu.render();
    }
  };

  Cards.LongPress = LongPress;
  Cards.Menu = Menu;
  Cards.Lines = Lines;
  CT.ArchiveCards = Cards;
})(window);
