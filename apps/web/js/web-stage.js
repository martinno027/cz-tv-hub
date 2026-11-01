/*
 * Layout on the web (spec/web.md "Layout"): the TV's 1920 × 1080 stage scaled to the window, kept 16:9 and centred
 * (letterboxed); on a phone held upright "Otočte telefon na šířku." over the page (v1, no narrow layout).
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var WIDTH = 1920;
  var HEIGHT = 1080;
  var PHONE_MAX_WIDTH = 600; // CSS px: an upright phone; an upright tablet still shows the (small) TV layout
  var ROTATE_TEXT = 'Otočte telefon na šířku.';

  /** {scale, x, y}: the stage scaled to fit `w` × `h`, centred (no strip or bar of its own: T29). */
  function fit(w, h) {
    var scale = Math.min(w / WIDTH, h / HEIGHT);
    if (!(scale > 0)) scale = 1;
    return { scale: scale, x: Math.round((w - WIDTH * scale) / 2) || 0, y: Math.round((h - HEIGHT * scale) / 2) || 0 };
  }

  /** Whether the window is a phone held upright. */
  function rotateNeeded(w, h) {
    return h > w && w <= PHONE_MAX_WIDTH;
  }

  function transform(f) {
    return 'translate(' + f.x + 'px, ' + f.y + 'px) scale(' + f.scale + ')';
  }

  /** Applies the layout to the stage and the rotate overlay (`win` = window); the fit is kept in `last`. */
  function apply(stage, overlay, win) {
    var w = win.innerWidth;
    var h = win.innerHeight;
    var f = CT.WebStage.last = fit(w, h);
    stage.style.transform = transform(f);
    var rotate = rotateNeeded(w, h);
    if (overlay) {
      overlay.textContent = ROTATE_TEXT;
      overlay.classList.toggle('visible', rotate);
    }
    return rotate;
  }

  CT.WebStage = {
    WIDTH: WIDTH,
    HEIGHT: HEIGHT,
    ROTATE_TEXT: ROTATE_TEXT,
    fit: fit,
    last: null,
    rotateNeeded: rotateNeeded,
    transform: transform,
    apply: apply
  };
})(typeof window !== 'undefined' ? window : global);
