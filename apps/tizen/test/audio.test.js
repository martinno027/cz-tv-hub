'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

test('audio tracks: AVPlay info, labels, cycling and OSD text', () => {
  const tracks = CT.AudioTracks.fromAvPlay([
    { type: 'VIDEO', index: 0, extra_info: '{}' },
    { type: 'AUDIO', index: 1, extra_info: '{"language":"cs","fourCC":"mp4a"}' },
    { type: 'AUDIO', index: 2, extra_info: { track_lang: 'eng' } },
    { type: 'AUDIO', index: 3, extra_info: 'not json' },
    null
  ]);
  assert.deepEqual(tracks, [{ index: 1, language: 'cs' }, { index: 2, language: 'eng' }, { index: 3, language: '' }]);
  assert.equal(CT.AudioTracks.label(tracks[0], 0), 'Čeština');
  assert.equal(CT.AudioTracks.label(tracks[1], 1), 'Angličtina');
  assert.equal(CT.AudioTracks.label(tracks[2], 2), 'Zvuk 3');
  assert.equal(CT.AudioTracks.label({ language: 'en-US' }, 0), 'Angličtina');
  assert.equal(CT.AudioTracks.next(tracks, 1), 1);
  assert.equal(CT.AudioTracks.next(tracks, 3), 0);
  assert.equal(CT.AudioTracks.next(tracks, 99), 0);
  assert.equal(CT.AudioTracks.next([], 1), -1);
  assert.equal(CT.AudioTracks.osd(tracks, 1), 'Zvuk: Angličtina (2/3)');
  assert.deepEqual(CT.AudioTracks.fromAvPlay(undefined), []);
});
