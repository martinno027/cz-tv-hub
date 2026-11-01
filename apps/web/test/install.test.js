'use strict';
// Own window (spec/web.md "Install as an app", T35): index.html links the manifest and Safari's tags, the manifest
// has what Chrome/Edge need for "Otevřít jako okno", every icon exists with the size it claims, the colours are the
// app's dark theme, and the hint in Nastavení → Server is the spec's text.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { CT, WEB, APP } = require('./load');

const SPEC = path.join(WEB, '..', '..', 'spec', 'web.md');
const html = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');
const head = html.slice(0, html.indexOf('</head>'));
const manifest = JSON.parse(fs.readFileSync(path.join(WEB, 'manifest.webmanifest'), 'utf8'));

/** Width and height from a PNG's IHDR (the first chunk); throws on anything that is not a PNG. */
function pngSize(file) {
  const data = fs.readFileSync(file);
  assert.equal(data.subarray(0, 8).toString('hex'), '89504e470d0a1a0a', file + ': not a PNG');
  assert.equal(data.subarray(12, 16).toString('latin1'), 'IHDR', file);
  return data.readUInt32BE(16) + 'x' + data.readUInt32BE(20);
}

function attr(tag, name) {
  const m = new RegExp('\\s' + name + '="([^"]*)"').exec(tag);
  return m ? m[1] : null;
}

function tags(kind, key, value) {
  return Array.from(head.matchAll(new RegExp('<' + kind + '\\s[^>]*>', 'g')), (m) => m[0])
    .filter((t) => attr(t, key) === value);
}

test('index.html: the manifest, Safari\'s own-window tags, the touch icon and the theme colour', () => {
  const links = tags('link', 'rel', 'manifest');
  assert.equal(links.length, 1);
  assert.equal(attr(links[0], 'href'), 'manifest.webmanifest');
  assert.equal(attr(tags('meta', 'name', 'apple-mobile-web-app-capable')[0], 'content'), 'yes');
  assert.equal(attr(tags('meta', 'name', 'apple-mobile-web-app-title')[0], 'content'), manifest.short_name);
  assert.equal(attr(tags('meta', 'name', 'theme-color')[0], 'content'), manifest.theme_color);
  const touch = tags('link', 'rel', 'apple-touch-icon');
  assert.equal(touch.length, 1);
  assert.equal(pngSize(path.join(WEB, attr(touch[0], 'href'))), '180x180');
  for (const icon of tags('link', 'rel', 'icon')) {
    assert.equal(pngSize(path.join(WEB, attr(icon, 'href'))), attr(icon, 'sizes'));
  }
  // relative URLs: the page is /web/, the gateway serves only under it
  for (const t of head.match(/<link\s[^>]*>/g)) assert.ok(!/href="(\/|https?:)/.test(t), t);
});

test('manifest: name, start in /web/, own window, dark theme', () => {
  assert.equal(manifest.name, 'Televize');
  assert.ok(manifest.short_name && manifest.short_name.length <= 12, 'short_name fits under a launcher icon');
  assert.equal(manifest.start_url, '/web/');
  assert.equal(manifest.scope, '/web/');
  assert.ok(manifest.start_url.startsWith(manifest.scope));
  assert.equal(manifest.display, 'standalone');
  assert.equal(manifest.lang, 'cs');
  // the app's dark theme: --bg of the Samsung stylesheet the web uses
  const bg = /--bg:\s*(#[0-9a-f]{6})/i.exec(fs.readFileSync(path.join(APP, 'css', 'app.css'), 'utf8'))[1];
  assert.equal(manifest.background_color, bg);
  assert.equal(manifest.theme_color, bg);
});

test('manifest: icons 192 and 512 plus a maskable one, each file a PNG of the size it claims', () => {
  const any = manifest.icons.filter((i) => (i.purpose || 'any').split(' ').includes('any'));
  const maskable = manifest.icons.filter((i) => (i.purpose || '').split(' ').includes('maskable'));
  assert.deepEqual(any.map((i) => i.sizes).sort(), ['192x192', '512x512']);
  assert.ok(maskable.length >= 1);
  for (const icon of manifest.icons) {
    assert.equal(icon.type, 'image/png', icon.src);
    assert.ok(!/^(\/|https?:)/.test(icon.src), icon.src + ': relative to the manifest');
    assert.equal(pngSize(path.join(WEB, icon.src)), icon.sizes, icon.src);
  }
  // the maskable one is a different picture (artwork shrunk into the safe zone), not a copy of the plain icon
  const plain = fs.readFileSync(path.join(WEB, any.find((i) => i.sizes === maskable[0].sizes).src));
  assert.notDeepEqual(fs.readFileSync(path.join(WEB, maskable[0].src)), plain);
});

test('manifest: no service worker or offline parts (nothing to cache, video never cached)', () => {
  const text = JSON.stringify(manifest);
  assert.ok(!/serviceworker|service_worker/i.test(text));
  assert.ok(!/serviceWorker/.test(fs.readFileSync(path.join(WEB, 'js', 'boot.js'), 'utf8')));
});

test('Nastavení → Server: the hint is the spec\'s text', () => {
  global.window = global.window || global;
  if (!CT.Web) {
    global.document = global.document || { getElementById: () => null };
    require(path.join(APP, 'js', 'ui', 'platform.js'));
    require(path.join(WEB, 'js', 'platform-web.js'));
  }
  const spec = fs.readFileSync(SPEC, 'utf8').replace(/\s*\n\s*/g, ' ');
  assert.equal(CT.Web.INSTALL_HINT, 'Vlastní okno bez lišty: v Chrome ⋮ → Uložit a sdílet → Vytvořit zástupce → ' +
    'Otevřít jako okno; v Safari Soubor → Přidat do Docku.');
  assert.ok(spec.includes('("' + CT.Web.INSTALL_HINT + '")'), 'spec/web.md quotes the same text');
});
