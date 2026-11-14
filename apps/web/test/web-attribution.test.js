'use strict';
// The weather attribution on the web (spec/apps.md "Domů", T59): "Open-Meteo.com" is a link to https://open-meteo.com/
// in a new tab, as Open-Meteo's licence asks; the rest stays text. Samsung keeps it plain (home-screen.test.js).
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const { CT, WEB, APP } = require('./load');

function node(tag) {
  return { tagName: tag, className: '', textContent: '', children: [], appendChild(c) { this.children.push(c); return c; } };
}
global.window = global;
global.location = { protocol: 'http:', hostname: '192.168.1.20', port: '8098', host: '192.168.1.20:8098',
  origin: 'http://192.168.1.20:8098', search: '' };
global.document = {
  getElementById: () => null,
  createElement: (t) => node(t.toUpperCase()),
  createTextNode: (t) => Object.assign(node('#text'), { textContent: t })
};
require(path.join(APP, 'js', 'ui', 'platform.js'));
require(path.join(WEB, 'js', 'platform-web.js'));
require(path.join(APP, 'js', 'ui', 'weather.js'));

const TEXT = 'Počasí: Open-Meteo.com (CC BY 4.0), CAMS · Výstrahy: ČHMÚ (CC BY 4.0)';

test('"Open-Meteo.com" is a link opening in a new tab; the line reads the same', () => {
  const line = CT.WeatherView.attribution(TEXT);
  assert.deepEqual(line.children.map((n) => n.tagName), ['#text', 'A', '#text']);
  const a = line.children[1];
  assert.equal(a.textContent, 'Open-Meteo.com');
  assert.equal(a.href, 'https://open-meteo.com/');
  assert.equal(a.target, '_blank');
  assert.equal(a.rel, 'noopener');
  assert.equal(line.children.map((n) => n.textContent).join(''), TEXT);
});

test('an attribution without the name stays plain text', () => {
  const line = CT.WeatherView.attribution('Výstrahy: ČHMÚ (CC BY 4.0)');
  assert.deepEqual(line.children.map((n) => [n.tagName, n.textContent]), [['#text', 'Výstrahy: ČHMÚ (CC BY 4.0)']]);
});
