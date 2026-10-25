/*
 * "Archiv" (spec/apps.md "Archiv"): iVysílání and our recordings from the gateway's /dvb/archive. Home = the search
 * field (the TV's keyboard; results 0.5 s after the last change, at least 2 characters) and rows: "Pokračovat ve
 * sledování" (this TV's resume entries), then the gateway's rows; the end of a row opens the whole category
 * (sub-genre chips, sort, a paged grid). A card opens the show page (show.js). Return goes back one step at a time.
 * Without a gateway the tab only says how to set one up.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var A = CT.Archive;
  var Cards = CT.ArchiveCards;
  var el = Cards.el;

  var ROW_STEP = 340; // a home row: its title and a line of cards
  var ROWS_VISIBLE = 2;
  var GRID_STEP = 300;
  var GRID_COLS = 5;
  var GRID_VISIBLE = 2;
  var HOME_FRESH_MS = 10 * 60 * 1000;
  var PAGE_AHEAD_ROWS = 2; // the next page is asked for when the focus is this close to the grid's end
  var Menu = Cards.Menu;

  function $(id) {
    return document.getElementById(id);
  }

  function ArchiveScreen(app) {
    var self = this;
    this.app = app;
    this.page = new CT.ShowPage(app, this);
    // OK on a "Pokračovat" card: short = play from the position, long = "Odebrat z řady"
    this.press = new Cards.LongPress(function () { self.playContinue(); }, function () { self.offerRemove(); });
    this.reset();
    var input = $('ar-search');
    // Leaving the on-screen keyboard (Done / Cancel / Enter) returns to remote navigation.
    input.addEventListener('blur', function () { self.applyFocus(); });
    var changed = function () { self.typed(input.value); };
    input.addEventListener('input', changed);
    input.addEventListener('change', changed);
  }

  /** Forgets everything the gateway said (start, another gateway or none). */
  ArchiveScreen.prototype.reset = function () {
    this.generation = (this.generation || 0) + 1;
    this.home = { rows: [], loadedAt: 0, loading: false, error: null, attribution: '', notice: '' };
    this.rows = []; // what home shows: [{id, title, items: [model], cards: [card | resume item], continue, slug}]
    this.zone = 'search'; // 'search' | 'rows' | 'results' (home), 'genres' | 'sorts' | 'grid' (category)
    this.row = 0;
    this.cols = {}; // row id → focused column
    this.firsts = {}; // row id → first visible column
    this.view = 'home';
    this.search = { text: '', query: null, cards: [], nextPage: null, loading: false, error: null, notice: '', attribution: '',
      focus: 0, timer: null };
    this.category = null;
    Menu.close();
    if (this.homeLines) this.homeLines.clear();
    if (this.page && this.page.isOpen()) this.page.close();
  };

  ArchiveScreen.prototype.client = function () {
    return this.app.dvbRepo ? new CT.ArchiveClient(this.app.dvbRepo.client) : null;
  };

  ArchiveScreen.prototype.show = function () {
    var configured = !!this.app.dvbConfig;
    $('ar-message').classList.toggle('hidden', configured);
    $('ar-message').textContent = configured ? '' : A.NO_GATEWAY;
    $('ar-home').classList.toggle('hidden', !configured || this.view !== 'home');
    $('ar-category').classList.toggle('hidden', !configured || this.view !== 'category');
    if (!configured) {
      $('ar-tmdb').classList.add('hidden');
      return;
    }
    if (!this.home.loading && (this.home.error || Date.now() - this.home.loadedAt > HOME_FRESH_MS)) this.loadHome();
    this.buildRows();
    this.render();
  };

  /** The tab is left (another tab, or the gateway changed): the show page goes too. */
  ArchiveScreen.prototype.hide = function () {
    Menu.close();
    if (this.page.isOpen()) this.page.close();
  };

  ArchiveScreen.prototype.loadHome = function () {
    var self = this;
    var client = this.client();
    var generation = this.generation;
    if (!client) return;
    this.home.loading = true;
    client.home().then(function (home) {
      if (generation !== self.generation) return;
      // `error` with a 200: iVysílání did not answer, our recordings are still there (the text goes above the rows)
      self.home = { rows: home.rows, loadedAt: Date.now(), loading: false, error: null, attribution: home.attribution,
        notice: home.error };
    }, function (e) {
      if (generation !== self.generation) return;
      self.home.loading = false;
      self.home.error = e.userMessage || A.FAILED;
    }).then(function () {
      if (generation !== self.generation || self.app.tab !== 'archive') return;
      self.buildRows();
      self.render();
    });
  };

  /** Home rows: "Pokračovat ve sledování" first (this TV only), then the gateway's rows, each ending in "Vše ›". */
  ArchiveScreen.prototype.buildRows = function () {
    var focusedId = this.rows[this.row] ? this.rows[this.row].id : null;
    var rows = [];
    var resume = this.app.resumeStore.continueRow();
    if (resume.length) {
      rows.push({ id: 'continue', title: 'Pokračovat ve sledování', continue: true, cards: resume,
        items: resume.map(Cards.fromResume) });
    }
    this.home.rows.forEach(function (r) {
      rows.push({ id: r.id, slug: r.id, title: r.title, continue: false, cards: r.cards.concat([null]),
        items: r.cards.map(Cards.fromCard).concat([{ more: 'Vše ›' }]) });
    });
    this.rows = rows;
    var i = rows.map(function (r) { return r.id; }).indexOf(focusedId);
    this.row = i >= 0 ? i : Math.max(0, Math.min(this.row, rows.length - 1));
    if (!rows.length && this.zone === 'rows') this.zone = 'search';
    if (this.homeLines) this.homeLines.clear();
    var self = this;
    this.homeLines = new Cards.Lines($('ar-rows').firstChild, rows.length, ROW_STEP, ROWS_VISIBLE, function (r) {
      return self.rowElement(r);
    });
  };

  ArchiveScreen.prototype.col = function (row) {
    var r = this.rows[row];
    var c = r ? this.cols[r.id] || 0 : 0;
    return r ? Math.min(c, r.items.length - 1) : 0;
  };

  ArchiveScreen.prototype.rowElement = function (r) {
    var self = this;
    var row = this.rows[r];
    var line = el('div', 'ar-row');
    line.appendChild(el('div', 'ar-row-title', row.title));
    var track = el('div', 'ar-strip');
    line.appendChild(track);
    var strip = new Cards.Strip(track, row.items.length, function (i) { return Cards.element(self.app, row.items[i]); });
    strip.first = this.firsts[row.id] || 0;
    line.strip = strip;
    strip.sync(null);
    return line;
  };

  /** Draws the current view (home or category) and its focus. */
  ArchiveScreen.prototype.render = function () {
    if (!this.app.dvbConfig) return;
    $('ar-home').classList.toggle('hidden', this.view !== 'home');
    $('ar-category').classList.toggle('hidden', this.view !== 'category');
    if (this.view === 'home') this.renderHome();
    else this.renderCategory();
    this.renderTmdb();
    this.applyFocus();
  };

  ArchiveScreen.prototype.renderHome = function () {
    var results = this.search.query !== null;
    $('ar-rows').classList.toggle('hidden', results);
    $('ar-results').classList.toggle('hidden', !results);
    if (results) {
      this.setNotice($('ar-notice'), this.search.notice);
      return this.renderGrid($('ar-results'), this.search, this.search.loading ? 'Hledám…' : A.NOTHING_FOUND);
    }
    var note = this.home.error || (this.home.loading && !this.rows.length ? 'Načítám archiv…' : '');
    if (!note && !this.rows.length) note = 'Archiv je zatím prázdný';
    this.setNotice($('ar-notice'), this.home.error ? '' : this.home.notice);
    var track = $('ar-rows').firstChild;
    var message = $('ar-rows').querySelector('.ar-note');
    if (!message) {
      message = el('div', 'ar-note');
      $('ar-rows').appendChild(message);
    }
    message.textContent = note;
    $('ar-rows').classList.toggle('empty', !this.rows.length);
    track.classList.toggle('hidden', !this.rows.length);
  };

  /**
   * A paged grid (search results, a category): `state` = {cards, nextPage, loading, error, focus}; built around
   * the focus only.
   */
  ArchiveScreen.prototype.renderGrid = function (view, state, emptyText) {
    var self = this;
    var track = view.firstChild;
    var rows = Math.ceil(state.cards.length / GRID_COLS);
    if (!state.lines || state.lines.track !== track) {
      track.innerHTML = '';
      state.lines = new Cards.Lines(track, rows, GRID_STEP, GRID_VISIBLE, function (r) {
        var line = el('div', 'ar-grid-row');
        for (var c = 0; c < GRID_COLS; c++) {
          var i = r * GRID_COLS + c;
          if (i >= state.cards.length) break;
          var card = Cards.element(self.app, Cards.fromCard(state.cards[i]));
          card.style.left = c * Cards.STEP_X + 'px';
          card.dataset.index = String(i);
          line.appendChild(card);
        }
        return line;
      });
      state.builtFor = state.cards.length;
    } else if (state.builtFor !== state.cards.length) {
      // a new page: the last (partial) row is built again with its new cards
      var lastRow = Math.floor((state.builtFor - 1) / GRID_COLS);
      if (state.lines.built[lastRow]) {
        track.removeChild(state.lines.built[lastRow]);
        delete state.lines.built[lastRow];
      }
      state.lines.setRows(rows);
      state.builtFor = state.cards.length;
    }
    state.lines.sync(Math.floor(state.focus / GRID_COLS));
    view.classList.toggle('empty', !state.cards.length);
    var note = view.querySelector('.ar-note');
    note.textContent = state.error || (!state.cards.length ? emptyText : state.loading ? 'Načítám další…' : '');
  };

  /** The gateway's text above the cards when iVysílání did not answer (our recordings are listed anyway). */
  ArchiveScreen.prototype.setNotice = function (element, text) {
    element.textContent = text || '';
    element.classList.toggle('hidden', !text);
  };

  /** The grid's state lost its cards (new search, other sub-genre or sort): built anew. */
  function resetGrid(state) {
    if (state.lines) state.lines.clear();
    state.lines = null;
    state.cards = [];
    state.nextPage = null;
    state.focus = 0;
  }

  /** The cards visible on screen now (the rows' visible columns, the grid's visible lines). */
  ArchiveScreen.prototype.visibleCards = function () {
    var cards = [];
    var grid = this.view === 'category' ? this.category : this.search.query !== null ? this.search : null;
    if (grid) {
      var top = grid.lines ? grid.lines.top : 0;
      return grid.cards.slice(top * GRID_COLS, (top + GRID_VISIBLE + 1) * GRID_COLS);
    }
    if (!this.homeLines) return cards;
    for (var r = this.homeLines.top; r < Math.min(this.rows.length, this.homeLines.top + ROWS_VISIBLE + 1); r++) {
      var row = this.rows[r];
      if (row.continue) continue; // this TV's own entries: no TMDb data
      var first = this.firsts[row.id] || 0;
      cards = cards.concat(row.cards.slice(first, first + Cards.VISIBLE + 1).filter(Boolean));
    }
    return cards;
  };

  /**
   * The TMDB logo and notice at the bottom whenever a card on screen has TMDb data (`tmdb`); the sentence from the
   * answer when it sent one, else TMDb's standard one.
   */
  ArchiveScreen.prototype.renderTmdb = function () {
    var on = this.app.dvbConfig && !this.page.isOpen() && this.visibleCards().some(A.showsTmdb);
    var answer = this.view === 'category' && this.category ? this.category
      : this.search.query !== null ? this.search : this.home;
    $('ar-tmdb').classList.toggle('hidden', !on);
    $('ar-tmdb').lastChild.textContent = on ? answer.attribution || A.ATTRIBUTION : '';
  };

  ArchiveScreen.prototype.applyFocus = function () {
    if (this.page.isOpen()) return this.page.applyFocus();
    var active = this.app.inContent() && this.app.tab === 'archive' && !Menu.isOpen();
    var self = this;
    $('ar-search').classList.toggle('focused', active && this.view === 'home' && this.zone === 'search');
    if (this.view === 'home' && this.search.query === null && this.homeLines) {
      this.homeLines.sync(this.zone === 'rows' ? this.row : null);
      Object.keys(this.homeLines.built).forEach(function (k) {
        var r = Number(k);
        var line = self.homeLines.built[k];
        var focused = active && self.zone === 'rows' && r === self.row;
        line.strip.sync(focused ? self.col(r) : null);
        if (focused) self.firsts[self.rows[r].id] = line.strip.first;
        if (!focused) Object.keys(line.strip.built).forEach(function (i) { line.strip.built[i].classList.remove('focused'); });
      });
    }
    var grid = this.view === 'category' ? this.category : this.search.query !== null ? this.search : null;
    var gridZone = this.view === 'category' ? 'grid' : 'results';
    if (grid && grid.lines) {
      Object.keys(grid.lines.built).forEach(function (k) {
        Array.prototype.forEach.call(grid.lines.built[k].childNodes, function (card) {
          card.classList.toggle('focused', active && self.zone === gridZone && Number(card.dataset.index) === grid.focus);
        });
      });
    }
    if (this.view === 'category') this.focusChips(active);
    this.renderTmdb(); // what is on screen may have scrolled
  };

  ArchiveScreen.prototype.editing = function () {
    return document.activeElement === $('ar-search');
  };

  // --- search ---

  /** Typing: the results 0.5 s after the last change; under 2 characters the rows come back. */
  ArchiveScreen.prototype.typed = function (text) {
    var self = this;
    var s = this.search;
    s.text = text;
    clearTimeout(s.timer);
    s.timer = setTimeout(function () { self.runSearch(); }, A.DEBOUNCE_MS);
  };

  ArchiveScreen.prototype.runSearch = function () {
    var s = this.search;
    var query = A.query(s.text);
    if (query === s.query) return;
    resetGrid(s);
    s.query = query;
    s.error = null;
    s.notice = '';
    if (query !== null) this.loadSearch(1);
    this.render();
  };

  ArchiveScreen.prototype.loadSearch = function (page) {
    var self = this;
    var s = this.search;
    var client = this.client();
    var query = s.query;
    if (!client || s.loading) return;
    s.loading = true;
    client.search(query, page).then(function (r) {
      if (s.query !== query) return;
      s.cards = s.cards.concat(r.cards);
      s.nextPage = r.nextPage;
      s.error = null;
      s.notice = r.error;
      s.attribution = r.attribution;
    }, function (e) {
      if (s.query !== query) return;
      s.error = e.userMessage || A.FAILED;
    }).then(function () {
      if (s.query === query) s.loading = false;
      if (s.query === query && self.app.tab === 'archive' && self.view === 'home') self.render();
    });
  };

  /** Clears the search (Return on the results): the rows again. */
  ArchiveScreen.prototype.clearSearch = function () {
    var s = this.search;
    clearTimeout(s.timer);
    $('ar-search').value = '';
    s.text = '';
    resetGrid(s);
    s.query = null;
    s.error = null;
    s.loading = false;
    this.zone = 'search';
    this.render();
  };

  // --- category ---

  ArchiveScreen.prototype.openCategory = function (row) {
    this.category = {
      slug: row.slug, title: row.title, genres: [], genre: null, sort: 'popular', cards: [], nextPage: null,
      loading: false, error: null, notice: '', focus: 0, chip: 0, sortChip: 0, attribution: '', lines: null, token: 0
    };
    this.view = 'category';
    this.zone = 'grid';
    this.loadCategory(1);
    this.render();
  };

  ArchiveScreen.prototype.loadCategory = function (page) {
    var self = this;
    var c = this.category;
    var client = this.client();
    if (!client || (c.loading && page > 1)) return;
    var token = ++c.token;
    c.loading = true;
    client.category(c.slug, c.sort, c.genre, page).then(function (r) {
      if (self.category !== c || c.token !== token) return;
      if (r.title) c.title = r.title;
      if (page === 1) c.genres = r.genres;
      c.cards = c.cards.concat(r.cards);
      c.nextPage = r.nextPage;
      c.error = null;
      c.notice = r.error;
      c.attribution = r.attribution;
    }, function (e) {
      if (self.category !== c || c.token !== token) return;
      c.error = e.userMessage || A.FAILED;
    }).then(function () {
      if (self.category !== c || c.token !== token) return;
      c.loading = false;
      if (!c.cards.length && self.zone === 'grid') self.zone = 'sorts';
      if (self.app.tab === 'archive' && self.view === 'category') self.render();
    });
  };

  ArchiveScreen.prototype.renderCategory = function () {
    var c = this.category;
    $('ar-cat-title').textContent = c.title;
    var genres = $('ar-genres');
    genres.innerHTML = '';
    [{ id: null, title: 'Vše' }].concat(c.genres).forEach(function (g) {
      var chip = el('div', 'day', g.title);
      chip.classList.toggle('selected', g.id === c.genre);
      genres.appendChild(chip);
    });
    genres.classList.toggle('hidden', !c.genres.length);
    var sorts = $('ar-sorts');
    sorts.innerHTML = '';
    A.SORTS.forEach(function (s) {
      var chip = el('div', 'day', s.title);
      chip.classList.toggle('selected', s.id === c.sort);
      sorts.appendChild(chip);
    });
    this.setNotice($('ar-cat-notice'), c.notice);
    this.renderGrid($('ar-cat-grid'), c, c.loading ? 'Načítám…' : 'V této kategorii nic není.');
  };

  ArchiveScreen.prototype.focusChips = function (active) {
    var c = this.category;
    var zone = this.zone;
    Array.prototype.forEach.call($('ar-genres').childNodes, function (chip, i) {
      chip.classList.toggle('focused', active && zone === 'genres' && i === c.chip);
    });
    Array.prototype.forEach.call($('ar-sorts').childNodes, function (chip, i) {
      chip.classList.toggle('focused', active && zone === 'sorts' && i === c.sortChip);
    });
  };

  /** A sub-genre or sort chip chosen: the grid starts again from page 1, the focus stays on the chip. */
  ArchiveScreen.prototype.chooseChip = function () {
    var c = this.category;
    if (this.zone === 'genres') {
      var g = c.chip === 0 ? null : c.genres[c.chip - 1];
      c.genre = g ? g.id : null;
    } else {
      c.sort = A.SORTS[c.sortChip].id;
    }
    resetGrid(c);
    c.error = null;
    c.loading = false;
    this.loadCategory(1);
    this.render();
  };

  // --- keys ---

  /** Returns false when the key leaves the screen upwards (to the tabs), 'input' while typing. */
  ArchiveScreen.prototype.onKey = function (key) {
    if (!this.app.dvbConfig) return key !== 'up';
    if (this.page.isOpen()) {
      this.page.onKey(key);
      return true;
    }
    if (Menu.isOpen()) {
      if (!this.press.active()) Menu.onKey(key); // not the held OK that opened it
      this.applyFocus();
      return true;
    }
    if (this.editing()) {
      if (key === 'done' || key === 'cancel' || key === 'enter' || key === 'back' || key === 'up' || key === 'down') {
        $('ar-search').blur();
        this.runSearch();
        if (key === 'down' || key === 'done' || key === 'enter') this.down();
        this.applyFocus();
        return true;
      }
      return 'input';
    }
    if (this.view === 'category') return this.onCategoryKey(key);
    switch (key) {
      case 'up':
        if (this.zone === 'search') return false;
        this.up();
        break;
      case 'down':
        this.down();
        break;
      case 'left':
      case 'right':
        this.sideways(key === 'left' ? -1 : 1);
        break;
      case 'enter':
        this.enter();
        break;
      default:
        break;
    }
    this.applyFocus();
    return true;
  };

  /** Return: closes what is open, one step at a time; true = handled (else the tabs get the focus). */
  ArchiveScreen.prototype.onBack = function () {
    if (this.page.isOpen()) {
      this.page.onKey('back');
      return true;
    }
    if (Menu.isOpen()) {
      Menu.close();
      this.applyFocus();
      return true;
    }
    if (this.view === 'category') {
      this.view = 'home';
      this.zone = 'rows';
      this.category = null;
      this.render();
      return true;
    }
    if (this.search.query !== null || this.search.text) {
      this.clearSearch();
      return true;
    }
    return false;
  };

  ArchiveScreen.prototype.onKeyUp = function (key) {
    if (key !== 'enter') return;
    if (this.page.isOpen()) this.page.onKeyUp(key);
    else this.press.up();
  };

  ArchiveScreen.prototype.up = function () {
    if (this.zone === 'rows') {
      if (this.row > 0) this.row--;
      else this.zone = 'search';
    } else if (this.zone === 'results') {
      if (this.search.focus >= GRID_COLS) this.search.focus -= GRID_COLS;
      else this.zone = 'search';
    }
  };

  ArchiveScreen.prototype.down = function () {
    if (this.zone === 'search') {
      if (this.search.query !== null) {
        if (this.search.cards.length) this.zone = 'results';
      } else if (this.rows.length) {
        this.zone = 'rows';
      }
    } else if (this.zone === 'rows') {
      if (this.row < this.rows.length - 1) this.row++;
    } else if (this.zone === 'results') {
      this.gridDown(this.search, this.loadSearch.bind(this));
    }
  };

  /** Down in a grid: the next row (its last card when it is shorter); the next page when the end is near. */
  ArchiveScreen.prototype.gridDown = function (state, loadPage) {
    var rows = Math.ceil(state.cards.length / GRID_COLS);
    var row = Math.floor(state.focus / GRID_COLS);
    if (row < rows - 1) state.focus = Math.min(state.cards.length - 1, state.focus + GRID_COLS);
    this.pageAhead(state, loadPage);
  };

  ArchiveScreen.prototype.pageAhead = function (state, loadPage) {
    var rows = Math.ceil(state.cards.length / GRID_COLS);
    if (state.nextPage && !state.loading && Math.floor(state.focus / GRID_COLS) >= rows - PAGE_AHEAD_ROWS) loadPage(state.nextPage);
  };

  ArchiveScreen.prototype.sideways = function (dir) {
    if (this.zone === 'rows') {
      var r = this.rows[this.row];
      this.cols[r.id] = Math.max(0, Math.min(r.items.length - 1, this.col(this.row) + dir));
    } else if (this.zone === 'results') {
      var s = this.search;
      var col = s.focus % GRID_COLS;
      if ((dir < 0 && col > 0) || (dir > 0 && col < GRID_COLS - 1 && s.focus < s.cards.length - 1)) s.focus += dir;
      this.pageAhead(s, this.loadSearch.bind(this));
    }
  };

  ArchiveScreen.prototype.enter = function () {
    if (this.zone === 'search') {
      $('ar-search').focus(); // opens the TV's on-screen keyboard
      return;
    }
    if (this.zone === 'results') {
      var card = this.search.cards[this.search.focus];
      if (card) this.page.open(card);
      return;
    }
    var row = this.rows[this.row];
    if (!row) return;
    var item = row.cards[this.col(this.row)];
    if (row.continue) return this.press.down();
    if (item === null) return this.openCategory(row);
    this.page.open(item);
  };

  /** The focused "Pokračovat" card's entry, or null. */
  ArchiveScreen.prototype.continueItem = function () {
    var row = this.rows[this.row];
    return row && row.continue && this.zone === 'rows' ? row.cards[this.col(this.row)] : null;
  };

  ArchiveScreen.prototype.playContinue = function () {
    var item = this.continueItem();
    if (item) this.app.play(A.continueRequest(item));
  };

  ArchiveScreen.prototype.offerRemove = function () {
    var self = this;
    var item = this.continueItem();
    if (!item) return;
    Menu.open(item.showTitle ? item.showTitle + ' · ' + item.title : item.title, [
      { label: 'Odebrat z řady', action: function () {
        self.app.resumeStore.remove(item.key);
        self.buildRows();
        self.render();
      } },
      { label: 'Zrušit', action: null }
    ]);
    this.applyFocus();
  };

  ArchiveScreen.prototype.onCategoryKey = function (key) {
    var c = this.category;
    var chips = this.zone === 'genres' ? c.genres.length + 1 : A.SORTS.length;
    switch (key) {
      case 'up':
        if (this.zone === 'grid') {
          if (c.focus >= GRID_COLS) c.focus -= GRID_COLS;
          else this.zone = 'sorts';
        } else if (this.zone === 'sorts') {
          if (c.genres.length) this.zone = 'genres';
          else return false;
        } else {
          return false;
        }
        break;
      case 'down':
        if (this.zone === 'genres') this.zone = 'sorts';
        else if (this.zone === 'sorts') {
          if (c.cards.length) this.zone = 'grid';
        } else this.gridDown(c, this.loadCategory.bind(this));
        break;
      case 'left':
      case 'right': {
        var dir = key === 'left' ? -1 : 1;
        if (this.zone === 'genres') c.chip = Math.max(0, Math.min(chips - 1, c.chip + dir));
        else if (this.zone === 'sorts') c.sortChip = Math.max(0, Math.min(chips - 1, c.sortChip + dir));
        else {
          var col = c.focus % GRID_COLS;
          if ((dir < 0 && col > 0) || (dir > 0 && col < GRID_COLS - 1 && c.focus < c.cards.length - 1)) c.focus += dir;
          this.pageAhead(c, this.loadCategory.bind(this));
        }
        break;
      }
      case 'enter':
        if (this.zone === 'grid') {
          if (c.cards[c.focus]) this.page.open(c.cards[c.focus]);
        } else {
          this.chooseChip();
          return true;
        }
        break;
      default:
        break;
    }
    if (this.app.tab === 'archive') this.renderGrid($('ar-cat-grid'), c, c.loading ? 'Načítám…' : 'V této kategorii nic není.');
    this.applyFocus();
    return true;
  };

  /** The player closed: positions may have changed (the row and the show page's buttons). */
  ArchiveScreen.prototype.onPlayerClosed = function () {
    if (this.page.isOpen()) this.page.refresh();
    if (this.app.tab !== 'archive' || !this.app.dvbConfig) return;
    this.buildRows();
    this.render();
  };

  CT.ArchiveScreen = ArchiveScreen;
})(window);
