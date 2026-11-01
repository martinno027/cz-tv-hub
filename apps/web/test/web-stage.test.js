'use strict';
// Layout (spec/web.md "Layout"): the 1920×1080 stage scaled into the window, 16:9 letterboxed; upright phone text.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

const S = CT.WebStage;

test('scale and offsets: the stage fits and is centred', () => {
  assert.deepEqual(S.fit(1920, 1080), { scale: 1, x: 0, y: 0 });
  assert.deepEqual(S.fit(960, 540), { scale: 0.5, x: 0, y: 0 });
  const wide = S.fit(2560, 1080); // ultrawide: bars left and right
  assert.equal(wide.scale, 1);
  assert.equal(wide.x, 320);
  assert.equal(wide.y, 0);
  const tall = S.fit(1280, 1024); // 5:4: bars above and below
  assert.equal(tall.scale, 1280 / 1920);
  assert.equal(tall.x, 0);
  assert.equal(tall.y, 152);
  const phone = S.fit(844, 390); // a phone on its side
  assert.equal(phone.scale, 390 / 1080);
  assert.equal(phone.y, 0);
  assert.equal(phone.x, Math.round((844 - 1920 * 390 / 1080) / 2));
  assert.equal(S.transform(tall), 'translate(0px, 152px) scale(' + 1280 / 1920 + ')');
  assert.equal(S.fit(0, 0).scale, 1); // a window without a size yet does not divide by zero into NaN
});

test('upright phone: "Otočte telefon na šířku."; a phone on its side, a tablet and a desktop window: no', () => {
  assert.equal(S.ROTATE_TEXT, 'Otočte telefon na šířku.');
  assert.equal(S.rotateNeeded(390, 844), true);
  assert.equal(S.rotateNeeded(360, 640), true);
  assert.equal(S.rotateNeeded(844, 390), false);
  assert.equal(S.rotateNeeded(820, 1180), false); // upright iPad: the small TV layout
  assert.equal(S.rotateNeeded(1440, 900), false);
  assert.equal(S.rotateNeeded(500, 500), false);
});

test('apply: stage transform and the overlay', () => {
  const stage = { style: {} };
  const classes = new Set();
  const overlay = { textContent: '', classList: { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)) } };
  assert.equal(S.apply(stage, overlay, { innerWidth: 390, innerHeight: 844 }), true);
  assert.ok(classes.has('visible'));
  assert.equal(overlay.textContent, 'Otočte telefon na šířku.');
  assert.equal(S.apply(stage, overlay, { innerWidth: 1920, innerHeight: 1080 }), false);
  assert.ok(!classes.has('visible'));
  assert.equal(stage.style.transform, 'translate(0px, 0px) scale(1)');
});

test('"‹ Zpět" strip: the stage fits below it, so the button never covers the TV layout', () => {
  assert.equal(S.stripHeight(900), 52);
  assert.equal(S.stripHeight(390), 40); // a phone on its side
  const f = S.fit(1440, 900, 52);
  assert.equal(f.scale, Math.min(1440 / 1920, 848 / 1080));
  assert.ok(f.y >= 52, 'stage starts below the strip: ' + f.y);
  assert.ok(f.y + 1080 * f.scale <= 900 + 0.5);
  const wide = S.fit(1920, 1080, 52); // a 16:9 window: smaller, centred left/right, under the strip
  assert.equal(wide.y, 52);
  assert.equal(wide.x, Math.round((1920 - 1920 * (1028 / 1080)) / 2));
  const strip = { style: {} };
  const stage = { style: {} };
  S.apply(stage, null, { innerWidth: 1440, innerHeight: 900 }, strip);
  assert.equal(strip.style.height, '52px');
  assert.equal(stage.style.transform, S.transform(S.fit(1440, 900, 52)));
});
