'use strict';
// The app runs on Tizen 4.0 (Chromium 56): no ES modules and nothing newer than ES2017.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const APP = path.join(__dirname, '..', 'app');
const TOO_NEW = [
  [/\?\./, 'optional chaining'],
  [/\?\?/, 'nullish coalescing'],
  [/\{\s*\.\.\.|,\s*\.\.\.\w+\s*\}/, 'object spread'],
  [/catch\s*\{/, 'optional catch binding'],
  [/\.(padStart|padEnd|flat|flatMap|matchAll|replaceAll|at)\(/, 'post-ES2017 method'],
  [/\bglobalThis\b|\bObject\.fromEntries\b|\bPromise\.(allSettled|any)\b/, 'post-ES2017 global'],
  [/^\s*(import|export)\s/m, 'ES module syntax'],
  [/\basync\s+function\s*\*|for\s+await\b/, 'async iteration'],
  [/#\w+\s*[=;(]/, 'private class field']
];

function scripts(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((d) => {
    const full = path.join(dir, d.name);
    return d.isDirectory() ? scripts(full) : d.name.endsWith('.js') ? [full] : [];
  });
}

test('app scripts stay within what Chromium 56 understands', () => {
  const files = scripts(path.join(APP, 'js'));
  assert.ok(files.length >= 9);
  for (const file of files) {
    const code = fs.readFileSync(file, 'utf8').replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '').replace(/'(?:\\.|[^'\\\n])*'/g, "''");
    for (const [re, what] of TOO_NEW) assert.ok(!re.test(code), `${path.relative(APP, file)}: ${what}`);
  }
});
