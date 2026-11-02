/*
 * Layout on the web (spec/web.md "Layout (T31)"): the app fills the whole browser window, no black borders. The TV
 * layout (1920 px wide) is scaled by the window's width; its height is the window's height at that scale, so the
 * guide grid, the lists and the Archiv grid show more or fewer rows. Too short a stage or a phone held upright:
 * "Otočte telefon na šířku." over the page.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var WIDTH = 1920;
  var HEIGHT = 1080; // the TV's height: the layout's reference, no longer a fixed size on the web
  var MIN_HEIGHT = 600; // TV px: below this the screens do not fit (spec/web.md)
  var PHONE_MAX_WIDTH = 600; // CSS px: an upright phone (its stage would be very tall and tiny)
  var ROTATE_TEXT = 'Otočte telefon na šířku.';

  /** {scale, x, y, height}: the stage scaled to the window's width, as tall as the window (`height` in TV px). */
  function fit(w, h) {
    var scale = w / WIDTH;
    if (!(scale > 0)) scale = 1;
    var height = h > 0 ? h / scale : HEIGHT;
    return { scale: scale, x: 0, y: 0, height: Math.round(height) };
  }

  /** Whether the layout does not fit: a phone held upright, or a stage shorter than MIN_HEIGHT. */
  function rotateNeeded(w, h) {
    if (h > w && w <= PHONE_MAX_WIDTH) return true;
    return w > 0 && h > 0 && fit(w, h).height < MIN_HEIGHT;
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
    stage.style.height = f.height + 'px';
    // what is sized in screen px inside the scaled stage (the T52 loader, web.css) divides by it
    stage.style.setProperty('--web-scale', String(f.scale));
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
    MIN_HEIGHT: MIN_HEIGHT,
    ROTATE_TEXT: ROTATE_TEXT,
    fit: fit,
    last: null,
    rotateNeeded: rotateNeeded,
    transform: transform,
    apply: apply
  };
})(typeof window !== 'undefined' ? window : global);
