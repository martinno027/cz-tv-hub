/*
 * The programme page (spec/apps.md "Programme page (T15)"): selecting a programme in the guide opens it full
 * screen instead of the old choice. It shows at once from the guide's data; the gateway's TMDb details
 * (GET /dvb/details, CT programmes too) fill in when they come. Return closes it and the guide keeps its focus.
 * Posters come through the gateway with the key, into blob URLs kept by the app's ImageLoader (memory only).
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  function $(id) {
    return document.getElementById(id);
  }

  function ProgrammePage(app) {
    this.app = app;
    this.entry = null;
    this.day = null;
    this.details = null; // the gateway's answer for this entry, or null (none yet, 404, no gateway)
    this.buttons = []; // [{id, label, request, el}], the last one is Zpět
    this.focus = 0;
  }

  ProgrammePage.prototype.isOpen = function () {
    return !!this.entry;
  };

  /** `day` = the guide's broadcast day the entry came from (the CT details lookup needs it). */
  ProgrammePage.prototype.open = function (entry, day) {
    var self = this;
    this.entry = entry;
    this.day = day;
    this.details = null;
    this.buttons = [];
    this.focus = 0;
    $('programme').classList.remove('hidden');
    this.render();
    this.renderButtons();
    var repo = this.app.dvbRepo;
    if (!repo) return;
    CT.Programme.fetchDetails(repo.client, entry, day).then(function (details) {
      if (self.entry !== entry || !details) return;
      self.details = details;
      self.render();
    });
  };

  ProgrammePage.prototype.close = function () {
    this.entry = null;
    this.details = null;
    $('programme').classList.add('hidden');
    $('pg-image').style.backgroundImage = '';
  };

  /**
   * Every minute (the state may have moved on; the focus stays on its button) and after the player closed (a
   * position may have been saved: `fromStart` puts the focus on the first button again, "Pokračovat" when offered).
   */
  ProgrammePage.prototype.refresh = function (fromStart) {
    if (!this.entry) return;
    if (fromStart) this.focus = -1;
    this.render();
    this.renderButtons();
  };

  ProgrammePage.prototype.render = function () {
    var entry = this.entry;
    var now = Date.now();
    var P = CT.Programme;
    var match = this.details ? this.details.match : null;
    $('pg-title').textContent = entry.title;
    $('pg-episode').textContent = entry.subtitle || '';

    var facts = [CT.Days.label(CT.Days.chipDay(entry.start), CT.Days.today(now)) + ' ' + CT.Days.timeRange(entry.start, entry.end),
      P.minutes(entry) + ' min'];
    var genre = entry.genre || (match ? match.genres.slice(0, 2).join(', ') : '');
    if (genre) facts.push(genre);
    if (match && match.year) facts.push(String(match.year));
    var factsEl = $('pg-facts');
    factsEl.innerHTML = '';
    factsEl.appendChild(CT.sourceIcon(entry.source));
    factsEl.appendChild(document.createTextNode([entry.channel.name].concat(facts).join('  ·  ')));

    setLine('pg-rating', match ? P.ratingLine(match.rating) : null);
    setLine('pg-original', P.originalTitleLine(match, entry.title));
    $('pg-description').textContent = entry.description || (match ? match.overview : '');
    $('pg-state').textContent = P.stateLine(entry, now);
    $('pg-attribution').textContent = match ? this.details.attribution : '';
    $('pg-tmdb').classList.toggle('hidden', !match);
    this.renderImage(match);
  };

  function setLine(id, text) {
    var line = $(id);
    line.textContent = text || '';
    line.classList.toggle('hidden', !text);
  }

  /** The TMDb poster when there is one, else the guide's image / gateway thumbnail / placeholder. */
  ProgrammePage.prototype.renderImage = function (match) {
    var self = this;
    var entry = this.entry;
    var guideImage = this.app.screens.guide.imageOf(entry);
    var placeholder = entry.source === 'dvb' ? 'dvb' : '';
    var showGuide = function () {
      self.setImage('', placeholder, false);
      if (!guideImage) return;
      self.app.images.load(guideImage, function (url, status) {
        if (self.entry !== entry || self.showsPoster) return;
        if (url) self.setImage(url, placeholder, false);
        // CT's CDN refused the blob request: let the image element load it, as in the guide detail
        else if (guideImage.kind === 'ct' && status === 0) self.setImage(guideImage.url, placeholder, false);
      });
    };
    this.showsPoster = false;
    if (!match || !match.poster) return showGuide();
    var request = { key: 'gw:poster:' + match.poster, kind: 'gw', final: true, path: match.poster };
    var cached = this.app.images.get(request.key);
    if (cached) {
      this.showsPoster = true;
      return this.setImage(cached, '', true);
    }
    showGuide();
    this.app.images.load(request, function (url) {
      if (self.entry !== entry || !url) return;
      self.showsPoster = true;
      self.setImage(url, '', true);
    });
  };

  ProgrammePage.prototype.setImage = function (url, placeholder, poster) {
    var image = $('pg-image');
    image.style.backgroundImage = url ? 'url("' + url.replace(/"/g, '%22') + '")' : '';
    image.classList.toggle('poster', !!poster);
    image.classList.toggle('placeholder', !url && !!placeholder);
    image.innerHTML = '';
    if (!url && placeholder) image.appendChild(CT.sourceIcon(placeholder));
    image.style.visibility = url || placeholder ? 'visible' : 'hidden';
  };

  /** The decision's buttons (no "Otevřít v iVysílání" on Samsung) and Zpět; focus on the first. */
  ProgrammePage.prototype.renderButtons = function () {
    var box = $('pg-buttons');
    var focusedId = this.buttons[this.focus] ? this.buttons[this.focus].id : null;
    this.buttons = CT.Programme.actions(this.entry, Date.now(), this.app.resumeStore);
    this.buttons.push({ id: 'back', label: 'Zpět', request: null });
    box.innerHTML = '';
    this.buttons.forEach(function (b) {
      b.el = document.createElement('div');
      b.el.className = 'button';
      b.el.textContent = b.label;
      box.appendChild(b.el);
    });
    // keep the focus on the same button when it is still there (e.g. after the player closed), else the first
    var same = this.buttons.map(function (b) { return b.id; }).indexOf(focusedId);
    this.focus = Math.max(0, same);
    this.applyFocus();
  };

  ProgrammePage.prototype.applyFocus = function () {
    var focus = this.focus;
    this.buttons.forEach(function (b, i) { b.el.classList.toggle('focused', i === focus); });
  };

  ProgrammePage.prototype.onKey = function (key) {
    switch (key) {
      case 'left':
        this.focus = Math.max(0, this.focus - 1);
        break;
      case 'right':
        this.focus = Math.min(this.buttons.length - 1, this.focus + 1);
        break;
      case 'enter': {
        var b = this.buttons[this.focus];
        if (!b.request) return this.app.closeProgramme();
        this.app.play(b.request);
        return;
      }
      case 'back':
        this.app.closeProgramme();
        return;
      default:
        break;
    }
    this.applyFocus();
  };

  CT.ProgrammePage = ProgrammePage;
})(window);
