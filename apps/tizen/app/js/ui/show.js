/*
 * The Archiv show page (spec/apps.md "Archiv" → "Show page"): the T15 programme page for a whole show, full
 * screen over the tab. It shows at once from the card and fills in from GET /dvb/archive/show/<id>: facts (source
 * · year · kind · countries · episode count), the TMDb rating, description, creators; a film gets the T15 buttons
 * (Pokračovat od … / Přehrát / Zpět), a series the season chips and its episodes (each Přehrát or Pokračovat od …,
 * not playable ones dimmed), and "Nahráno z antény" lists our recorded episodes. The episode list is built around
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
  var PAGE_AHEAD = 4; // the next page of episodes is asked for this many episodes before the end

  function $(id) {
    return document.getElementById(id);
  }

  function ShowPage(app, screen) {
    this.app = app;
    this.screen = screen;
    this.card = null;
    this.data = null; // the parsed show, once loaded
    this.error = null;
    this.season = 0;
    this.seasonFocus = 0; // the season chip focused (chosen with OK)
    this.zone = 'buttons'; // 'buttons' | 'seasons' | 'episodes' | 'none' (a series page with nothing to focus)
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
   * season replaces the iVysílání episodes; a group index: its next page is added to it. Our recorded episodes stay.
   */
  ShowPage.prototype.load = function (season, page, first, group) {
    var self = this;
    var client = this.screen.client();
    var card = this.card;
    if (!client) return;
    var token = ++this.token;
    if (group !== null && group !== undefined) this.loadingPage = true;
    client.show(card.id, season, page).then(function (show) {
      if (self.card !== card || self.token !== token) return;
      self.loadingPage = false;
      if (first) self.data = show;
      else if (group !== null && group !== undefined) self.data.groups = A.withPage(self.data.groups, group, show.groups);
      else self.data.groups = A.withSeason(self.data.groups, show.groups);
      if (!first) self.data.error = show.error;
      self.error = null;
      self.render(first);
    }, function (e) {
      if (self.card !== card || self.token !== token) return;
      self.loadingPage = false;
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
    this.renderEpisodes();
    $('sh-state').textContent = this.stateLine();
    if (fresh) {
      this.zone = this.isFilm() ? 'buttons' : this.episodes.length ? 'episodes' : this.hasSeasons() ? 'seasons' : 'none';
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
    this.list = new Cards.Lines(track, lines.length, EP_STEP, EP_VISIBLE, function (i) { return self.lineElement(i); });
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
    var seasons = this.data && !this.isFilm() && this.data.seasons.length > 1;
    switch (key) {
      case 'back':
        this.close();
        return;
      case 'up':
        if (this.zone === 'episodes') {
          if (this.episode > 0) this.episode--;
          else if (seasons) this.enterSeasons();
          else if (this.buttons.length) this.zone = 'buttons';
        } else if (this.zone === 'seasons' && this.buttons.length) {
          this.zone = 'buttons';
        }
        break;
      case 'down':
        if (this.zone === 'buttons' || this.zone === 'none') {
          if (seasons) this.enterSeasons();
          else if (this.episodes.length) this.zone = 'episodes';
        } else if (this.zone === 'seasons') {
          if (this.episodes.length) this.zone = 'episodes';
        } else if (this.episode < this.episodes.length - 1) {
          this.episode++;
        }
        this.pageAhead();
        break;
      case 'left':
      case 'right': {
        var dir = key === 'left' ? -1 : 1;
        if (this.zone === 'buttons') this.button = Math.max(0, Math.min(this.buttons.length - 1, this.button + dir));
        else if (this.zone === 'seasons') {
          this.seasonFocus = Math.max(0, Math.min(this.data.seasons.length - 1, this.seasonFocus + dir));
        }
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

  ShowPage.prototype.enterSeasons = function () {
    this.zone = 'seasons';
    this.seasonFocus = this.season;
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
    if (this.zone === 'seasons') {
      if (this.seasonFocus === this.season) return;
      this.season = this.seasonFocus;
      this.episode = 0;
      this.load(this.data.seasons[this.season].id, 1, false, null);
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

  /** Near the end of the focused episode's group: its next page (`?season=<its season>&page=<its nextPage>`). */
  ShowPage.prototype.pageAhead = function () {
    var data = this.data;
    var line = this.lines[this.episodes[this.episode]];
    if (!data || !line || this.loadingPage || this.zone !== 'episodes') return;
    var group = data.groups[line.group];
    if (!group || !group.nextPage) return;
    var at = this.episodes[this.episode];
    var left = this.lines.filter(function (l, i) { return i > at && l.episode && l.group === line.group; }).length;
    if (left < PAGE_AHEAD) this.load(group.season, group.nextPage, false, line.group);
  };

  /** After the player: the buttons (a new "Pokračovat" focused first) and the episodes' positions again. */
  ShowPage.prototype.refresh = function () {
    if (!this.card) return;
    if (this.zone === 'buttons') this.buttons = [];
    this.render(false);
  };

  CT.ShowPage = ShowPage;
})(window);
