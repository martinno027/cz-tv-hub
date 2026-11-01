/*
 * The web page's own controls and wiring (spec/web.md "Input (T29)", "Layout (T31)", "Fullscreen"): a "‹ Zpět"
 * link inside the headers that need one (a Nastavení section, the Archiv show page, the player bar; the programme page
 * has its own Zpět button, the Archiv sub-menu its "‹ <category>" item), the fullscreen button at the right end of the tab bar and in the player bar,
 * the mute button (🔊/🔇, spec/apps.md "Mute (T34)") before it in the player bar, and the input, history
 * and fullscreen glue. No strip or overlay over the TV layout.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var BACK = '‹ Zpět';
  var MUTE_HINT = 'M ztlumení · ';
  var END_HINT = 'F celá obrazovka · Esc zpět';

  function el(doc, tag, cls, text) {
    var e = doc.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  /**
   * The player's key hint on the web (the keyboard, not the remote): the audio part only when the stream has
   * several audio tracks. `kind` = 'live' | 'other' | 'timeline' (player.js).
   */
  function hint(kind, tracks) {
    var audio = tracks > 1;
    if (kind === 'live') {
      return 'Mezerník pauza · ▲ ▼ kanál · 0–9 číslo kanálu · ' + (audio ? 'A zvuk · ' : '') + MUTE_HINT + END_HINT;
    }
    if (kind === 'timeline') {
      return 'Mezerník pauza · ◀ ▶ 10 s, podržet = rychleji · ▲ ▼ začátek pořadu · ' +
        (audio ? 'A nebo podržet Enter zvuk · ' : '') + MUTE_HINT + END_HINT;
    }
    return 'Mezerník pauza · ◀ ▶ posun o 10 s · ' + (audio ? '▲ zvuk · ' : '') + MUTE_HINT + END_HINT;
  }

  /** The tracks of what plays now (none before the stream is ready). */
  function playerHint(kind, player) {
    var tracks = 0;
    try {
      tracks = player.target ? player.backend.audioTracks().length : 0;
    } catch (e) {
      tracks = 0;
    }
    return hint(kind, tracks);
  }

  /** Before the app starts: the links and buttons in the Samsung markup, the player's hint. */
  function adapt(doc) {
    CT.Platform.playerHint = playerHint;
    Array.prototype.forEach.call(doc.querySelectorAll('.set-section'), function (section) {
      section.insertBefore(el(doc, 'div', 'web-back-link', BACK), section.firstChild);
    });
    // the Archiv show page has no Zpět button of its own (the programme page has one among its buttons)
    var show = doc.getElementById('show');
    show.insertBefore(el(doc, 'div', 'web-back-link', BACK), show.firstChild);
    var top = doc.getElementById('top');
    top.appendChild(el(doc, 'div', 'web-fullscreen web-fullscreen-top'));
    var osd = doc.getElementById('osd');
    var row = el(doc, 'div', 'web-osd-row');
    row.appendChild(el(doc, 'div', 'web-back-link', BACK));
    var right = el(doc, 'div', 'web-osd-buttons');
    var mute = el(doc, 'div', 'web-mute');
    right.appendChild(mute);
    right.appendChild(el(doc, 'div', 'web-fullscreen web-fullscreen-player'));
    row.appendChild(right);
    osd.insertBefore(row, osd.firstChild);
    renderMute(mute, false);
    CT.Platform.onMute = function (muted) { renderMute(mute, muted); };
    renderFullscreen(doc);
  }

  /** The player bar's mute button: the state's icon (🔊 sound on, 🔇 muted), what a click does as its label. */
  function renderMute(button, muted) {
    var text = CT.Mute.label(muted);
    button.textContent = muted ? CT.Mute.INDICATOR : CT.Mute.ICON_ON;
    button.setAttribute('title', text);
    button.setAttribute('aria-label', text);
    button.classList.toggle('muted', muted);
  }

  function renderFullscreen(doc) {
    CT.WebFullscreen.render(doc, Array.prototype.slice.call(doc.querySelectorAll('.web-fullscreen')));
  }

  /** After the app started: input, the history (Back = the browser's Back), fullscreen state. */
  function wire(doc, win, app) {
    var tracker = null;
    var scheduled = false;
    var sync = function () {
      if (scheduled || !tracker) return;
      scheduled = true;
      setTimeout(function () {
        scheduled = false;
        tracker.sync();
      }, 0);
    };
    CT.WebDnd.install(doc, app); // Kanály: drag and drop (T31)
    var input = CT.WebInput.install(doc, app, {
      history: win.history,
      scale: function () { return CT.WebStage.last ? CT.WebStage.last.scale : 1; },
      onChange: sync
    });
    tracker = new CT.WebHistory.Tracker(app, win.history, function () {
      input.send('back', 'keydown');
      input.send('back', 'keyup');
    }, function (entry) { reopen(app, entry); });
    win.addEventListener('popstate', function (e) { tracker.onPop(e.state); });
    doc.addEventListener('keydown', sync); // after the app's own handler (added before this one)
    if (win.MutationObserver) {
      var observer = new win.MutationObserver(sync);
      ['stage', 'programme', 'show', 'player'].forEach(function (id) {
        observer.observe(doc.getElementById(id), { attributes: true, attributeFilter: ['class'] });
      });
      observer.observe(doc.getElementById('ar-side'), { childList: true, subtree: true });
    }
    // the audio part of the player's hint needs the stream's tracks, known once it plays (hls.js lists them late)
    ['playing', 'loadedmetadata'].forEach(function (name) {
      doc.getElementById('html5player').addEventListener(name, function () {
        if (app.player.isOpen() && app.player.hintKind) app.player.showHint(app.player.hintKind);
      });
    });
    ['fullscreenchange', 'webkitfullscreenchange'].forEach(function (name) {
      doc.addEventListener(name, function () {
        renderFullscreen(doc);
        app.fitStage();
        relayout(doc, app);
      });
    });
    // the stage's height follows the window (T31): the guide and the lists count their rows again
    win.addEventListener('resize', function () { relayout(doc, app); });
    // the show page's buttons come with its data: the header link only without a Zpět button among them
    var showBack = doc.querySelector('#show > .web-back-link');
    var syncShowBack = function () {
      showBack.classList.toggle('hidden', !needsBackLink(app.screens.archive.page.buttons));
    };
    syncShowBack();
    if (win.MutationObserver) new win.MutationObserver(syncShowBack).observe(doc.getElementById('sh-buttons'), { childList: true });
    // capture: also the first key, which only shows the focus (web-input.js stops it before the app)
    doc.addEventListener('keydown', function () { setTimeout(function () { revealFocus(doc, app); }, 0); }, true);
    return { tracker: tracker, input: input };
  }

  /**
   * After the window (and so the stage's height) changed: the guide's visible rows and width, the Archiv grid's and
   * a show's visible lines, at the same scroll position (the TV never changes size, so the screens count once).
   */
  function relayout(doc, app) {
    var guide = app.screens.guide;
    if (guide && guide.rows && guide.rows.length) {
      guide.viewRows = guide.visibleRows();
      guide.viewPx = guide.viewWidth();
      guide.applyScroll();
    }
    var Lines = CT.ArchiveCards.Lines;
    var archive = app.screens.archive;
    var lists = [[archive && archive.grid && archive.grid.lines, 'ar-grid', 350, 2],
      [archive && archive.page && archive.page.list, 'sh-episodes', 76, 5]];
    lists.forEach(function (l) {
      if (!l[0]) return;
      l[0].visible = Lines.fitting(doc.getElementById(l[1]), l[2], l[3]);
      l[0].sync(null);
    });
  }

  /** The show page's header link only when the page has no Zpět button of its own (series; a film page has one). */
  function needsBackLink(buttons) {
    return !(buttons || []).some(function (b) { return b.id === 'back'; });
  }

  /** Keyboard focus inside a page taller than the window: scroll it into view (the page scrolls on the web). */
  function revealFocus(doc, app) {
    var page = app.programme && app.programme.isOpen() ? doc.getElementById('programme')
      : app.screens.archive.page.isOpen() ? doc.getElementById('show') : null;
    var focused = page ? page.querySelector('.focused') : null;
    if (focused) CT.scrollIntoView(focused);
  }

  /** Forward in the history: open the entry again. */
  function reopen(app, entry) {
    if (entry.type === 'section') {
      if (app.tab !== 'settings') app.showTab('settings');
      app.zone = 'content';
      app.screens.settings.openSection(entry.data);
    } else if (entry.type === 'programme') {
      app.openProgramme(entry.data.entry, entry.data.day);
    } else if (entry.type === 'player') {
      app.play(entry.data);
    }
  }

  CT.WebPage = { BACK: BACK, adapt: adapt, renderMute: renderMute, wire: wire, reopen: reopen, relayout: relayout, needsBackLink: needsBackLink, hint: hint, playerHint: playerHint };
})(typeof window !== 'undefined' ? window : global);
