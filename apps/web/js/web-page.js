/*
 * The web page's own controls and wiring (spec/web.md "Input (T29)", "Fullscreen"): a "‹ Zpět" link inside the
 * headers that need one on touch (a Nastavení section, the player bar; the programme and show pages have their own
 * Zpět button), the fullscreen button at the right end of the tab bar and in the player bar, and the input, history
 * and fullscreen glue. No strip or overlay over the TV layout.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var BACK = '‹ Zpět';

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
      return 'Mezerník pauza · ▲ ▼ kanál · 0–9 číslo kanálu · ' + (audio ? 'A zvuk · ' : '') + 'F celá obrazovka · Esc zpět';
    }
    if (kind === 'timeline') {
      return 'Mezerník pauza · ◀ ▶ 10 s, podržet = rychleji · ▲ ▼ začátek pořadu · ' +
        (audio ? 'A nebo podržet Enter zvuk · ' : '') + 'F celá obrazovka · Esc zpět';
    }
    return 'Mezerník pauza · ◀ ▶ posun o 10 s · ' + (audio ? '▲ zvuk · ' : '') + 'F celá obrazovka · Esc zpět';
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
    var top = doc.getElementById('top');
    top.appendChild(el(doc, 'div', 'web-fullscreen web-fullscreen-top'));
    var osd = doc.getElementById('osd');
    var row = el(doc, 'div', 'web-osd-row');
    row.appendChild(el(doc, 'div', 'web-back-link', BACK));
    row.appendChild(el(doc, 'div', 'web-fullscreen web-fullscreen-player'));
    osd.insertBefore(row, osd.firstChild);
    renderFullscreen(doc);
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
      });
    });
    return { tracker: tracker, input: input };
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

  CT.WebPage = { BACK: BACK, adapt: adapt, wire: wire, reopen: reopen, hint: hint, playerHint: playerHint };
})(typeof window !== 'undefined' ? window : global);
