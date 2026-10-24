'use strict';
// Loads the core scripts the way index.html does (shared global `CT`) and reads the shared fixtures.
const fs = require('fs');
const path = require('path');

const CORE = ['models', 'days', 'decisions', 'api', 'layout', 'dvb', 'timeline', 'schedule', 'planner', 'entries', 'resume', 'channel-prefs', 'thumbs', 'prefetch', 'ct-antenna', 'player-title', 'audio', 'channel-numbers', 'psp', 'profile'];
const FIXTURES = path.join(__dirname, '..', '..', '..', 'spec', 'fixtures');

CORE.forEach((name) => require(path.join(__dirname, '..', 'app', 'js', 'core', name + '.js')));

/** Fixture files wrap responses as {"status": …, "body": …}; returns {status, body: string}. */
function response(name) {
  const json = JSON.parse(fs.readFileSync(path.join(FIXTURES, name), 'utf8'));
  return { status: json.status, body: JSON.stringify(json.body) };
}

function json(name) {
  return JSON.parse(fs.readFileSync(path.join(FIXTURES, name), 'utf8'));
}

module.exports = { CT: global.CT, response, json };
