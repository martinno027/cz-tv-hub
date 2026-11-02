/*
 * The web page's own controls and wiring (spec/web.md "Input (T29)", "Layout (T31)", "Fullscreen"): a "‹ Zpět"
 * link inside the headers that need one (a Nastavení section, the Archiv show page, the player bar; the programme page
 * has its own Zpět button, the Archiv sub-menu its "‹ <category>" item), the fullscreen button at the right end of the tab bar and in the player bar,
 * the mute button (🔊/🔇, spec/apps.md "Mute (T34)") before it in the player bar, the own-window button ⧉/⧈ before
 * ⛶ in the tab bar with the tab's „běží v samostatném okně" line (T46, web-window.js), 📌 „Vždy navrchu" before ⧉ in
 * the player bar with the page's „Přehrává se v okně navrchu" line (T48, web-pip.js), and the input, history and
 * fullscreen glue. No strip or overlay over the TV layout (the T46/T48 lines cover the page only while they apply).
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
    top.appendChild(el(doc, 'div', 'web-window web-window-top hidden'));
    top.appendChild(el(doc, 'div', 'web-fullscreen web-fullscreen-top'));
    var line = el(doc, 'div', 'web-elsewhere');
    line.id = 'web-elsewhere';
    line.appendChild(el(doc, 'p', 'web-elsewhere-text', CT.WebWindow.ELSEWHERE));
    line.appendChild(el(doc, 'div', 'button web-elsewhere-return', CT.WebWindow.RETURN));
    doc.body.appendChild(line);
    var pipLine = el(doc, 'div', 'web-pip-line');
    pipLine.id = 'web-pip-line';
    pipLine.appendChild(el(doc, 'p', 'web-elsewhere-text', CT.WebPip.PLACEHOLDER));
    pipLine.appendChild(el(doc, 'div', 'button web-pip-return', CT.WebPip.RETURN));
    doc.body.appendChild(pipLine);
    var osd = doc.getElementById('osd');
    var row = el(doc, 'div', 'web-osd-row');
    row.appendChild(el(doc, 'div', 'web-back-link', BACK));
    var right = el(doc, 'div', 'web-osd-buttons');
    var mute = el(doc, 'div', 'web-mute');
    right.appendChild(mute);
    right.appendChild(el(doc, 'div', 'web-pip hidden'));
    right.appendChild(el(doc, 'div', 'web-window web-window-player hidden'));
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
    var own = ownWindow(doc, win, app);
    var pip = onTop(doc, win, app);
    var input = CT.WebInput.install(doc, app, {
      history: win.history,
      window: own,
      pip: pip,
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
    return { tracker: tracker, input: input, window: own, pip: pip };
  }

  /**
   * Always on top (T48): the controller, its button and the page's line. P toggles it while the player is open; while
   * the line is up the keyboard only knows Enter/Space/P („Vrátit sem"), as under the T46 line. The player closing
   * for any reason brings the picture back first; the window mirrors the player's message and mute.
   */
  function onTop(doc, win, app) {
    var video = doc.getElementById('html5player');
    var css = doc.querySelector('link[href$="css/web.css"]');
    var playerOpen = function () { return !!app.player && app.player.isOpen(); };
    var message = doc.getElementById('player-message');
    var pip = new CT.WebPip.Controller(win, {
      video: function () { return video; },
      playerOpen: playerOpen,
      playPause: function () {
        app.player.onKey('playpause');
        app.player.onKeyUp('playpause');
      },
      toggleMute: function () { app.player.toggleMute(); },
      muted: function () { return app.player.mute.muted; },
      message: function () { return message.style.display === 'flex' ? message.textContent : ''; },
      toast: function (text) { app.toast(text); },
      render: function (c) {
        CT.WebPip.render(c, Array.prototype.slice.call(doc.querySelectorAll('.web-pip')),
          doc.getElementById('web-pip-line'));
      },
      cssUrl: css ? css.href.replace(/web\.css$/, 'pip.css') : null
    });
    doc.addEventListener('keydown', function (e) {
      if (e.ctName || e.ctrlKey || e.metaKey || e.altKey) return;
      var tag = doc.activeElement ? doc.activeElement.tagName : '';
      if (pip.away()) {
        e.preventDefault();
        e.stopImmediatePropagation();
        if (e.key === 'Enter' || e.key === ' ' || e.keyCode === 13 || e.keyCode === 32 || CT.WebPip.isPipKey(e, tag)) pip.back();
        return;
      }
      if (CT.WebPip.isPipKey(e, tag) && (playerOpen() || pip.state !== 'off')) {
        e.preventDefault();
        e.stopImmediatePropagation();
        pip.toggle();
      }
    }, true);
    var previous = CT.Platform.onMute;
    CT.Platform.onMute = function (muted) {
      if (previous) previous(muted);
      pip.renderWindow();
    };
    if (win.MutationObserver) {
      var player = doc.getElementById('player');
      new win.MutationObserver(function () {
        if (player.classList.contains('hidden')) pip.playerClosed();
      }).observe(player, { attributes: true, attributeFilter: ['class'] });
      new win.MutationObserver(function () { pip.renderWindow(); })
        .observe(message, { attributes: true, attributeFilter: ['style'], childList: true, characterData: true, subtree: true });
    }
    pip.set('off'); // the first render
    return pip;
  }

  /**
   * The own window (T46): the controller, its button and line; while the line is up the keyboard only knows
   * Enter/Space („Vrátit sem"), so nothing plays under it (registered before web-input.js's own key handler).
   */
  function ownWindow(doc, win, app) {
    var own = new CT.WebWindow.Controller(win, {
      stopPlayer: function () {
        if (app.player && app.player.isOpen()) app.player.close();
      },
      render: function (c) {
        CT.WebWindow.render(c, Array.prototype.slice.call(doc.querySelectorAll('.web-window')),
          doc.getElementById('web-elsewhere'));
      },
      toast: function (text) { app.toast(text); },
      playing: function () { return playing(app.player); },
      play: function (what) { playHere(app, what); }
    });
    doc.addEventListener('keydown', function (e) {
      if (!own.elsewhere || e.ctName) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return; // the browser's shortcuts stay
      e.preventDefault();
      e.stopImmediatePropagation();
      if (e.key === 'Enter' || e.key === ' ' || e.keyCode === 13 || e.keyCode === 32) own.returnHere();
    }, true);
    return own.start();
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

  /**
   * What plays now, for the popup to take over (T46): the request and, once the stream is there, the position as the
   * app keeps it when it goes to the background (player.js suspend: a timeline's point, a recording's/archive's
   * position, nothing for live). Null without a player.
   */
  function playing(player) {
    if (!player || !player.isOpen() || !player.request) return null;
    var out = { request: player.request, resumeMs: null, timelineAt: null };
    if (!player.target) return out; // still loading: from where the request itself starts
    try {
      out.timelineAt = player.tl ? player.tl.continueAt() : null;
      out.resumeMs = player.resumePosition();
    } catch (e) {
      out.timelineAt = null;
      out.resumeMs = null;
    }
    return out;
  }

  /** Replaces `obj[name]` for its next call only (`fn(original, args)`), then puts the original back. */
  function once(obj, name, fn) {
    var own = Object.prototype.hasOwnProperty.call(obj, name);
    var original = obj[name];
    obj[name] = function () {
      if (own) obj[name] = original;
      else delete obj[name];
      return fn(original, Array.prototype.slice.call(arguments));
    };
  }

  /**
   * In the popup: play what the tab handed over, through the player's own "continue at" path (player.js onError:
   * `timelineAt` + `load(…, resumePosition())`), so the planner decides: a timeline window around the point, a
   * recording/archive/timeshift at the position, live as live. The player's first load is that one (no second
   * request to ČT or the gateway). When the planner cannot open the timeline window there any more, the request
   * plays as it says (its own start).
   */
  function playHere(app, what) {
    if (!what || !what.request) return;
    var player = app.player;
    var planner = app.planner;
    var at = what.timelineAt || null;
    var resumeMs = typeof what.resumeMs === 'number' ? what.resumeMs : null;
    if (at || resumeMs !== null) {
      once(player, 'load', function (load) { // open()'s load, with the position
        player.timelineAt = at;
        return load.call(player, false, resumeMs);
      });
      if (at) {
        once(planner, 'timelineWindow', function (timelineWindow, args) { // a window that is gone → the start
          return timelineWindow.apply(planner, args).then(null, function () {
            return planner.plan(what.request, false);
          });
        });
      }
    }
    app.play(what.request);
  }

  CT.WebPage = { onTop: onTop, playing: playing, playHere: playHere, BACK: BACK, adapt: adapt, ownWindow: ownWindow, renderMute: renderMute, wire: wire, reopen: reopen, relayout: relayout, needsBackLink: needsBackLink, hint: hint, playerHint: playerHint };
})(typeof window !== 'undefined' ? window : global);
