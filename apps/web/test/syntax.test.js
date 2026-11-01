'use strict';
// The web layer is written like the Samsung app (plain ES2017 browser scripts, no modules, no build step), so the
// shared code and its adapters read the same; the same guard as apps/tizen/test/syntax.test.js.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { WEB } = require('./load');

const TOO_NEW = [
  [/\?\./, 'optional chaining'],
  [/\?\?/, 'nullish coalescing'],
  [/\{\s*\.\.\.|,\s*\.\.\.\w+\s*\}/, 'object spread'],
  [/catch\s*\{/, 'optional catch binding'],
  [/\.(padStart|padEnd|flat|flatMap|matchAll|replaceAll|at)\(/, 'post-ES2017 method'],
  [/^\s*(import|export)\s/m, 'ES module syntax']
];

test('web scripts: plain ES2017', () => {
  const dir = path.join(WEB, 'js');
  const files = fs.readdirSync(dir).filter((f) => f.endsWith('.js'));
  assert.ok(files.length >= 8);
  for (const f of files) {
    const code = fs.readFileSync(path.join(dir, f), 'utf8').replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '').replace(/'(?:\\.|[^'\\\n])*'/g, "''");
    for (const [re, what] of TOO_NEW) assert.ok(!re.test(code), f + ': ' + what);
  }
});
