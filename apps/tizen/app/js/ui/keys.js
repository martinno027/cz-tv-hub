/* Remote control keys: Samsung key codes plus desktop equivalents for development in a browser. */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var CODES = {
    37: 'left', 38: 'up', 39: 'right', 40: 'down', 13: 'enter',
    10009: 'back', 8: 'back', 27: 'back',
    10252: 'playpause', 415: 'play', 19: 'pause', 413: 'stop',
    412: 'rewind', 417: 'forward',
    427: 'chup', 428: 'chdown', 33: 'chup', 34: 'chdown',
    65376: 'done', 65385: 'cancel',
    10195: 'audio', 404: 'audio' // AUDIO/MTS and the green key: next audio track
  };
  // Digits (remote and keyboard row, keyboard numpad): 'd0' … 'd9'.
  for (var d = 0; d <= 9; d++) {
    CODES[48 + d] = 'd' + d;
    CODES[96 + d] = 'd' + d;
  }

  /** Media keys are delivered to the app only after registration. */
  var MEDIA_KEYS = ['MediaPlayPause', 'MediaPlay', 'MediaPause', 'MediaStop', 'MediaRewind', 'MediaFastForward',
    'ChannelUp', 'ChannelDown', 'MTS', 'ColorF1Green', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9'];

  CT.Keys = {
    name: function (event) {
      return CODES[event.keyCode] || null;
    },

    /** 0–9 for a digit key name ('d7' → 7), else -1. */
    digit: function (name) {
      return name && name.length === 2 && name.charAt(0) === 'd' ? parseInt(name.charAt(1), 10) : -1;
    },

    register: function () {
      if (!root.tizen || !root.tizen.tvinputdevice) return;
      MEDIA_KEYS.forEach(function (key) {
        try {
          root.tizen.tvinputdevice.registerKey(key);
        } catch (e) {
          // Not every model has every key.
        }
      });
    },

    exitApp: function () {
      try {
        root.tizen.application.getCurrentApplication().exit();
      } catch (e) {
        // Desktop browser: nothing to exit.
      }
    }
  };
})(window);
