'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { CT } = require('./load');

const T = Date.UTC(2026, 9, 23, 19, 10);
const prog = (title, startMin, endMin, extra) => Object.assign({ title, start: T + startMin * 60000, end: T + endMin * 60000 }, extra || {});

test('live: the programme airing now over the channel', () => {
  const info = { current: prog('Na tělo', -10, 20), next: prog('Všechnopárty', 20, 80) };
  assert.deepStrictEqual(CT.PlayerTitle.live('CT 1 PHA HD T2', info, T), { title: 'Na tělo', subtitle: 'CT 1 PHA HD T2' });
  // the refresh is late: the next programme already started
  assert.deepStrictEqual(CT.PlayerTitle.live('ČT1', info, T + 25 * 60000), { title: 'Všechnopárty', subtitle: 'ČT1' });
  assert.deepStrictEqual(CT.PlayerTitle.live('ČT1', { current: prog('Události', -5, 5, { episodeTitle: 'Díl 3' }) }, T),
    { title: 'Události', subtitle: 'ČT1 · Díl 3' });
  assert.deepStrictEqual(CT.PlayerTitle.live('Nova HD', { current: prog('Zprávy', -5, 5, { subtitle: 'Večerní' }) }, T),
    { title: 'Zprávy', subtitle: 'Nova HD · Večerní' });
});

test('live without guide data: the channel', () => {
  assert.deepStrictEqual(CT.PlayerTitle.live('ČT1', null, T), { title: 'ČT1', subtitle: '' });
  assert.deepStrictEqual(CT.PlayerTitle.live('ČT1', { current: prog('Starý', -60, -1) }, T), { title: 'ČT1', subtitle: '' });
});

test('programmes', () => {
  assert.deepStrictEqual(CT.PlayerTitle.programme('Zprávy', 'Nova HD'), { title: 'Zprávy', subtitle: 'Nova HD' });
  assert.deepStrictEqual(CT.PlayerTitle.programme('', 'Nova HD'), { title: 'Nova HD', subtitle: '' });
});
