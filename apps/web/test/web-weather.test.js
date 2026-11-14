'use strict';
// Weather on the web (spec/apps.md "Location for weather", T59): the browser's location only on HTTPS (a secure
// context), else none (the gateway's household place); Samsung takes whatever the TV has.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const { CT, WEB, APP } = require('./load');

global.window = global;
global.location = { protocol: 'http:', hostname: '192.168.1.20', port: '8098', host: '192.168.1.20:8098',
  origin: 'http://192.168.1.20:8098', search: '' };
global.document = { getElementById: () => null };
const geo = { getCurrentPosition() {} };
Object.defineProperty(global, 'navigator', { value: { geolocation: geo }, configurable: true, writable: true });

require(path.join(APP, 'js', 'ui', 'platform.js'));
const samsung = CT.Platform.geolocation;
require(path.join(WEB, 'js', 'platform-web.js'));

test('Samsung: the TV\'s geolocation as it is', () => {
  assert.equal(samsung(), geo);
});

test('the web: the location only in a secure context (https), never over plain http', () => {
  assert.notEqual(CT.Platform.geolocation, samsung);
  global.isSecureContext = false;
  assert.equal(CT.Platform.geolocation(), null);
  delete global.isSecureContext; // a browser without the flag: not taken as secure
  assert.equal(CT.Platform.geolocation(), null);
  global.isSecureContext = true;
  assert.equal(CT.Platform.geolocation(), geo);
  global.navigator.geolocation = undefined;
  assert.equal(CT.Platform.geolocation(), null);
});
