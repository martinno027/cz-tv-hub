/*
 * Fullscreen (spec/web.md "Fullscreen"): the whole page through the browser's Fullscreen API (the player keeps its
 * bar), from a button at the right end of the tab bar and in the player bar, the key F and a double click on the
 * video. Without the API (iPhone) the buttons are hidden.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var ICON = '⛶';
  var ENTER = 'Celá obrazovka';
  var LEAVE = 'Ukončit celou obrazovku';

  /** Whether the browser can put the page into fullscreen. */
  function available(doc) {
    var el = doc.documentElement;
    if (!el || !(el.requestFullscreen || el.webkitRequestFullscreen)) return false;
    var enabled = doc.fullscreenEnabled !== undefined ? doc.fullscreenEnabled : doc.webkitFullscreenEnabled;
    return enabled !== false;
  }

  function active(doc) {
    return !!(doc.fullscreenElement || doc.webkitFullscreenElement);
  }

  /** Into fullscreen, or out of it; nothing without the API. Returns what was asked: 'enter' | 'leave' | null. */
  function toggle(doc) {
    if (!available(doc)) return null;
    var p;
    if (active(doc)) {
      p = (doc.exitFullscreen || doc.webkitExitFullscreen).call(doc);
      if (p && p.catch) p.catch(function () {});
      return 'leave';
    }
    var el = doc.documentElement;
    p = (el.requestFullscreen || el.webkitRequestFullscreen).call(el);
    if (p && p.catch) p.catch(function () {}); // refused (no user gesture): nothing to do
    return 'enter';
  }

  /** The label of a button for the current state. */
  function label(doc) {
    return active(doc) ? LEAVE : ENTER;
  }

  /** Shows/labels the buttons (`buttons` = elements) for `doc`'s state. */
  function render(doc, buttons) {
    var can = available(doc);
    var text = label(doc);
    buttons.forEach(function (b) {
      if (!b) return;
      b.classList.toggle('hidden', !can);
      b.textContent = ICON;
      b.setAttribute('title', text);
      b.setAttribute('aria-label', text);
    });
  }

  CT.WebFullscreen = { ICON: ICON, ENTER: ENTER, LEAVE: LEAVE, available: available, active: active, toggle: toggle,
    label: label, render: render };
})(typeof window !== 'undefined' ? window : global);
