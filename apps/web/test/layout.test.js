'use strict';
// T31 layout rules in the shared markup and the web layer: the guide's order (spec/apps.md "Order on the screen"),
// "‹ Zpět" only on the screens where Back means something, the Archiv/episode lines fitting the height.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { CT, APP } = require('./load');

// archive-cards.js is a Samsung screen script (window = the page); its line helper needs no DOM
global.window = global.window || global;
require(path.join(APP, 'js', 'ui', 'archive-cards.js'));

test('guide order in the Samsung markup (the web shares it): tab bar → detail → day strip → grid', () => {
  const html = fs.readFileSync(path.join(APP, 'index.html'), 'utf8');
  const guide = html.slice(html.indexOf('id="screen-guide"'), html.indexOf('</section>', html.indexOf('id="screen-guide"')));
  const at = (id) => guide.indexOf('id="' + id + '"');
  assert.ok(html.indexOf('id="tabs"') < html.indexOf('id="screen-guide"'));
  assert.ok(at('detail') >= 0 && at('days') >= 0 && at('grid') >= 0);
  assert.ok(at('detail') < at('days'), 'detail before the day strip');
  assert.ok(at('days') < at('grid'), 'day strip before the grid');
});

/** A minimal document: the elements adapt() looks for, children kept per parent. */
function fakeDoc() {
  const made = [];
  const element = (id, cls) => {
    const e = { id, className: cls || '', children: [], textContent: '', attrs: {},
      classList: { toggle() {}, add() {}, remove() {} }, setAttribute(k, v) { this.attrs[k] = v; } };
    e.appendChild = (c) => { e.children.push(c); c.parent = e; };
    e.insertBefore = (c) => { e.children.unshift(c); c.parent = e; };
    Object.defineProperty(e, 'firstChild', { get: () => e.children[0] || null });
    return e;
  };
  const byId = {};
  ['top', 'osd', 'programme', 'show', 'screen-live', 'screen-guide', 'screen-archive', 'set-choice', 'ar-side'].forEach((id) => { byId[id] = element(id); });
  const sections = ['set-section-channels', 'set-section-profile', 'set-section-server'].map((id) => {
    const section = element(id, 'set-section');
    section.appendChild(element('', 'page-title')); // the section's header: Zpět goes above its title
    return section;
  });
  const doc = {
    documentElement: {},
    body: element('body'), // the own-window line (T46) goes there
    getElementById: (id) => byId[id] || null,
    querySelectorAll: (sel) => (sel === '.set-section' ? sections : sel === '.web-fullscreen' ? made.filter((m) => /web-fullscreen/.test(m.className)) : []),
    createElement: () => { const e = element(''); made.push(e); return e; }
  };
  return { doc, byId, sections, made };
}

test('"‹ Zpět" only where Back means something: Nastavení sections, the show page, the player bar', () => {
  const { doc, byId, sections, made } = fakeDoc();
  CT.Platform = CT.Platform || {};
  CT.WebPage.adapt(doc);
  const backs = made.filter((m) => m.className === 'web-back-link');
  assert.equal(backs.length, sections.length + 2);
  assert.equal(byId.show.children[0].className, 'web-back-link'); // the Archiv show page has no Zpět button
  assert.ok(!byId.programme.children.some((c) => c.className === 'web-back-link')); // it has its own Zpět button
  sections.forEach((s) => assert.equal(s.children[0].className, 'web-back-link')); // inside the section's header
  const osdRow = byId.osd.children[0];
  assert.equal(osdRow.children[0].className, 'web-back-link'); // inside the player bar
  // never on Živě, the guide, the Archiv or the Nastavení choice, never a strip of its own
  ['screen-live', 'screen-guide', 'screen-archive', 'set-choice', 'top'].forEach((id) => {
    assert.ok(!byId[id].children.some((c) => c.className === 'web-back-link'), id);
  });
  assert.equal(doc.getElementById('web-strip'), null);
});

test('Archiv grid and episodes: as many lines as fit the height, never fewer than the TV\'s', () => {
  const L = CT.ArchiveCards && CT.ArchiveCards.Lines;
  assert.ok(L, 'archive-cards.js loaded');
  assert.equal(L.fitting({ clientHeight: 800 }, 350, 2), 2); // the TV
  assert.equal(L.fitting({ clientHeight: 1200 }, 350, 2), 3); // a taller window
  assert.equal(L.fitting({ clientHeight: 380 }, 76, 5), 5); // the TV's episodes
  assert.equal(L.fitting({ clientHeight: 700 }, 76, 5), 9);
  assert.equal(L.fitting({ clientHeight: 0 }, 76, 5), 5); // before layout
  assert.equal(L.fitting(null, 76, 5), 5);
});

test('show page: the header "‹ Zpět" only when the page has no Zpět button (series yes, film never)', () => {
  const N = CT.WebPage.needsBackLink;
  assert.equal(N([{ id: 'resume' }, { id: 'startOver' }, { id: 'back' }]), false); // film: its own Zpět
  assert.equal(N([]), true); // series: no buttons
  assert.equal(N([{ id: 'play' }]), true);
  assert.equal(N(undefined), true); // still loading
});
