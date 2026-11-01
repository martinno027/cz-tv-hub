/*
 * Loads the Samsung app into the web page (spec/web.md "Where it lives": the same files, served at /web/app/): its
 * markup from app/index.html and its scripts in the same order, with the web layer right after the Samsung
 * platform defaults (js/ui/platform.js) and before the screens. A screen or script added to the Samsung app is on
 * the web at once. Samsung-only scripts (the TV's web APIs, the build's gateway preset) are left out; the build info
 * is the web's own (written by the gateway install). No inline script anywhere (the gateway's CSP forbids them).
 */
(function (root) {
  'use strict';

  var APP = 'app/';
  var HOOK = 'js/ui/platform.js'; // the Samsung defaults the web layer replaces
  var WEB = ['js/web-ct.js', 'js/web-player.js', 'js/web-focus.js', 'js/web-scroll.js', 'js/web-history.js',
    'js/web-fullscreen.js', 'js/web-input.js', 'js/web-stage.js', 'js/web-gateway.js', 'js/web-demo.js', 'js/web-page.js',
    'js/platform-web.js'];
  var SKIP = /^\$WEBAPIS\/|^js\/local-config\.js$/; // Samsung's web APIs; a TV build's gateway preset
  var BUILD_INFO = 'js/build-info.js';
  var OPTIONAL = ['js/build-info.js']; // absent outside an install: "vývojová verze"

  /** The scripts of the Samsung index.html (`sources`, in order) → the page's script URLs. */
  function plan(sources) {
    var out = [];
    var hooked = false;
    sources.forEach(function (src) {
      if (SKIP.test(src)) return;
      if (src === BUILD_INFO) {
        out.push(BUILD_INFO); // the web's own, next to this file
        return;
      }
      out.push(APP + src);
      if (src === HOOK) {
        hooked = true;
        WEB.forEach(function (w) { out.push(w); });
      }
    });
    if (!hooked) throw new Error('app/index.html has no ' + HOOK);
    return out;
  }

  /** A markup URL relative to app/index.html → relative to this page; absolute ones stay. */
  function rebase(value) {
    if (!value || /^([a-z]+:|\/|#)/i.test(value)) return value;
    return APP + value;
  }

  function fail(doc, what) {
    var box = doc.getElementById('web-loading');
    if (box) box.textContent = 'Stránku se nepodařilo načíst (' + what + ').';
  }

  function load(doc, urls, done) {
    var left = urls.length;
    var failed = null;
    urls.forEach(function (url) {
      var s = doc.createElement('script');
      s.src = url;
      s.async = false; // in order, as the Samsung index.html runs them
      var finish = function (ok) {
        if (!ok && OPTIONAL.indexOf(url) < 0 && !failed) failed = url;
        if (--left === 0) done(failed);
      };
      s.onload = function () { finish(true); };
      s.onerror = function () { finish(false); };
      doc.body.appendChild(s);
    });
  }

  function start(doc) {
    var xhr = new root.XMLHttpRequest();
    xhr.open('GET', APP + 'index.html', true);
    xhr.onload = function () {
      if (xhr.status !== 200) return fail(doc, 'app/index.html ' + xhr.status);
      var page = new root.DOMParser().parseFromString(xhr.responseText, 'text/html');
      var sources = Array.prototype.map.call(page.querySelectorAll('script[src]'), function (s) {
        return s.getAttribute('src');
      });
      var urls;
      try {
        urls = plan(sources);
      } catch (e) {
        return fail(doc, String(e.message));
      }
      Array.prototype.forEach.call(page.querySelectorAll('body [src]'), function (e) {
        e.setAttribute('src', rebase(e.getAttribute('src')));
      });
      var loading = doc.getElementById('web-loading');
      Array.prototype.slice.call(page.body.children).forEach(function (node) {
        if (node.tagName !== 'SCRIPT') doc.body.insertBefore(doc.importNode(node, true), loading);
      });
      load(doc, urls, function (failed) {
        if (failed) return fail(doc, failed);
        if (loading) loading.parentNode.removeChild(loading);
        root.CT.Web.ready();
      });
    };
    xhr.onerror = function () { fail(doc, 'síť'); };
    xhr.send();
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { plan: plan, rebase: rebase, WEB: WEB, HOOK: HOOK };
  } else {
    start(root.document);
  }
})(typeof window !== 'undefined' ? window : global);
