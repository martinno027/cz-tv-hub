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
    65376: 'done', 65385: 'cancel'
  };

  /** Media keys are delivered to the app only after registration. */
  var MEDIA_KEYS = ['MediaPlayPause', 'MediaPlay', 'MediaPause', 'MediaStop', 'MediaRewind', 'MediaFastForward',
    'ChannelUp', 'ChannelDown'];

  CT.Keys = {
    name: function (event) {
      return CODES[event.keyCode] || null;
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
