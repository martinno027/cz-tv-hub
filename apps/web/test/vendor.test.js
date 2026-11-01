'use strict';
// hls.js is vendored on purpose (spec/web.md): the pinned version and SHA-256 in README.md match the file, its
// licence is next to it, and "O aplikaci" names the same version.
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { WEB } = require('./load');

const readme = fs.readFileSync(path.join(WEB, 'README.md'), 'utf8');
const lib = fs.readFileSync(path.join(WEB, 'vendor', 'hls.min.js'));

test('hls.js: README pins version, source and SHA-256 of vendor/hls.min.js', () => {
  const sha = crypto.createHash('sha256').update(lib).digest('hex');
  assert.ok(readme.includes(sha), 'README has ' + sha);
  const version = /hls\.js (\d+\.\d+\.\d+)/.exec(readme)[1];
  assert.ok(readme.includes('https://registry.npmjs.org/hls.js/-/hls.js-' + version + '.tgz'));
  assert.ok(lib.toString('utf8').includes('"' + version + '"'), 'the file is ' + version);
  const platform = fs.readFileSync(path.join(WEB, 'js', 'platform-web.js'), 'utf8');
  assert.ok(platform.includes("version: '" + version + "'"));
  assert.ok(readme.includes('Third-party: hls.js ' + version + ' (Apache-2.0)'));
});

test('hls.js: the licence is shipped with it', () => {
  const licence = fs.readFileSync(path.join(WEB, 'vendor', 'LICENSE'), 'utf8');
  assert.match(licence, /Licensed under the Apache License, Version 2\.0/);
  assert.match(licence, /Dailymotion/);
});
