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

  /** CSS px of the strip above the stage that holds "‹ Zpět" (its own space: it never covers the TV layout). */
  function stripHeight(h) {
    return h < 500 ? 40 : 52;
  }

  /** {scale, x, y}: the stage scaled to fit `w` × `h` below a strip of `top` px, centred. */
  function fit(w, h, top) {
    var t = top || 0;
    var scale = Math.min(w / WIDTH, (h - t) / HEIGHT);
    if (!(scale > 0)) scale = 1;
    return { scale: scale, x: Math.round((w - WIDTH * scale) / 2), y: t + Math.max(0, Math.round((h - t - HEIGHT * scale) / 2)) };
  }

  /** Whether the window is a phone held upright. */
  function rotateNeeded(w, h) {
    return h > w && w <= PHONE_MAX_WIDTH;
  }

  function transform(f) {
    return 'translate(' + f.x + 'px, ' + f.y + 'px) scale(' + f.scale + ')';
  }

  /** Applies the layout to the stage, the strip with "‹ Zpět" and the rotate overlay (`win` = window). */
  function apply(stage, overlay, win, strip) {
    var w = win.innerWidth;
    var h = win.innerHeight;
    var top = strip ? stripHeight(h) : 0;
    if (strip) strip.style.height = top + 'px';
    stage.style.transform = transform(fit(w, h, top));
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
    stripHeight: stripHeight,
    rotateNeeded: rotateNeeded,
    transform: transform,
    apply: apply
  };
})(typeof window !== 'undefined' ? window : global);
