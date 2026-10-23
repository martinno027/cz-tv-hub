/*
 * Picking a channel by number (spec/apps.md "Channel numbers and zapping"): digits collect in a buffer shown top
 * right; it is used at once when no further digit could give a valid number, else after 2 s or on OK.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};
  var NOT_FOUND_MS = 2000;

  function box() {
    return document.getElementById('number-entry');
  }

  /** `onNumber(n)` is called with the entered number (1…); the caller checks it against its list. */
  function NumberEntry(onNumber) {
    this.onNumber = onNumber;
    this.buffer = '';
    this.timer = null;
  }

  NumberEntry.prototype.active = function () {
    return this.buffer !== '';
  };

  /** One digit; `count` = how many channels have a number. */
  NumberEntry.prototype.digit = function (digit, count) {
    var self = this;
    var r = CT.ChannelNumbers.type(this.buffer, digit, count);
    this.buffer = r.buffer;
    clearTimeout(this.timer);
    if (!this.buffer) return;
    if (r.commit) {
      this.commit();
      return;
    }
    this.show(this.buffer + '_');
    this.timer = setTimeout(function () { self.commit(); }, CT.ChannelNumbers.ENTRY_TIMEOUT_MS);
  };

  NumberEntry.prototype.commit = function () {
    var number = parseInt(this.buffer, 10);
    this.cancel();
    if (number > 0) this.onNumber(number);
  };

  NumberEntry.prototype.cancel = function () {
    clearTimeout(this.timer);
    this.timer = null;
    this.buffer = '';
    box().classList.remove('visible');
  };

  /** "Kanál <n> není" for a moment; nothing else changes. */
  NumberEntry.prototype.notFound = function (number) {
    var self = this;
    this.show('Kanál ' + number + ' není');
    this.timer = setTimeout(function () { self.cancel(); }, NOT_FOUND_MS);
  };

  NumberEntry.prototype.show = function (text) {
    var b = box();
    b.textContent = text;
    b.classList.add('visible');
  };

  CT.NumberEntry = NumberEntry;
})(window);
