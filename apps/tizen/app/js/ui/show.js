/*
 * The Archiv show page (spec/apps.md "Archiv" → "Show page"): the T15 programme page for a whole show, full
 * screen over the tab. It shows at once from the card and fills in from GET /dvb/archive/show/<id>: facts (source
 * · year · kind · countries · episode count), the TMDb rating, description, creators; a film gets the T15 buttons
 * (Pokračovat od … / Přehrát / Zpět), a series the season chips and its episodes (each Přehrát or Pokračovat od …,
 * not playable ones dimmed), and "Nahráno z antény" lists our recorded episodes. A ČT list of more than 100 episodes
 * comes a month at a time: year and month chips under the season chips (T32). The episode list is built around
 * the focus only. Return closes it; after the player the buttons and positions are recomputed.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var A = CT.Archive;
  var Cards = CT.ArchiveCards;
  var el = Cards.el;

  var EP_STEP = 76;
  var EP_VISIBLE = 5;
  var EP_VISIBLE_PERIODS = 4; // with the year and month chips
  var ZONES = ['buttons', 'seasons', 'years', 'months', 'episodes']; // top to bottom
  var PAGE_AHEAD = 4; // the next page of episodes is asked for this many episodes before the end

  function $(id) {
    return document.getElementById(id);
  }

  /** A long chip row (a show since 2005 has 22 years) scrolls so that `chip` (focused, else selected) is in view. */
  function revealChip(box, chip) {
    if (!chip) return;
    var left = chip.offsetLeft - box.offsetLeft;
    if (left < box.scrollLeft) box.scrollLeft = left;
    else if (left + chip.offsetWidth > box.scrollLeft + box.clientWidth) box.scrollLeft = left + chip.offsetWidth - box.clientWidth;
  }

  function ShowPage(app, screen) {
    this.app = app;
    this.screen = screen;
    this.card = null;
    this.data = null; // the parsed show, once loaded
    this.error = null;
    this.season = 0;
    this.seasonFocus = 0; // the season chip focused (chosen with OK)
    this.zone = 'buttons'; // one of ZONES | 'none' (a series page with nothing to focus)
    this.yearFocus = 0; // the year / month chip focused (chosen with OK)
    this.monthFocus = 0;
    this.choice = null; // {year, month?} asked for and not answered yet: its chip shows selected meanwhile
    this.buttons = [];
    this.button = 0;
    this.lines = []; // the episode list: [{header} | {episode}]
    this.episodes = []; // indexes into lines of the focusable episodes
    this.episode = 0;
    this.list = null;
    this.loadingPage = false;
    this.token = 0;
    var self = this;
    // OK on an episode with a "Pokračovat" offer: short = from the position, long = "Přehrát od začátku"
    this.press = new Cards.LongPress(function () { self.playEpisode(false); }, function () { self.offerFromStart(); });
  }

  ShowPage.prototype.isOpen = function () {
    return !!this.card;
  };

  ShowPage.prototype.open = function (card) {
    this.card = card;
    this.data = null;
    this.error = null;
    this.season = 0;
    this.seasonFocus = 0;
    this.yearFocus = 0;
    this.monthFocus = 0;
    this.choice = null;
    this.zone = 'buttons';
    this.button = 0;
    this.episode = 0;
    this.loadingPage = false;
    $('show').classList.remove('hidden');
    this.render();
    this.load(null, 1, true, null);
  };

  ShowPage.prototype.close = function () {
    this.card = null;
    this.data = null;
    this.token++;
    $('show').classList.add('hidden');
    $('sh-image').style.backgroundImage = '';
    if (this.list) this.list.clear();
    this.list = null;
    this.screen.applyFocus();
  };

  /**
   * GET /dvb/archive/show/<id> (`season` = null: the show as the gateway opens it). `group` = null: page 1 of a
   * season (or of a `period` {year, month?}, T32) replaces the iVysílání episodes; a group index: its next page is
   * added to it. Our recorded episodes stay. `failed` (optional) puts back what was chosen before.
   */
  ShowPage.prototype.load = function (season, page, first, group, period, failed) {
    var self = this;
    var client = this.screen.client();
    var card = this.card;
    if (!client) return;
    var token = ++this.token;
    if (group !== null && group !== undefined) this.loadingPage = true;
    client.show(card.id, season, page, period || null).then(function (show) {
      if (self.card !== card || self.token !== token) return;
      self.loadingPage = false;
      self.choice = null;
      if (first) self.data = show;
      else if (group !== null && group !== undefined) self.data.groups = A.withPage(self.data.groups, group, show.groups);
      else self.data.groups = A.withSeason(self.data.groups, show.groups);
      if (!first) self.data.error = show.error;
      self.error = null;
      self.render(first);
    }, function (e) {
      if (self.card !== card || self.token !== token) return;
      self.loadingPage = false;
      self.choice = null; // the chips show what is listed again
      if (failed) failed();
      if (first) self.error = e.userMessage || A.FAILED;
      else self.app.toast(e.userMessage || A.FAILED);
      self.render(false);
    });
  };

  ShowPage.prototype.hasSeasons = function () {
    return !!this.data && !this.isFilm() && this.data.seasons.length > 1;
  };

  ShowPage.prototype.isFilm = function () {
    var card = this.data ? this.data.card : this.card;
    return card.kind === 'movie';
  };

  /** Everything from the data there is; `fresh` = the answer just came: the focus goes to its starting place. */
  ShowPage.prototype.render = function (fresh) {
    var P = CT.Programme;
    var data = this.data;
    var card = data ? data.card : this.card;
    $('sh-title').textContent = card.title;
    var facts = $('sh-facts');
    facts.innerHTML = '';
    A.sources(card).forEach(function (s) { facts.appendChild(CT.sourceIcon(s)); });
    var factList = data ? A.showFacts(data) : A.showFacts({ card: card, countries: [] });
    facts.appendChild(document.createTextNode(factList.join('  ·  ')));
    var rating = P.ratingLine(card.rating);
    $('sh-rating').textContent = rating || '';
    $('sh-rating').classList.toggle('hidden', !rating);
    $('sh-description').textContent = data ? data.description : '';
    $('sh-description').classList.toggle('film', this.isFilm());
    // the gateway sends ČT's own line ("Režie: … Hrají: …"), so no label of ours in front
    $('sh-creators').textContent = data ? data.creators.join(', ') : '';
    var tmdb = A.showsTmdb(card) || !!rating;
    $('sh-tmdb').classList.toggle('hidden', !tmdb);
    $('sh-tmdb').lastChild.textContent = tmdb ? (data && data.attribution) || A.ATTRIBUTION : '';
    // iVysílání did not answer: the gateway's text above the groups (our recorded episodes are still listed)
    $('sh-notice').textContent = data ? data.error : '';
    $('sh-notice').classList.toggle('hidden', !data || !data.error);
    this.renderImage(card);
    this.renderButtons();
    this.renderSeasons();
    this.renderPeriods();
    this.renderEpisodes();
    $('sh-state').textContent = this.stateLine();
    if (fresh) {
      this.zone = this.isFilm() ? 'buttons' : this.episodes.length ? 'episodes' : this.hasSeasons() ? 'seasons' : 'none';
      // a show without seasons that comes a month at a time starts on its selected year (T32)
      if (!this.isFilm() && !this.hasSeasons() && this.periods()) this.moveZoneTo('years');
      this.button = 0;
      this.episode = 0;
    }
    this.applyFocus();
  };

  ShowPage.prototype.stateLine = function () {
    if (this.error) return this.error;
    if (!this.data) return 'Načítám…';
    if (this.isFilm()) {
      var ep = A.filmEpisode(this.data);
      if (!ep) return 'Pořad teď nelze přehrát';
      if (ep.source === 'rec' && !ep.playable) return CT.DvbMessages.NOT_RECORDED;
      return ep.playable ? '' : 'Ze záznamu zřejmě nedostupné';
    }
    return this.episodes.length ? '' : 'Žádné díly k přehrání';
  };

  /** The card image: a TMDb poster as a portrait, a CT image or thumbnail as a landscape, else the source icon. */
  ShowPage.prototype.renderImage = function (card) {
    var box = $('sh-image');
    box.style.backgroundImage = '';
    box.classList.remove('loaded');
    box.classList.toggle('poster', CT.Programme.POSTER_PATH.test(card.image || ''));
    box.innerHTML = '';
    var icons = el('div', 'ar-placeholder');
    A.sources(card).forEach(function (s) { icons.appendChild(CT.sourceIcon(s)); });
    box.appendChild(icons);
    Cards.loadImage(this.app, box, card.image);
  };

  /** Film: the T15 buttons for its episode; series: Zpět (the episodes have their own). */
  ShowPage.prototype.renderButtons = function () {
    var focusedId = this.buttons[this.button] ? this.buttons[this.button].id : null;
    var buttons = [];
    var data = this.data;
    if (data && this.isFilm()) {
      var ep = A.filmEpisode(data);
      if (ep && (ep.playable || ep.source === 'ct')) {
        var offer = this.app.resumeStore.offer(A.resumeKey(ep));
        if (offer) buttons.push({ id: 'resume', label: 'Pokračovat od ' + offer.label, request: A.episodeRequest(ep, data, offer.progress) });
        buttons.push({ id: 'play', label: 'Přehrát', request: A.episodeRequest(ep, data) });
      }
    }
    // a film page ends with Zpět as T15; a series page has none (Return closes it)
    if (this.isFilm()) buttons.push({ id: 'back', label: 'Zpět', request: null });
    this.buttons = buttons;
    var box = $('sh-buttons');
    box.innerHTML = '';
    buttons.forEach(function (b) {
      b.el = el('div', 'button', b.label);
      box.appendChild(b.el);
    });
    var same = buttons.map(function (b) { return b.id; }).indexOf(focusedId);
    this.button = Math.max(0, same);
  };

  ShowPage.prototype.renderSeasons = function () {
    var box = $('sh-seasons');
    var seasons = this.data && !this.isFilm() ? this.data.seasons : [];
    var current = this.season;
    box.innerHTML = '';
    seasons.forEach(function (s, i) {
      var chip = el('div', 'day', s.title);
      chip.classList.toggle('selected', i === current);
      box.appendChild(chip);
    });
    box.classList.toggle('hidden', seasons.length < 2);
    if (seasons.length < 2 && this.zone === 'seasons') this.zone = this.buttons.length ? 'buttons' : 'none';
  };

  /**
   * The periods shown: the gateway's, with a choice not answered yet as selected; while another year loads its
   * months are not known yet, so the month chips are hidden.
   */
  ShowPage.prototype.periods = function () {
    var periods = this.data && !this.isFilm() ? A.periodsOf(this.data.groups) : null;
    if (!periods || !this.choice) return periods;
    if (!this.choice.month) return { years: periods.years, year: this.choice.year, months: [], month: null };
    return { years: periods.years, year: this.choice.year, months: periods.months, month: this.choice.month };
  };

  /** Year and month chips (T32) under the season chips; the focus leaves a row that is gone. */
  ShowPage.prototype.renderPeriods = function () {
    var chips = A.periodChips(this.periods());
    [['sh-years', chips.years], ['sh-months', chips.months]].forEach(function (row) {
      var box = $(row[0]);
      box.innerHTML = '';
      row[1].forEach(function (c) {
        var chip = el('div', 'day', c.label);
        chip.classList.toggle('selected', c.selected);
        box.appendChild(chip);
      });
      box.classList.toggle('hidden', !row[1].length);
    });
    $('show').classList.toggle('periods', chips.years.length > 0);
    this.yearFocus = Math.min(this.yearFocus, Math.max(0, chips.years.length - 1));
    this.monthFocus = Math.min(this.monthFocus, Math.max(0, chips.months.length - 1));
    if (!chips.years.length && (this.zone === 'years' || this.zone === 'months')) {
      this.zone = this.hasSeasons() ? 'seasons' : this.buttons.length ? 'buttons' : 'none';
    }
  };

  /** The list: a film's other episodes (e.g. ours of it), a series' episodes; groups under their titles. */
  ShowPage.prototype.renderEpisodes = function () {
    var self = this;
    var lines = [];
    var data = this.data;
    if (data) {
      var film = this.isFilm() ? A.filmEpisode(data) : null;
      data.groups.forEach(function (g) {
        var episodes = g.episodes.filter(function (e) { return e !== film; });
        if (!episodes.length) return;
        var index = data.groups.indexOf(g);
        if (g.title) lines.push({ header: g.title });
        episodes.forEach(function (e) { lines.push({ episode: e, group: index }); });
      });
    }
    this.lines = lines;
    this.episodes = [];
    lines.forEach(function (l, i) { if (l.episode) self.episodes.push(i); });
    this.episode = Math.min(this.episode, Math.max(0, this.episodes.length - 1));
    if (!this.episodes.length && this.zone === 'episodes') {
      this.zone = this.hasSeasons() ? 'seasons' : this.buttons.length ? 'buttons' : 'none';
    }
    if (this.list) this.list.clear();
    var track = $('sh-episodes').firstChild;
    var visible = this.periods() ? EP_VISIBLE_PERIODS : EP_VISIBLE;
    this.list = new Cards.Lines(track, lines.length, EP_STEP, visible, function (i) { return self.lineElement(i); });
    $('sh-episodes').classList.toggle('hidden', !lines.length);
  };

  ShowPage.prototype.lineElement = function (i) {
    var line = this.lines[i];
    if (line.header) return el('div', 'sh-group', line.header);
    var e = line.episode;
    var row = el('div', 'sh-ep' + (e.playable ? '' : ' dimmed'));
    row.appendChild(el('span', 'sh-ep-line', A.episodeLine(e, Date.now())));
    var offer = this.app.resumeStore.offer(A.resumeKey(e));
    row.appendChild(el('span', 'sh-ep-action', offer ? 'Pokračovat od ' + offer.label : 'Přehrát'));
    row.dataset.line = String(i);
    return row;
  };

  ShowPage.prototype.applyFocus = function () {
    if (!this.card) return;
    var zone = this.zone;
    var button = this.button;
    this.buttons.forEach(function (b, i) { b.el.classList.toggle('focused', zone === 'buttons' && i === button); });
    var seasonFocus = this.seasonFocus;
    Array.prototype.forEach.call($('sh-seasons').childNodes, function (chip, i) {
      chip.classList.toggle('focused', zone === 'seasons' && i === seasonFocus);
    });
    var yearFocus = this.yearFocus;
    var monthFocus = this.monthFocus;
    [['sh-years', 'years', yearFocus], ['sh-months', 'months', monthFocus]].forEach(function (row) {
      var box = $(row[0]);
      var shown = null;
      Array.prototype.forEach.call(box.childNodes, function (chip, i) {
        var focused = zone === row[1] && i === row[2];
        chip.classList.toggle('focused', focused);
        if (focused || (!shown && chip.classList.contains('selected'))) shown = chip;
      });
      revealChip(box, shown);
    });
    var focusedLine = zone === 'episodes' ? this.episodes[this.episode] : null;
    if (this.list) {
      this.list.sync(focusedLine === null || focusedLine === undefined ? null : focusedLine);
      var built = this.list.built;
      Object.keys(built).forEach(function (k) { built[k].classList.toggle('focused', Number(k) === focusedLine); });
    }
  };

  ShowPage.prototype.onKey = function (key) {
    if (Cards.Menu.isOpen()) {
      if (!this.press.active()) Cards.Menu.onKey(key); // not the held OK that opened it
      return;
    }
    switch (key) {
      case 'back':
        this.close();
        return;
      case 'up':
        if (this.zone === 'episodes' && this.episode > 0) this.episode--;
        else this.moveZone(-1);
        break;
      case 'down':
        if (this.zone === 'episodes') {
          if (this.episode < this.episodes.length - 1) this.episode++;
        } else {
          this.moveZone(1);
        }
        this.pageAhead();
        break;
      case 'left':
      case 'right': {
        var dir = key === 'left' ? -1 : 1;
        var clamp = function (i, n) { return Math.max(0, Math.min(n - 1, i + dir)); };
        if (this.zone === 'buttons') this.button = clamp(this.button, this.buttons.length);
        else if (this.zone === 'seasons') this.seasonFocus = clamp(this.seasonFocus, this.data.seasons.length);
        else if (this.zone === 'years') this.yearFocus = clamp(this.yearFocus, $('sh-years').childNodes.length);
        else if (this.zone === 'months') this.monthFocus = clamp(this.monthFocus, $('sh-months').childNodes.length);
        break;
      }
      case 'enter':
        this.enter();
        return;
      default:
        break;
    }
    this.applyFocus();
  };

  /** The rows there are now, top to bottom (buttons, season / year / month chips, episodes). */
  ShowPage.prototype.zones = function () {
    var chips = A.periodChips(this.periods());
    var present = {
      buttons: this.buttons.length > 0, seasons: this.hasSeasons(), years: chips.years.length > 0,
      months: chips.months.length > 0, episodes: this.episodes.length > 0
    };
    return ZONES.filter(function (z) { return present[z]; });
  };

  /** Up (-1) / down (1) to the next row there is; a chip row is entered on its selected chip. */
  ShowPage.prototype.moveZone = function (dir) {
    var zones = this.zones();
    var at = ZONES.indexOf(this.zone);
    var next = zones.filter(function (z) { return dir > 0 ? ZONES.indexOf(z) > at : ZONES.indexOf(z) < at; });
    if (next.length) this.moveZoneTo(dir > 0 ? next[0] : next[next.length - 1]);
  };

  /** Into row `zone`; a chip row on its selected chip. */
  ShowPage.prototype.moveZoneTo = function (zone) {
    if (zone === 'seasons') this.seasonFocus = this.season;
    var chips = A.periodChips(this.periods());
    var selected = function (list) { return Math.max(0, list.map(function (c) { return c.selected; }).indexOf(true)); };
    if (zone === 'years') this.yearFocus = selected(chips.years);
    if (zone === 'months') this.monthFocus = selected(chips.months);
    this.zone = zone;
  };

  /** The season of the iVysílání list now (the chosen season chip, else the ČT group's own). */
  ShowPage.prototype.ctSeason = function () {
    if (this.hasSeasons()) return this.data.seasons[this.season].id;
    var group = this.data.groups.filter(A.isCtGroup)[0];
    return group ? group.season : null;
  };

  /** OK on a year chip (a year alone: the gateway answers with its newest month) or a month chip. */
  ShowPage.prototype.choosePeriod = function () {
    var chips = A.periodChips(this.periods());
    var period;
    if (this.zone === 'years') {
      var year = chips.years[this.yearFocus];
      if (!year || year.selected) return;
      period = { year: year.year };
    } else {
      var month = chips.months[this.monthFocus];
      if (!month || month.selected) return;
      period = { year: this.periods().year, month: month.month };
    }
    this.choice = period;
    this.episode = 0;
    this.load(this.ctSeason(), 1, false, null, period);
    this.renderPeriods();
    this.applyFocus();
  };

  ShowPage.prototype.enter = function () {
    if (this.zone === 'none') return;
    if (this.zone === 'buttons') {
      var b = this.buttons[this.button];
      if (!b) return;
      if (!b.request) return this.close();
      this.app.play(b.request);
      return;
    }
    if (this.zone === 'years' || this.zone === 'months') return this.choosePeriod();
    if (this.zone === 'seasons') {
      if (this.seasonFocus === this.season) return;
      var self = this;
      var previous = this.season;
      this.season = this.seasonFocus;
      this.episode = 0;
      this.choice = null; // another season comes with its own newest month
      this.load(this.data.seasons[this.season].id, 1, false, null, null, function () {
        self.season = previous; // not answered: the season listed stays chosen
        self.seasonFocus = previous;
      });
      this.renderSeasons();
      this.applyFocus();
      return;
    }
    var e = this.focusedEpisode();
    if (!e) return;
    if (e.source === 'rec' && !e.playable) return this.app.toast(CT.DvbMessages.NOT_RECORDED);
    // with a "Pokračovat" offer OK can be held: "Přehrát od začátku"
    if (this.app.resumeStore.offer(A.resumeKey(e))) return this.press.down();
    this.playEpisode(false);
  };

  ShowPage.prototype.focusedEpisode = function () {
    var line = this.zone === 'episodes' ? this.lines[this.episodes[this.episode]] : null;
    return line ? line.episode : null;
  };

  /**
   * The focused episode: from its "Pokračovat" position, or from the start (`fromStart`). Not marked playable on
   * ČT: played anyway, so ČT's own text says why (licence, DRM).
   */
  ShowPage.prototype.playEpisode = function (fromStart) {
    var e = this.focusedEpisode();
    if (!e) return;
    var offer = fromStart ? null : this.app.resumeStore.offer(A.resumeKey(e));
    this.app.play(A.episodeRequest(e, this.data, offer ? offer.progress : null));
  };

  /** Long OK on an episode with an offer. */
  ShowPage.prototype.offerFromStart = function () {
    var self = this;
    var e = this.focusedEpisode();
    if (!e) return;
    Cards.Menu.open(e.title || this.data.card.title, [
      { label: 'Přehrát od začátku', action: function () { self.playEpisode(true); } },
      { label: 'Zrušit', action: null }
    ]);
  };

  ShowPage.prototype.onKeyUp = function (key) {
    if (key === 'enter') this.press.up();
  };

  /**
   * Near the end of the focused episode's group: its next page (`?season=<its season>&page=<its nextPage>`, with the
   * group's year and month when it has them).
   */
  ShowPage.prototype.pageAhead = function () {
    var data = this.data;
    var line = this.lines[this.episodes[this.episode]];
    // not while another year or month is coming (its answer replaces this list)
    if (!data || !line || this.loadingPage || this.choice || this.zone !== 'episodes') return;
    var group = data.groups[line.group];
    if (!group || !group.nextPage) return;
    var at = this.episodes[this.episode];
    var left = this.lines.filter(function (l, i) { return i > at && l.episode && l.group === line.group; }).length;
    var period = group.periods ? { year: group.periods.year, month: group.periods.month } : null;
    if (left < PAGE_AHEAD) this.load(group.season, group.nextPage, false, line.group, period);
  };

  /** After the player: the buttons (a new "Pokračovat" focused first) and the episodes' positions again. */
  ShowPage.prototype.refresh = function () {
    if (!this.card) return;
    if (this.zone === 'buttons') this.buttons = [];
    this.render(false);
  };

  CT.ShowPage = ShowPage;
})(window);
