/**
 * The two lines of the player's info bar (spec/apps.md "Title in the player's info bar"):
 * the programme over the channel; live follows the guide data and changes with the next programme.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  function join(channel, episode) {
    return [channel, episode].filter(function (s) { return s && String(s).trim(); }).join(' · ');
  }

  CT.PlayerTitle = {
    /** `info` = {current, next} from onAir (may be up to a refresh old); `now` in ms. */
    live: function (channelName, info, now) {
      var programme = null;
      if (info) {
        if (info.current && info.current.start <= now && now < info.current.end) programme = info.current;
        else if (info.next && info.next.start <= now && now < info.next.end) programme = info.next;
      }
      if (!programme || !String(programme.title || '').trim()) return { title: channelName, subtitle: '' };
      return { title: programme.title, subtitle: join(channelName, programme.episodeTitle || programme.subtitle || '') };
    },

    programme: function (title, channelName, episode) {
      if (!String(title || '').trim()) return { title: channelName, subtitle: '' };
      return { title: title, subtitle: join(channelName, episode || '') };
    }
  };
})(typeof window !== 'undefined' ? window : global);
