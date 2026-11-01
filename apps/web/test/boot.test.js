'use strict';
// The loader (boot.js): the Samsung index.html's scripts in their order, the web layer right after the Samsung
// platform defaults, Samsung-only scripts left out; every file it names exists. The web page itself carries no
// inline script or style (the gateway's CSP forbids them) and loads nothing from another origin.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { WEB, APP } = require('./load');
const boot = require(path.join(WEB, 'js', 'boot.js'));

function samsungScripts() {
  const html = fs.readFileSync(path.join(APP, 'index.html'), 'utf8');
  return Array.from(html.matchAll(/<script src="([^"]+)"><\/script>/g), (m) => m[1]);
}

test('plan: Samsung order, web layer after js/ui/platform.js, before the screens', () => {
  const sources = samsungScripts();
  const urls = boot.plan(sources);
  assert.ok(!urls.some((u) => u.includes('$WEBAPIS')));
  assert.ok(!urls.some((u) => u.includes('local-config')));
  assert.ok(urls.includes('js/build-info.js'));
  assert.ok(!urls.includes('app/js/build-info.js'));
  const at = urls.indexOf('app/js/ui/platform.js');
  assert.ok(at > 0);
  assert.deepEqual(urls.slice(at + 1, at + 1 + boot.WEB.length), boot.WEB);
  assert.ok(urls.indexOf('app/js/ui/demo.js') > at + boot.WEB.length); // demo.js reads the fixtures base
  assert.ok(urls.indexOf('app/js/ui/player.js') > at);
  assert.equal(urls[urls.length - 1], 'app/js/ui/app.js');
  // every Samsung script but the two left out, in the same order
  const samsung = urls.filter((u) => u.startsWith('app/')).map((u) => u.slice(4));
  assert.deepEqual(samsung, sources.filter((s) => !/^\$WEBAPIS|local-config|build-info/.test(s)));
});

test('plan: every file exists (Samsung app and web layer)', () => {
  for (const u of boot.plan(samsungScripts())) {
    if (u === 'js/build-info.js') continue; // written by the install
    const file = u.startsWith('app/') ? path.join(APP, u.slice(4)) : path.join(WEB, u);
    assert.ok(fs.existsSync(file), u);
  }
});

test('plan: without the hook the loader refuses (the web layer would come too late)', () => {
  assert.throws(() => boot.plan(['js/core/models.js', 'js/ui/app.js']), /platform\.js/);
});

test('rebase: markup URLs relative to app/index.html', () => {
  assert.equal(boot.rebase('img/tmdb-logo-short.svg'), 'app/img/tmdb-logo-short.svg');
  assert.equal(boot.rebase('/dvb/x'), '/dvb/x');
  assert.equal(boot.rebase('https://x/y'), 'https://x/y');
  assert.equal(boot.rebase('data:image/png;base64,'), 'data:image/png;base64,');
});

test('index.html: no inline script, style or handler; only own files', () => {
  const html = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');
  const scripts = Array.from(html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g));
  assert.equal(scripts.length, 2);
  for (const [, attrs, body] of scripts) {
    assert.equal(body.trim(), '');
    assert.match(attrs, /src="(vendor|js)\//);
  }
  assert.ok(!/<style/i.test(html));
  assert.ok(!/\sstyle=/i.test(html));
  assert.ok(!/\son[a-z]+=/i.test(html));
  assert.ok(!/(src|href)="(https?:)?\/\//i.test(html));
  // the Samsung markup the loader imports has no inline styles or handlers either
  const app = fs.readFileSync(path.join(APP, 'index.html'), 'utf8').replace(/<script[\s\S]*?<\/script>/g, '');
  assert.ok(!/\sstyle=/i.test(app));
  assert.ok(!/\son[a-z]+=/i.test(app));
});
