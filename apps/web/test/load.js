'use strict';
// Loads the Samsung core (as its own tests do) and the web layer's modules into the shared global `CT`.
// The web modules are plain browser scripts like the Samsung ones; DOM glue is checked in the browser, not here.
const path = require('path');

const WEB = path.join(__dirname, '..');
const APP = path.join(WEB, '..', 'tizen', 'app');
const samsung = require(path.join(WEB, '..', 'tizen', 'test', 'load.js'));

['web-ct', 'web-player', 'web-focus', 'web-scroll', 'web-history', 'web-fullscreen', 'web-dnd', 'web-input', 'web-stage', 'web-gateway', 'web-demo', 'web-page'].forEach((name) => {
  require(path.join(WEB, 'js', name + '.js'));
});

module.exports = { CT: samsung.CT, json: samsung.json, response: samsung.response, WEB, APP };
