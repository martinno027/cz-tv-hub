/*
 * "Archiv" (spec/apps.md "Archiv", layout T23): iVysílání and our recordings from the gateway's /dvb/archive. A menu
 * on the left (Hledat, Pokračovat ve sledování, Naše nahrávky, the gateway's categories; OK on a category opens its
 * sub-menu "‹ Filmy" / Všechny / sub-genres) and a 3-column grid on the right that always shows what the selected
 * item filters. The menu's rules are CT.ArchiveMenu (core, golden cases); this screen draws them, waits 0.4 s of
 * resting focus before changing the grid, and moves the focus between the menu, the sort chips, the search field and
 * the grid. A card opens the show page (show.js); a "Pokračovat" card plays at once. Without a gateway the tab only
 * says how to set one up.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var A = CT.Archive;
  var M = CT.ArchiveMenu;
  var Cards = CT.ArchiveCards;
  var el = Cards.el;

  var GRID_COLS = 3;
  var GRID_STEP_X = 460; // a grid card is 430 px wide (CSS #ar-grid .ar-card)
  var GRID_STEP_Y = 350;
  var GRID_VISIBLE = 2; // full lines on screen (the third shows in part)
  var PAGE_AHEAD_ROWS = 2; // the next page is asked for when the focus is this close to the grid's end
  var MENU_STEP = 58; // a menu item (CSS .ar-menu-item)
  var CATEGORIES_FRESH_MS = 10 * 60 * 1000;
  var Menu = Cards.Menu;

  function $(id) {
    return document.getElementById(id);
  }

  function emptyGrid(key) {
    return { key: key, items: [], nextPage: null, loading: false, error: null, notice: '', attribution: '', focus: 0,
      lines: null, builtFor: 0, token: 0 };
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
    this.cats = { list: null, loadedAt: 0, loading: false, error: null };
    this.menu = null; // CT.ArchiveMenu state, once the categories are there
    this.menuFocus = 0;
    this.menuTop = 0;
    this.zone = 'menu'; // 'menu' | 'sorts' | 'field' | 'grid'
    this.sortFocus = 0;
    this.visible = false;
    clearTimeout(this.debounce);
    if (this.grid && this.grid.lines) this.grid.lines.clear();
    this.grid = emptyGrid(null);
    if (this.search) clearTimeout(this.search.timer);
    this.search = { text: '', query: null, timer: null };
    var input = $('ar-search');
    if (input) input.value = '';
    Menu.close();
    if (this.page && this.page.isOpen()) this.page.close();
  };

  ArchiveScreen.prototype.client = function () {
    return this.app.dvbRepo ? new CT.ArchiveClient(this.app.dvbRepo.client) : null;
  };

  /** The tab is shown: opened anew (menu on its starting item) unless it was already on screen (a redraw). */
  ArchiveScreen.prototype.show = function () {
    var configured = !!this.app.dvbConfig;
    $('ar-message').classList.toggle('hidden', configured);
    $('ar-message').textContent = configured ? '' : A.NO_GATEWAY;
    $('ar-layout').classList.toggle('hidden', !configured);
    if (!configured) {
      $('ar-tmdb').classList.add('hidden');
      return;
    }
    var opening = !this.visible;
    this.visible = true;
    var c = this.cats;
    if (!c.loading && (c.error || c.list === null || Date.now() - c.loadedAt > CATEGORIES_FRESH_MS)) this.loadCategories();
    if (opening && c.list !== null) this.openMenu();
    else this.rebuildGrid(); // e.g. thumbnails switched: the cards again
    this.render();
  };

  /** The tab is left (another tab, or the gateway changed): the show page goes too, the next visit opens anew. */
  ArchiveScreen.prototype.hide = function () {
    Menu.close();
    if (this.page.isOpen()) this.page.close();
    this.visible = false;
    clearTimeout(this.debounce);
  };

  ArchiveScreen.prototype.loadCategories = function () {
    var self = this;
    var client = this.client();
    var generation = this.generation;
    if (!client) return;
    this.cats.loading = true;
    client.categories().then(function (list) {
      if (generation !== self.generation) return;
      self.cats = { list: list, loadedAt: Date.now(), loading: false, error: null };
    }, function (e) {
      if (generation !== self.generation) return;
      // the menu still has Hledat, Pokračovat and Naše nahrávky; the categories are asked for again next time
      self.cats = { list: self.cats.list || [], loadedAt: 0, loading: false, error: e.userMessage || A.FAILED };
    }).then(function () {
      if (generation !== self.generation || !self.visible) return;
      if (!self.menu || !self.sameCategories()) self.openMenu();
      self.render();
    });
  };

  /** Whether the menu's categories are the ones loaded (a refresh with the same list keeps the place). */
  ArchiveScreen.prototype.sameCategories = function () {
    var a = this.menu.categories.map(function (c) { return c.id + '=' + c.title; }).join('|');
    var b = this.cats.list.map(function (c) { return c.id + '=' + c.title; }).join('|');
    return a === b;
  };

  ArchiveScreen.prototype.openMenu = function () {
    var known = this.menu ? this.menu.genres : null; // sub-genres already seen stay (no request for them)
    this.menu = M.open(this.cats.list || [], this.app.resumeStore.continueRow().length, known);
    this.menuFocus = M.selectedIndex(this.menu);
    this.zone = 'menu';
    this.sortFocus = 0;
    this.syncGrid();
  };

  // --- the menu ---

  ArchiveScreen.prototype.focusedItem = function () {
    return this.menu ? this.menu.items[this.menuFocus] || null : null;
  };

  /** The focus rests on a menu item: after 0.4 s it becomes the selection (no request per item passed). */
  ArchiveScreen.prototype.moveMenu = function (dir) {
    var self = this;
    var next = this.menuFocus + dir;
    if (next < 0 || next >= this.menu.items.length) return false;
    this.menuFocus = next;
    clearTimeout(this.debounce);
    this.debounce = setTimeout(function () { self.commitFocus(); }, M.FOCUS_DEBOUNCE_MS);
    return true;
  };

  /** The focused menu item becomes the selection at once (debounce over, Right, OK). */
  ArchiveScreen.prototype.commitFocus = function () {
    clearTimeout(this.debounce);
    var item = this.focusedItem();
    if (!item || !this.visible) return;
    var before = this.menu;
    this.menu = M.select(this.menu, item.id);
    if (this.menu !== before) {
      this.sortFocus = 0;
      this.syncGrid();
      if (this.app.tab === 'archive') this.render();
    }
  };

  ArchiveScreen.prototype.menuOk = function () {
    clearTimeout(this.debounce);
    var item = this.focusedItem();
    if (!item) return;
    var r = M.ok(this.menu, item.id);
    var levelChanged = r.state.level !== this.menu.level;
    this.menu = r.state;
    if (levelChanged) this.menuFocus = M.selectedIndex(this.menu);
    this.sortFocus = 0;
    this.syncGrid();
    if (r.focus === 'searchField') {
      this.zone = 'field';
      this.render();
      $('ar-search').focus(); // opens the TV's on-screen keyboard
      return;
    }
    this.render();
  };

  /** Back/Return in the menu: a sub-menu returns to the main menu; false = leave the tab. */
  ArchiveScreen.prototype.menuBack = function () {
    clearTimeout(this.debounce);
    var r = M.back(this.menu);
    if (r.leave) return false;
    this.menu = r.state;
    this.menuFocus = M.selectedIndex(this.menu);
    this.syncGrid();
    this.render();
    return true;
  };

  /** Into the grid area (Right from the menu): the search field, the cards, else the sort chips. */
  ArchiveScreen.prototype.enterGrid = function () {
    var show = this.menu.grid.show;
    if (show === 'search') this.zone = 'field';
    else if (this.grid.items.length) this.zone = 'grid';
    else if (M.sorts(this.menu).length) this.zone = 'sorts';
    else return;
    if (this.zone === 'grid') this.grid.focus = Math.min(this.grid.focus, this.grid.items.length - 1);
  };

  /** Back to the menu, on the selected item. */
  ArchiveScreen.prototype.toMenu = function () {
    this.zone = 'menu';
    this.menuFocus = M.selectedIndex(this.menu);
  };

  ArchiveScreen.prototype.renderMenu = function () {
    var box = $('ar-side');
    var track = box.firstChild;
    var menu = this.menu;
    var active = this.isActive() && this.zone === 'menu';
    track.innerHTML = '';
    if (!menu) return;
    var self = this;
    menu.items.forEach(function (item, i) {
      var e = el('div', 'ar-menu-item', item.title);
      e.classList.toggle('ar-menu-back', item.kind === 'back');
      e.classList.toggle('ar-menu-fixed', item.kind === 'search' || item.kind === 'continue' || item.kind === 'recordings');
      e.classList.toggle('selected', item.id === menu.selected);
      e.classList.toggle('focused', active && i === self.menuFocus);
      e.style.top = i * MENU_STEP + 'px';
      track.appendChild(e);
    });
    var visible = Math.max(1, Math.floor(box.clientHeight / MENU_STEP)); // the items that fit
    var focus = this.zone === 'menu' ? this.menuFocus : M.selectedIndex(menu);
    if (focus < this.menuTop) this.menuTop = focus;
    if (focus > this.menuTop + visible - 1) this.menuTop = focus - visible + 1;
    this.menuTop = Math.max(0, Math.min(this.menuTop, Math.max(0, menu.items.length - visible)));
    track.style.transform = 'translateY(' + -this.menuTop * MENU_STEP + 'px)';
  };

  // --- the grid ---

  /** The grid's key: what it shows (filter, sort, search text); another key = new cards from page 1. */
  ArchiveScreen.prototype.gridKey = function () {
    var r = M.request(this.menu);
    if (r.kind === 'search') return 'search:' + (this.search.query === null ? '' : this.search.query);
    if (r.kind === 'continue') return 'continue';
    return ['category', r.slug, r.genre || '', r.sort].join(':');
  };

  /** The grid follows the menu's selection (and the search text): loaded anew when it shows something else. */
  ArchiveScreen.prototype.syncGrid = function () {
    if (!this.menu) return;
    var key = this.gridKey();
    if (this.grid.key === key) return;
    if (this.grid.lines) this.grid.lines.clear();
    this.grid = emptyGrid(key);
    var r = M.request(this.menu);
    if (r.kind === 'continue') this.grid.items = this.app.resumeStore.continueRow();
    else if (r.kind === 'category') this.loadPage(1);
    else if (this.search.query !== null) this.loadPage(1);
  };

  /** Page `page` of the grid's filter (a category, a sub-genre, our recordings or the search). */
  ArchiveScreen.prototype.loadPage = function (page) {
    var self = this;
    var g = this.grid;
    var client = this.client();
    if (!client || (g.loading && page > 1)) return;
    var r = M.request(this.menu);
    var token = ++g.token;
    var query = this.search.query;
    g.loading = true;
    var call = r.kind === 'search' ? client.search(query, page) : client.category(r.slug, r.sort, r.genre, page);
    call.then(function (answer) {
      if (self.grid !== g || g.token !== token) return;
      if (r.kind === 'category' && r.slug !== M.RECORDINGS && page === 1 && (!r.genre || answer.genres.length)) {
        self.menu = M.withGenres(self.menu, r.slug, answer.genres); // the sub-menu's items
      }
      g.items = g.items.concat(answer.cards);
      g.nextPage = answer.nextPage;
      g.error = null;
      // `error` with a 200: iVysílání did not answer, our recordings are still there (the text goes above the grid)
      g.notice = answer.error;
      g.attribution = answer.attribution;
    }, function (e) {
      if (self.grid !== g || g.token !== token) return;
      g.error = e.userMessage || A.FAILED;
    }).then(function () {
      if (self.grid !== g || g.token !== token) return;
      g.loading = false;
      if (!g.items.length && self.zone === 'grid') self.zone = M.sorts(self.menu).length ? 'sorts' : 'menu';
      if (self.app.tab === 'archive' && self.visible) self.render();
    });
  };

  /** The built cards again (thumbnails switched, an entry removed). */
  ArchiveScreen.prototype.rebuildGrid = function () {
    if (this.grid.lines) this.grid.lines.clear();
    this.grid.lines = null;
  };

  ArchiveScreen.prototype.isContinue = function () {
    return this.grid.key === 'continue';
  };

  ArchiveScreen.prototype.model = function (item) {
    return this.isContinue() ? Cards.fromResume(item) : Cards.fromCard(item);
  };

  /** The grid built around the focus only: lines of 3 cards. */
  ArchiveScreen.prototype.renderGrid = function () {
    var self = this;
    var g = this.grid;
    var view = $('ar-grid');
    var track = view.firstChild;
    var rows = Math.ceil(g.items.length / GRID_COLS);
    if (!g.lines) {
      track.innerHTML = '';
      g.lines = new Cards.Lines(track, rows, GRID_STEP_Y, GRID_VISIBLE, function (r) {
        var line = el('div', 'ar-grid-row');
        for (var c = 0; c < GRID_COLS; c++) {
          var i = r * GRID_COLS + c;
          if (i >= g.items.length) break;
          var card = Cards.element(self.app, self.model(g.items[i]));
          card.style.left = c * GRID_STEP_X + 'px';
          card.dataset.index = String(i);
          line.appendChild(card);
        }
        return line;
      });
      g.builtFor = g.items.length;
    } else if (g.builtFor !== g.items.length) {
      // a new page: the last (partial) line is built again with its new cards
      var lastRow = Math.floor((g.builtFor - 1) / GRID_COLS);
      if (g.lines.built[lastRow]) {
        track.removeChild(g.lines.built[lastRow]);
        delete g.lines.built[lastRow];
      }
      g.lines.setRows(rows);
      g.builtFor = g.items.length;
    }
    g.lines.sync(this.zone === 'grid' ? Math.floor(g.focus / GRID_COLS) : null);
    view.classList.toggle('empty', !g.items.length);
    var note = view.querySelector('.ar-note');
    note.textContent = this.gridNote();
  };

  /** The line under (or instead of) the cards: an error, loading, the empty text. */
  ArchiveScreen.prototype.gridNote = function () {
    var g = this.grid;
    if (g.error) return g.error;
    if (!this.menu) return this.cats.error || 'Načítám…';
    var searching = this.menu.grid.show === 'search';
    if (searching && this.search.query === null) return '';
    if (g.loading) return g.items.length ? 'Načítám další…' : 'Načítám…';
    return g.items.length ? '' : M.emptyText(this.menu);
  };

  ArchiveScreen.prototype.renderSorts = function () {
    var box = $('ar-sorts');
    var sorts = this.menu ? M.sorts(this.menu) : [];
    var menu = this.menu;
    var active = this.isActive() && this.zone === 'sorts';
    var focus = this.sortFocus;
    box.innerHTML = '';
    sorts.forEach(function (s, i) {
      var chip = el('div', 'day', s.title);
      chip.classList.toggle('selected', s.id === menu.sort);
      chip.classList.toggle('focused', active && i === focus);
      box.appendChild(chip);
    });
    box.classList.toggle('hidden', !sorts.length);
  };

  /** Draws the menu, the grid area and the focus. */
  ArchiveScreen.prototype.render = function () {
    if (!this.app.dvbConfig) return;
    var searching = !!this.menu && this.menu.grid.show === 'search';
    $('ar-search-box').classList.toggle('hidden', !searching);
    this.setNotice($('ar-notice'), this.grid.notice || (this.menu && this.cats.error && !this.grid.error ? this.cats.error : ''));
    this.renderSorts();
    this.renderGrid();
    this.applyFocus();
  };

  /** The gateway's text above the cards when iVysílání did not answer (our recordings are listed anyway). */
  ArchiveScreen.prototype.setNotice = function (element, text) {
    element.textContent = text || '';
    element.classList.toggle('hidden', !text);
  };

  /** The cards visible on screen now (the grid's visible lines). */
  ArchiveScreen.prototype.visibleCards = function () {
    var g = this.grid;
    if (this.isContinue()) return []; // this TV's own entries: no TMDb data
    var top = g.lines ? g.lines.top : 0;
    return g.items.slice(top * GRID_COLS, (top + GRID_VISIBLE + 1) * GRID_COLS);
  };

  /**
   * The TMDB logo and notice at the bottom whenever a card in the grid has TMDb data (`tmdb`); the sentence from
   * the answer when it sent one, else TMDb's standard one.
   */
  ArchiveScreen.prototype.renderTmdb = function () {
    var on = !!this.app.dvbConfig && !this.page.isOpen() && this.visibleCards().some(A.showsTmdb);
    $('ar-tmdb').classList.toggle('hidden', !on);
    $('ar-tmdb').lastChild.textContent = on ? this.grid.attribution || A.ATTRIBUTION : '';
  };

  ArchiveScreen.prototype.isActive = function () {
    return this.app.inContent() && this.app.tab === 'archive' && !Menu.isOpen() && !this.page.isOpen();
  };

  ArchiveScreen.prototype.applyFocus = function () {
    if (this.page.isOpen()) return this.page.applyFocus();
    if (!this.app.dvbConfig) return;
    var active = this.isActive();
    var g = this.grid;
    var zone = this.zone;
    this.renderMenu();
    $('ar-search').classList.toggle('focused', active && zone === 'field');
    Array.prototype.forEach.call($('ar-sorts').childNodes, function (chip, i) {
      chip.classList.toggle('focused', active && zone === 'sorts' && i === this.sortFocus);
    }, this);
    if (g.lines) {
      g.lines.sync(zone === 'grid' ? Math.floor(g.focus / GRID_COLS) : null);
      Object.keys(g.lines.built).forEach(function (k) {
        Array.prototype.forEach.call(g.lines.built[k].childNodes, function (card) {
          card.classList.toggle('focused', active && zone === 'grid' && Number(card.dataset.index) === g.focus);
        });
      });
    }
    this.renderTmdb(); // what is on screen may have scrolled
  };

  ArchiveScreen.prototype.editing = function () {
    return document.activeElement === $('ar-search');
  };

  // --- search ---

  /** Typing: the results 0.5 s after the last change, at least 2 characters. */
  ArchiveScreen.prototype.typed = function (text) {
    var self = this;
    var s = this.search;
    s.text = text;
    clearTimeout(s.timer);
    s.timer = setTimeout(function () { self.runSearch(); }, A.DEBOUNCE_MS);
  };

  ArchiveScreen.prototype.runSearch = function () {
    var s = this.search;
    clearTimeout(s.timer);
    var query = A.query(s.text);
    if (query === s.query) return;
    s.query = query;
    if (!this.menu || this.menu.grid.show !== 'search') return;
    this.syncGrid();
    if (this.app.tab === 'archive') this.render();
  };

  /** Clears the search (Back in the field or the results): the field stays focused. */
  ArchiveScreen.prototype.clearSearch = function () {
    var s = this.search;
    clearTimeout(s.timer);
    $('ar-search').value = '';
    s.text = '';
    s.query = null;
    this.zone = 'field';
    this.syncGrid();
    this.render();
  };

  /** Back in the search field or its results: the search is cleared first, then the menu. */
  ArchiveScreen.prototype.searchBack = function () {
    if (this.search.text || this.search.query !== null) return this.clearSearch();
    this.toMenu();
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
    if (this.editing()) return this.onEditingKey(key);
    if (!this.menu) return key !== 'up';
    var handled = true;
    if (this.zone === 'menu') handled = this.onMenuKey(key);
    else if (this.zone === 'sorts') handled = this.onSortsKey(key);
    else if (this.zone === 'field') handled = this.onFieldKey(key);
    else handled = this.onGridKey(key);
    if (handled === false) return false;
    this.applyFocus();
    return true;
  };

  ArchiveScreen.prototype.onEditingKey = function (key) {
    if (key === 'back') {
      $('ar-search').blur();
      this.searchBack();
      return true;
    }
    if (key === 'done' || key === 'cancel' || key === 'enter' || key === 'up' || key === 'down') {
      $('ar-search').blur();
      this.runSearch();
      this.zone = 'field';
      if ((key === 'down' || key === 'done' || key === 'enter') && this.grid.items.length) this.zone = 'grid';
      this.render();
      return true;
    }
    return 'input';
  };

  ArchiveScreen.prototype.onMenuKey = function (key) {
    switch (key) {
      case 'up':
        if (!this.moveMenu(-1)) return false;
        break;
      case 'down':
        this.moveMenu(1);
        break;
      case 'right':
        this.commitFocus();
        this.enterGrid();
        break;
      case 'enter':
        this.menuOk();
        break;
      default:
        break;
    }
    return true;
  };

  ArchiveScreen.prototype.onSortsKey = function (key) {
    var sorts = M.sorts(this.menu);
    switch (key) {
      case 'up':
        return false;
      case 'down':
        if (this.grid.items.length) this.zone = 'grid';
        break;
      case 'left':
        if (this.sortFocus > 0) this.sortFocus--;
        else this.toMenu();
        break;
      case 'right':
        this.sortFocus = Math.min(sorts.length - 1, this.sortFocus + 1);
        break;
      case 'enter':
        // the grid starts again from page 1, the focus stays on the chip
        this.menu = M.withSort(this.menu, sorts[this.sortFocus].id);
        this.syncGrid();
        this.render();
        break;
      default:
        break;
    }
    return true;
  };

  ArchiveScreen.prototype.onFieldKey = function (key) {
    switch (key) {
      case 'up':
        return false;
      case 'down':
        if (this.grid.items.length) this.zone = 'grid';
        break;
      case 'left':
        this.toMenu();
        break;
      case 'enter':
        $('ar-search').focus(); // opens the TV's on-screen keyboard
        break;
      default:
        break;
    }
    return true;
  };

  ArchiveScreen.prototype.onGridKey = function (key) {
    var g = this.grid;
    var col = g.focus % GRID_COLS;
    switch (key) {
      case 'up':
        if (g.focus >= GRID_COLS) g.focus -= GRID_COLS;
        else if (this.menu.grid.show === 'search') this.zone = 'field';
        else if (M.sorts(this.menu).length) this.zone = 'sorts';
        else return false;
        break;
      case 'down':
        if (Math.floor(g.focus / GRID_COLS) < Math.ceil(g.items.length / GRID_COLS) - 1) {
          g.focus = Math.min(g.items.length - 1, g.focus + GRID_COLS);
        }
        this.pageAhead();
        break;
      case 'left':
        if (col > 0) g.focus--;
        else this.toMenu();
        break;
      case 'right':
        if (col < GRID_COLS - 1 && g.focus < g.items.length - 1) g.focus++;
        this.pageAhead();
        break;
      case 'enter':
        if (this.isContinue()) {
          this.press.down();
          return true;
        }
        if (g.items[g.focus]) this.page.open(g.items[g.focus]);
        break;
      default:
        break;
    }
    this.renderGrid();
    return true;
  };

  /** More pages as the focus nears the end ("nextPage"). */
  ArchiveScreen.prototype.pageAhead = function () {
    var g = this.grid;
    var rows = Math.ceil(g.items.length / GRID_COLS);
    if (g.nextPage && !g.loading && Math.floor(g.focus / GRID_COLS) >= rows - PAGE_AHEAD_ROWS) this.loadPage(g.nextPage);
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
    if (!this.app.dvbConfig || !this.menu) return false;
    if (this.zone === 'menu') return this.menuBack();
    if (this.menu.grid.show === 'search') {
      this.searchBack();
      return true;
    }
    this.toMenu();
    this.render();
    return true;
  };

  ArchiveScreen.prototype.onKeyUp = function (key) {
    if (key !== 'enter') return;
    if (this.page.isOpen()) this.page.onKeyUp(key);
    else this.press.up();
  };

  /** The focused "Pokračovat" card's entry, or null. */
  ArchiveScreen.prototype.continueItem = function () {
    return this.isContinue() && this.zone === 'grid' ? this.grid.items[this.grid.focus] || null : null;
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
        self.refreshContinue();
      } },
      { label: 'Zrušit', action: null }
    ]);
    this.applyFocus();
  };

  /** "Pokračovat ve sledování" read again (an entry removed, a position saved), the focus kept where it can be. */
  ArchiveScreen.prototype.refreshContinue = function () {
    if (!this.isContinue()) return;
    var g = this.grid;
    g.items = this.app.resumeStore.continueRow();
    g.focus = Math.max(0, Math.min(g.focus, g.items.length - 1));
    if (!g.items.length && this.zone === 'grid') this.toMenu();
    this.rebuildGrid();
    this.render();
  };

  /** The player closed: positions may have changed (the "Pokračovat" grid and the show page's buttons). */
  ArchiveScreen.prototype.onPlayerClosed = function () {
    if (this.page.isOpen()) this.page.refresh();
    if (this.app.tab !== 'archive' || !this.app.dvbConfig || !this.visible) return;
    this.refreshContinue();
  };

  CT.ArchiveScreen = ArchiveScreen;
})(window);
