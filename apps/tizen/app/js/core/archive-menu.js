/*
 * The Archiv menu (spec/apps.md "Archiv", layout T23; golden: spec/fixtures/archive_cases.json "menu"): a menu on the
 * left, a grid on the right that always shows what the selected item filters. Main menu = Hledat, Pokračovat ve
 * sledování, Naše nahrávky, then the gateway's categories (GET /dvb/archive/categories); OK on a category opens its
 * sub-menu ("‹ Filmy", Všechny, its sub-genres from the category answer's `genres`). Pure functions over a state
 * object (a new state per step); the screen (ui/archive.js) only draws it and debounces the focus.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var J = CT.Json;

  var SEARCH = 'search';
  var CONTINUE = 'continue';
  var RECORDINGS = 'recordings';
  var BACK = 'back';
  var ALL = 'all';

  function isObject(v) {
    return v !== null && typeof v === 'object' && !Array.isArray(v);
  }

  /** [{id, title}] of a list, broken entries and repeated ids left out. */
  function idTitles(list) {
    var seen = {};
    return (Array.isArray(list) ? list : []).filter(isObject).map(function (o) {
      var id = J.str(o, 'id');
      var title = J.str(o, 'title');
      if (!id || !title || !title.trim() || seen[id]) return null;
      seen[id] = true;
      return { id: id, title: title.trim() };
    }).filter(Boolean);
  }

  function mainItems(categories) {
    // the fixed items' ids never collide with a category slug ("3947-filmy"); a category called so is left out
    var fixed = [
      { id: SEARCH, title: 'Hledat', kind: 'search' },
      { id: CONTINUE, title: 'Pokračovat ve sledování', kind: 'continue' },
      { id: RECORDINGS, title: 'Naše nahrávky', kind: 'recordings' }
    ];
    return fixed.concat(categories.filter(function (c) {
      return c.id !== SEARCH && c.id !== CONTINUE && c.id !== RECORDINGS;
    }).map(function (c) {
      return { id: c.id, title: c.title, kind: 'category' };
    }));
  }

  function subItems(category, genres) {
    return [
      { id: BACK, title: '‹ ' + category.title, kind: 'back' },
      { id: ALL, title: Menu.ALL_TITLE, kind: 'all' }
    ].concat(genres.filter(function (g) { return g.id !== BACK && g.id !== ALL; }).map(function (g) {
      return { id: g.id, title: g.title, kind: 'genre' };
    }));
  }

  function find(items, id) {
    for (var i = 0; i < items.length; i++) if (items[i].id === id) return items[i];
    return null;
  }

  /** What the grid shows for a selection: {show: search|continue|recordings} or {show: category, category, genre}. */
  function gridOf(level, category, selected) {
    if (level === 'sub') return { show: 'category', category: category, genre: selected === ALL ? null : selected };
    if (selected === SEARCH || selected === CONTINUE || selected === RECORDINGS) return { show: selected };
    return { show: 'category', category: selected, genre: null };
  }

  /** The sort a filter starts with: Nejsledovanější for a category, Nejnovější for our recordings, none else. */
  function defaultSort(grid) {
    if (grid.show === 'category') return 'popular';
    return grid.show === 'recordings' ? 'newest' : null;
  }

  /** The sort's scope: kept while it stays the same (the sub-menu of one category), reset for another. */
  function scope(grid) {
    return grid.show === 'category' ? 'category:' + grid.category : grid.show;
  }

  /**
   * A state: {level: main|sub, categories, genres (slug → [{id, title}]), items, selected, category (sub only),
   * title (sub only), grid, sort}. Built only here.
   */
  function make(base, level, category, selected) {
    var cat = level === 'sub' ? find(base.categories, category) : null;
    var genres = cat ? base.genres[cat.id] || [] : [];
    var grid = gridOf(level, cat ? cat.id : null, selected);
    var keep = base.grid && scope(base.grid) === scope(grid) && base.sort;
    return {
      level: level,
      categories: base.categories,
      genres: base.genres,
      items: level === 'sub' ? subItems(cat, genres) : mainItems(base.categories),
      selected: selected,
      category: cat ? cat.id : null,
      title: cat ? '‹ ' + cat.title : null,
      grid: grid,
      sort: keep ? base.sort : defaultSort(grid)
    };
  }

  function copyGenres(genres) {
    var out = {};
    Object.keys(genres || {}).forEach(function (k) { out[k] = genres[k]; });
    return out;
  }

  var Menu = {
    SEARCH: SEARCH,
    CONTINUE: CONTINUE,
    RECORDINGS: RECORDINGS,
    BACK: BACK,
    ALL: ALL,
    /** Focus resting this long on a menu item changes the grid (no request per item passed). */
    FOCUS_DEBOUNCE_MS: 400,
    ALL_TITLE: 'Všechny',
    EMPTY_CONTINUE: 'Zatím nemáte nic rozkoukaného',
    EMPTY_RECORDINGS: 'Zatím žádné nahrávky',
    RECORDING_SORTS: [
      { id: 'newest', title: 'Nejnovější' },
      { id: 'az', title: 'A–Z' }
    ],

    /** GET /dvb/archive/categories → [{id, title}] in the gateway's order (broken entries left out). */
    parseCategories: function (status, body) {
      var top = CT.DvbParser.ensureOk(status, body);
      return idTitles(J.arr(top, 'categories'));
    },

    /** Sub-genres of a category answer's `genres` ([{id, title}], ČT's order). */
    genres: function (list) {
      return idTitles(list);
    },

    /**
     * The tab opens: the main menu on "Pokračovat ve sledování" when this TV has an entry for it
     * (`continueEntries` > 0), else on the first category; without categories (they did not load) on
     * "Naše nahrávky". `genres` (optional) = sub-genres already known, slug → [{id, title}].
     */
    open: function (categories, continueEntries, genres) {
      var cats = idTitles(categories);
      var first = mainItems(cats).filter(function (i) { return i.kind === 'category'; })[0];
      var selected = continueEntries > 0 ? CONTINUE : first ? first.id : RECORDINGS;
      return make({ categories: cats, genres: copyGenres(genres), grid: null, sort: null }, 'main', null, selected);
    },

    /**
     * The focus rested on `id` (or OK on it in the main menu outside a category): it becomes the selection and the
     * grid follows. The sub-menu's back item selects nothing (only OK on it goes back); an unknown id changes nothing.
     */
    select: function (state, id) {
      var item = find(state.items, id);
      if (!item || item.kind === 'back' || id === state.selected) return state;
      return make(state, state.level, state.category, id);
    },

    /**
     * OK on `id`: {state, focus: 'searchField' | null}. A category opens its sub-menu (Všechny selected = the whole
     * category); the back item returns to the main menu on the category; Hledat selects and puts the focus into the
     * search field; anything else selects at once.
     */
    ok: function (state, id) {
      var item = find(state.items, id);
      if (!item) return { state: state, focus: null };
      if (item.kind === 'back') return { state: Menu.back(state).state, focus: null };
      if (item.kind === 'category') return { state: make(state, 'sub', id, ALL), focus: null };
      return { state: Menu.select(state, id), focus: item.kind === 'search' ? 'searchField' : null };
    },

    /**
     * Back/Return in the menu: {state, leave}. In a sub-menu the main menu with the category selected (the grid shows
     * the whole category again); in the main menu `leave` = the tab is left, as elsewhere.
     */
    back: function (state) {
      if (state.level !== 'sub') return { state: state, leave: true };
      return { state: make(state, 'main', null, state.category), leave: false };
    },

    /**
     * The sub-genres of `slug` became known (its category answer): stored, and the open sub-menu of that category
     * shows them (the selection kept when it is still there, else Všechny).
     */
    withGenres: function (state, slug, genres) {
      var list = idTitles(genres);
      var all = copyGenres(state.genres);
      all[slug] = list;
      var base = {
        level: state.level, categories: state.categories, genres: all, items: state.items, selected: state.selected,
        category: state.category, title: state.title, grid: state.grid, sort: state.sort
      };
      if (state.level !== 'sub' || state.category !== slug) return base;
      var selected = state.selected === ALL || find(list, state.selected) ? state.selected : ALL;
      return make(base, 'sub', slug, selected);
    },

    /** Another sort chosen for the grid's filter (one of `sorts(state)`); anything else changes nothing. */
    withSort: function (state, sort) {
      var ok = Menu.sorts(state).some(function (s) { return s.id === sort; });
      if (!ok || sort === state.sort) return state;
      return {
        level: state.level, categories: state.categories, genres: state.genres, items: state.items,
        selected: state.selected, category: state.category, title: state.title, grid: state.grid, sort: sort
      };
    },

    /** The sort chips above the grid: a category or sub-genre 3, our recordings 2, the others none. */
    sorts: function (state) {
      if (state.grid.show === 'category') return CT.Archive.SORTS;
      return state.grid.show === 'recordings' ? Menu.RECORDING_SORTS : [];
    },

    /** Index of the selected item (where Left from the grid returns). */
    selectedIndex: function (state) {
      for (var i = 0; i < state.items.length; i++) if (state.items[i].id === state.selected) return i;
      return 0;
    },

    /**
     * The gateway request of the grid: {kind: 'category', slug, genre, sort} (our recordings = slug
     * `recordings`), or {kind: 'continue'} / {kind: 'search'} (not one request).
     */
    request: function (state) {
      var g = state.grid;
      if (g.show === 'category') return { kind: 'category', slug: g.category, genre: g.genre, sort: state.sort };
      if (g.show === 'recordings') return { kind: 'category', slug: RECORDINGS, genre: null, sort: state.sort };
      return { kind: g.show };
    },

    /** Text of an empty grid that loaded fine. */
    emptyText: function (state) {
      if (state.grid.show === 'continue') return Menu.EMPTY_CONTINUE;
      if (state.grid.show === 'recordings') return Menu.EMPTY_RECORDINGS;
      return CT.Archive.NOTHING_FOUND;
    },

    /** The state as the golden cases write it. */
    view: function (state) {
      var v = { level: state.level };
      if (state.level === 'sub') {
        v.category = state.category;
        v.title = state.title;
      }
      v.items = state.items.map(function (i) { return i.id; });
      v.selected = state.selected;
      v.grid = state.grid;
      return v;
    }
  };

  /** GET /dvb/archive/categories through the archive client (the key as the header). */
  if (CT.ArchiveClient) {
    CT.ArchiveClient.prototype.categories = function () {
      return this.get('/dvb/archive/categories').then(function (r) { return Menu.parseCategories(r.status, r.body); });
    };
  }

  CT.ArchiveMenu = Menu;
})(typeof window !== 'undefined' ? window : global);
