'use strict';
// Layout (spec/web.md "Layout (T31)"): the whole window, scaled by width, as tall as the window; no letterbox.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

const S = CT.WebStage;

test('scaled by the window\'s width, as tall as the window (TV px), never offset (no black borders)', () => {
  assert.deepEqual(S.fit(1920, 1080), { scale: 1, x: 0, y: 0, height: 1080 });
  assert.deepEqual(S.fit(2000, 1200), { scale: 2000 / 1920, x: 0, y: 0, height: 1152 }); // the owner's window
  assert.deepEqual(S.fit(1440, 900), { scale: 0.75, x: 0, y: 0, height: 1200 }); // a 16:10 laptop: more rows
  assert.deepEqual(S.fit(2560, 1080), { scale: 2560 / 1920, x: 0, y: 0, height: 810 }); // ultrawide: fewer rows
  const phone = S.fit(844, 390); // a phone on its side
  assert.equal(phone.scale, 844 / 1920);
  assert.equal(phone.height, Math.round(390 / (844 / 1920)));
  assert.equal(S.fit(0, 0).scale, 1); // a window without a size yet does not divide by zero into NaN
});

test('"Otočte telefon na šířku.": an upright phone or a stage under 600 TV px; a laptop, a tablet, a phone on its side: no', () => {
  assert.equal(S.ROTATE_TEXT, 'Otočte telefon na šířku.');
  assert.equal(S.MIN_HEIGHT, 600);
  assert.equal(S.rotateNeeded(390, 844), true); // upright phone: its stage would be 4155 TV px tall and tiny
  assert.equal(S.rotateNeeded(2000, 600), true); // 576 TV px
  assert.equal(S.rotateNeeded(2000, 640), false); // 614 TV px
  assert.equal(S.rotateNeeded(844, 390), false); // 887 TV px
  assert.equal(S.rotateNeeded(820, 1180), false); // upright iPad
  assert.equal(S.rotateNeeded(1440, 900), false);
});

test('apply: stage transform and height, the overlay, the fit kept for the input', () => {
  const stage = { style: {} };
  const classes = new Set();
  const overlay = { textContent: '', classList: { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)) } };
  assert.equal(S.apply(stage, overlay, { innerWidth: 1440, innerHeight: 900 }), false);
  assert.equal(stage.style.transform, 'translate(0px, 0px) scale(0.75)');
  assert.equal(stage.style.height, '1200px');
  assert.deepEqual(S.last, S.fit(1440, 900));
  assert.ok(!classes.has('visible'));
  S.apply(stage, overlay, { innerWidth: 390, innerHeight: 844 });
  assert.ok(classes.has('visible'));
});
