/*
 * The web's platform parts (spec/web.md "Web layer"): loaded by boot.js right after the Samsung app's
 * js/ui/platform.js, before the screens; replaces the player, the CT transport, the keys, the gateway address,
 * the layout and the sources, and adds what only the web has (the "‹ Zpět" button, the rotate text, the no-key
 * text, "Otevřít v iVysílání"). Everything else is the Samsung app as it is.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var doc = root.document;

  var IVYSILANI_URL = 'https://www.ceskatelevize.cz/ivysilani/';
  var HLS_JS = { name: 'hls.js', version: '1.7.4', licence: 'Apache-2.0' }; // vendor/hls.min.js, apps/web/README.md
  var CT_REQUESTS = { live: true, startover: true, programme: true, archive: true };
  // Nastavení → Server: how to open the page in its own window (spec/web.md "Install as an app", T35)
  var INSTALL_HINT = 'Vlastní okno bez lišty: v Chrome ⋮ → Uložit a sdílet → Vytvořit zástupce → Otevřít jako ' +
    'okno; v Safari Soubor → Přidat do Docku.';

  function $(id) {
    return doc.getElementById(id);
  }

  function demo() {
    return !!(CT.Demo && CT.Demo.enabled);
  }

  /** "Otevřít v iVysílání": iVysílání's start page in a new tab, nothing of this page sent along. */
  function openIvysilani() {
    root.open(IVYSILANI_URL, '_blank', 'noopener,noreferrer');
  }

  function thirdPartyLine() {
    var version = root.Hls && root.Hls.version ? root.Hls.version : HLS_JS.version;
    return 'Third-party: ' + HLS_JS.name + ' ' + version + ' (' + HLS_JS.licence + ')';
  }

  var P = CT.Platform;
  P.name = 'web';
  P.startOnLoad = false; // boot.js starts the app once every script ran
  P.fixturesBase = 'fixtures/'; // ?demo: the synthetic spec/fixtures, served under /web/fixtures/
  P.ctTransport = function (transport, config) {
    return CT.WebCt.transport(demo() ? CT.WebDemo.wrap(transport) : transport, config);
  };
  P.playerBackend = function (cb) {
    return new CT.WebPlayer.WebBackend(cb, {
      video: $('html5player'),
      Hls: root.Hls || null,
      key: function () { return CT.App.dvbConfig ? CT.App.dvbConfig.key : null; },
      origin: root.location.origin
    });
  };
  /** Anténa is H.265: the Czech text where this browser cannot decode it (the demo's simulated timeline plays). */
  P.refusePlay = function (target) {
    if (!target.dvb || target.psp) return null;
    if (demo() && target.kind === 'timeline') return null;
    return CT.WebPlayer.hevcSupported($('html5player'), root.Hls || null, root) ? null : CT.WebPlayer.HEVC_MESSAGE;
  };
  /** Under every ČT player error (also "ČT jde v prohlížeči přehrát jen doma…"): "Otevřít v iVysílání". */
  P.playErrorAction = function (request) {
    if (!request || !CT_REQUESTS[request.kind]) return null;
    return { label: 'Otevřít v iVysílání', run: openIvysilani };
  };
  P.keyName = function (event, codes) {
    var active = doc.activeElement;
    return CT.WebInput.keyName(event, codes, active ? active.tagName : '');
  };
  P.officialApp = openIvysilani;
  P.gatewayAddress = function () {
    return CT.WebGateway.address(root.location);
  };
  /** Every source as on Samsung; Poslanecká sněmovna approved for the web by the owner on 2026-11-01 (T28). */
  P.sourceAllowed = function () {
    return true;
  };
  P.fitStage = function (stage) {
    CT.WebStage.apply(stage, $('web-rotate'), root);
  };
  P.onGatewayConfig = function (config) {
    doc.body.classList.toggle('web-nokey', !config);
    if (!demo()) CT.WebGateway.syncCookie(doc, config, root.location.protocol === 'https:');
  };
  /** A hidden tab or a minimised window keeps playing; nothing re-resolves on return (spec/web.md T41). */
  P.backgroundPlayback = true;
  P.versionSuffix = ' (web)';

  function el(tag, id, cls, text) {
    var e = doc.createElement(tag);
    if (id) e.id = id;
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  /** The Samsung markup → the web's: what only the web has, and Nastavení → Server without the address. */
  function adaptPage() {
    doc.body.appendChild(el('div', 'web-rotate', '', CT.WebStage.ROTATE_TEXT));
    doc.querySelector('main').appendChild(el('div', 'web-nokey', '', CT.WebCt.NO_KEY));

    var address = $('set-address');
    if (address && address.parentNode) address.parentNode.classList.add('hidden');
    var server = $('set-section-server');
    var hint = server.querySelector('p.hint');
    if (hint) {
      hint.textContent = 'Stránka je přímo z brány cz-tv-hub (' + root.location.host + '). Klíč je v ' +
        '/etc/cztvhub/cztvhub.conf ([dvb] key); zůstane jen v tomto prohlížeči. Bez klíče se nic nezobrazí.';
    }
    $('set-save').textContent = 'Uložit a ověřit';
    $('set-clear').textContent = 'Odebrat bránu';
    var choice = doc.querySelector('#set-choice .choice[data-section="server"] .choice-detail');
    if (choice) choice.textContent = 'Klíč brány Anténa (DVB-T2) na Raspberry Pi';
    var aboutTitle = server.querySelector('h2.about');
    if (aboutTitle) server.insertBefore(el('p', 'web-install', 'hint', INSTALL_HINT), aboutTitle);
    var about = server.querySelector('p.hint.small');
    if (about) {
      about.textContent = 'Neoficiální projekt, není spojen s Českou televizí ani s Poslaneckou sněmovnou PČR. ' +
        'Stránka přehrává jen oficiální streamy ČT, veřejné přímé přenosy Poslanecké sněmovny a vlastní anténu, ' +
        'nic nenahrává, nestahuje ani neobchází DRM.';
      about.parentNode.insertBefore(el('p', 'web-third-party', 'hint small', thirdPartyLine()), about.nextSibling);
    }
    var video = $('html5player');
    video.setAttribute('controlslist', 'nodownload noplaybackrate');
    video.setAttribute('disableremoteplayback', '');
  }

  /** Called by boot.js after the last script: the page, the input, then the app (as Samsung's `load`). */
  function ready() {
    adaptPage();
    CT.WebPage.adapt(doc, root); // T29: Zpět links, fullscreen buttons
    CT.App.start();
    CT.WebPage.wire(doc, root, CT.App); // T29: input, history, fullscreen
    root.addEventListener('orientationchange', function () { CT.App.fitStage(); });
  }

  CT.Web = { IVYSILANI_URL: IVYSILANI_URL, HLS_JS: HLS_JS, INSTALL_HINT: INSTALL_HINT, thirdPartyLine: thirdPartyLine, ready: ready };
})(window);
