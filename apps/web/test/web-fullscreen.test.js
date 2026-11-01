'use strict';
// Fullscreen (spec/web.md "Fullscreen"): the whole page through the Fullscreen API, buttons hidden without it.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

const FS = CT.WebFullscreen;

function fakeDoc(opts) {
  const o = opts || {};
  const calls = [];
  const doc = { fullscreenElement: null, calls };
  doc.documentElement = {};
  doc.body = { requestFullscreen() { calls.push('body'); return Promise.resolve(); } }; // never the body (or the video)
  if (o.api !== false) {
    doc.documentElement[o.webkit ? 'webkitRequestFullscreen' : 'requestFullscreen'] = function () {
      calls.push('request');
      doc[o.webkit ? 'webkitFullscreenElement' : 'fullscreenElement'] = doc.documentElement;
      return Promise.resolve();
    };
    doc[o.webkit ? 'webkitExitFullscreen' : 'exitFullscreen'] = function () {
      calls.push('exit');
      doc.fullscreenElement = null;
      doc.webkitFullscreenElement = null;
      return Promise.resolve();
    };
  }
  if (o.enabled === false) doc.fullscreenEnabled = false;
  return doc;
}

function button() {
  const classes = new Set();
  const attrs = {};
  return { classes, attrs, textContent: '', classList: { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)) },
    setAttribute: (k, v) => { attrs[k] = v; } };
}

test('toggle: into fullscreen on the whole page, out again; labels follow', () => {
  const doc = fakeDoc();
  assert.equal(FS.available(doc), true);
  assert.equal(FS.label(doc), 'Celá obrazovka');
  assert.equal(FS.toggle(doc), 'enter');
  assert.equal(doc.fullscreenElement, doc.documentElement); // the page, not the video: the player keeps its bar
  assert.equal(FS.label(doc), 'Ukončit celou obrazovku');
  assert.equal(FS.toggle(doc), 'leave');
  assert.deepEqual(doc.calls, ['request', 'exit']);
});

test('Safari\'s webkit names work too', () => {
  const doc = fakeDoc({ webkit: true });
  assert.equal(FS.toggle(doc), 'enter');
  assert.equal(FS.active(doc), true);
  assert.equal(FS.toggle(doc), 'leave');
});

test('without the API (iPhone) or when disabled the buttons are hidden and nothing is called', () => {
  for (const doc of [fakeDoc({ api: false }), fakeDoc({ enabled: false })]) {
    assert.equal(FS.available(doc), false);
    assert.equal(FS.toggle(doc), null);
    const b = button();
    FS.render(doc, [b]);
    assert.ok(b.classes.has('hidden'));
  }
  const b = button();
  FS.render(fakeDoc(), [b]);
  assert.ok(!b.classes.has('hidden'));
  assert.equal(b.textContent, '⛶');
  assert.equal(b.attrs.title, 'Celá obrazovka');
  assert.equal(b.attrs['aria-label'], 'Celá obrazovka');
});
