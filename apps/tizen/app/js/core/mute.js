/*
 * Mute (spec/apps.md "Mute (T34)"): one muted/unmuted state for the app session. Every player that opens takes it
 * over (zapping, the next programme); it is not stored and not in profiles.
 *
 * AVPlay has no mute of its own, so on the Samsung the player mutes the TV's audio (tizen.tvaudiocontrol) while it is
 * open and gives the TV its previous mute back when it closes or the app goes to the background (TvAudio). The TV's
 * own MUTE key changes the same TV mute; the player notices that (`outside`) and follows it. A <video> (desktop
 * browser, the web app) is muted on the element itself (ElementAudio).
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  /** The app session's state; `output` = TvAudio | ElementAudio | null of the player open now. */
  function Session() {
    this.muted = false;
    this.output = null;
  }

  /** A player opened (or came back from the background) with `output`: it gets the session's state. */
  Session.prototype.attach = function (output) {
    if (this.output && this.output !== output) this.output.release();
    this.output = output || null;
    return this.apply();
  };

  /** Puts the state on the output (`asked` = the viewer pressed mute); false = the output refused. */
  Session.prototype.apply = function (asked) {
    return this.output ? this.output.apply(this.muted, !!asked) !== false : true;
  };

  /** The mute button / key: the other state; a refusing output keeps the old one. Returns the state now. */
  Session.prototype.toggle = function () {
    this.muted = !this.muted;
    if (!this.apply(true)) this.muted = !this.muted;
    return this.muted;
  };

  /** Called every second while playing: true when the state changed outside the app (the TV's MUTE key). */
  Session.prototype.poll = function () {
    var seen = this.output && this.output.outside ? this.output.outside() : null;
    if (seen === null || seen === undefined || seen === this.muted) return false;
    this.muted = seen;
    return true;
  };

  /** The player closed or went to the background: the output gives back what it changed. The state stays. */
  Session.prototype.detach = function () {
    if (this.output) this.output.release();
    this.output = null;
  };

  /**
   * The TV's mute (`control` = tizen.tvaudiocontrol: isMute(), setMute(bool)), changed only while a player is open:
   * the first apply remembers the TV's own state, release() restores it if the app changed anything.
   */
  function TvAudio(control) {
    this.control = control;
    this.saved = null; // the TV's mute before the player changed it; null = not touched
    this.expected = null; // what the TV's mute should be now (to notice the TV's own MUTE key)
  }

  TvAudio.prototype.read = function () {
    try {
      return !!this.control.isMute();
    } catch (e) {
      return null;
    }
  };

  /** `asked` = the viewer pressed mute; a player opening unmuted leaves a TV muted before the app as it is. */
  TvAudio.prototype.apply = function (muted, asked) {
    var now = this.read();
    if (now === null) return false;
    if (now === muted) {
      this.expected = muted;
      return true;
    }
    if (!muted && !asked && this.saved === null) {
      this.expected = now; // the TV was muted before the app: not the app's to undo
      return true;
    }
    try {
      this.control.setMute(muted);
    } catch (e) {
      return false; // e.g. the privilege is missing on this firmware
    }
    if (this.saved === null) this.saved = now;
    this.expected = muted;
    return true;
  };

  /** The TV's mute when it differs from what the app set (its MUTE key was pressed), else null. */
  TvAudio.prototype.outside = function () {
    var now = this.read();
    if (now === null || this.expected === null || now === this.expected) return null;
    this.expected = now;
    if (this.saved === null) this.saved = !now; // what it was before the key
    return now;
  };

  TvAudio.prototype.release = function () {
    if (this.saved !== null && this.read() !== this.saved) {
      try {
        this.control.setMute(this.saved);
      } catch (e) {
        // nothing more to do: the TV's own MUTE key still works
      }
    }
    this.saved = null;
    this.expected = null;
  };

  /** A <video>'s own `muted` (nothing outside the app can change it, nothing to give back). */
  function ElementAudio(video) {
    this.video = video;
  }

  ElementAudio.prototype.apply = function (muted) {
    this.video.muted = muted;
    return true;
  };

  ElementAudio.prototype.outside = function () {
    return null;
  };

  ElementAudio.prototype.release = function () {};

  var ONE_TRACK = 'Pořad má jen jednu zvukovou stopu';

  /** The button's text for the current state (what pressing it does). */
  function label(muted) {
    return muted ? 'Zapnout zvuk' : 'Vypnout zvuk';
  }

  /**
   * The Anténa timeline's "Zvuk" choice: the broadcast tracks (only when there are several; else the note) and the
   * mute item last. `current` = the selected track's index. Returns {note, items: [{kind: 'track' | 'mute', label,
   * position (in `tracks`), current}], focus}; the focus starts on the current track, with one track on the mute item.
   */
  function choice(tracks, current, muted) {
    var items = [];
    var focus = 0;
    var several = tracks.length >= 2;
    if (several) {
      tracks.forEach(function (t, i) {
        if (t.index === current) focus = i;
        items.push({ kind: 'track', label: CT.AudioTracks.label(t, i), position: i, current: t.index === current });
      });
    }
    items.push({ kind: 'mute', label: label(muted), position: -1, current: false });
    return { note: several ? '' : ONE_TRACK, items: items, focus: several ? focus : items.length - 1 };
  }

  CT.Mute = {
    INDICATOR: '🔇', // 🔇
    ICON_ON: '🔊', // 🔊
    ONE_TRACK: ONE_TRACK,
    label: label,
    choice: choice,
    Session: Session,
    TvAudio: TvAudio,
    ElementAudio: ElementAudio
  };
})(typeof window !== 'undefined' ? window : global);
