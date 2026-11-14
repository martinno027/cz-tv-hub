'use strict';
// Fakes for the bar controller tests (the same shape timeline-scrub.test.js uses): a page with the elements the
// bar writes, timers and animation frames on a clock the test moves, a backend whose seeks take their time.

function fakeDocument() {
  const byId = {};
  const make = () => {
    const classes = new Set();
    const el = {
      style: {}, textContent: '', children: [], className: '', attrs: {},
      classList: {
        add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c),
        toggle: (c, on) => { if (on === undefined ? !classes.has(c) : on) classes.add(c); else classes.delete(c); }
      },
      appendChild: (c) => { el.children.push(c); },
      setAttribute: (k, v) => { el.attrs[k] = v; },
      getAttribute: (k) => (k in el.attrs ? el.attrs[k] : null)
    };
    Object.defineProperty(el, 'innerHTML', { set: () => { el.children = []; }, get: () => '' });
    return el;
  };
  return {
    hidden: false,
    getElementById: (id) => byId[id] || (byId[id] = make()),
    createElement: () => make(),
    createTextNode: (text) => ({ textContent: text }),
    addEventListener: () => {}
  };
}

/** Timers and animation frames on a clock the test moves (from `start`); installed only while `fn` runs. */
async function withClock(start, fn) {
  const saved = {};
  const names = ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'requestAnimationFrame', 'cancelAnimationFrame'];
  names.forEach((n) => { saved[n] = global[n]; });
  const savedNow = Date.now;
  let now = start;
  let nextId = 1;
  let timers = [];
  let frames = [];
  const clock = {
    now: () => now,
    /** Moves the clock in 16 ms frames: due timers, then the frames requested before this one. */
    advance(ms, onFrame) {
      const end = now + ms;
      while (now < end) {
        now = Math.min(end, now + 16);
        timers.filter((t) => t.at <= now).sort((a, b) => a.at - b.at).forEach((t) => {
          if (!timers.includes(t)) return;
          if (t.every) t.at += t.every; else timers = timers.filter((x) => x !== t);
          t.fn();
        });
        const due = frames;
        frames = [];
        due.forEach((f) => f.fn(now));
        if (onFrame) onFrame(now);
      }
    },
    pendingFrames: () => frames.length
  };
  global.setTimeout = (f, ms) => { timers.push({ id: nextId, at: now + (ms || 0), fn: f }); return nextId++; };
  global.clearTimeout = (id) => { timers = timers.filter((t) => t.id !== id); };
  global.setInterval = (f, ms) => { timers.push({ id: nextId, at: now + ms, every: ms, fn: f }); return nextId++; };
  global.clearInterval = global.clearTimeout;
  global.requestAnimationFrame = (f) => { frames.push({ id: nextId, fn: f }); return nextId++; };
  global.cancelAnimationFrame = (id) => { frames = frames.filter((f) => f.id !== id); };
  Date.now = () => now;
  try {
    await fn(clock);
  } finally {
    names.forEach((n) => { global[n] = saved[n]; });
    Date.now = savedNow;
  }
}

/** A backend at `position` s, playing, whose seeks land after `latencyMs`; `duration` s (0 = not known yet). */
function fakeBackend(clock, opts) {
  const b = {
    pos: opts.position * 1000, seeks: [], landAt: null, landTo: null, playing: true, toggles: 0,
    durationS: opts.duration === undefined ? 3 * 3600 : opts.duration,
    position() {
      if (b.landAt !== null && clock.now() >= b.landAt) { b.pos = b.landTo; b.landAt = null; }
      return b.pos;
    },
    duration: () => b.durationS * 1000,
    paused: () => !b.playing,
    togglePause() { b.toggles++; b.playing = !b.playing; },
    pause() { b.playing = false; },
    seekTo(ms) {
      b.seeks.push(ms / 1000);
      b.landAt = clock.now() + (opts.latencyMs || 0);
      b.landTo = ms;
      return ms;
    },
    audioTracks: () => opts.audio || [{ index: 1, language: 'cs' }],
    currentAudio: () => 1,
    selectAudio: () => true
  };
  if (opts.subtitles) {
    b.shownSubtitle = -1;
    b.subtitleTracks = () => opts.subtitles;
    b.currentSubtitle = () => b.shownSubtitle;
    b.selectSubtitle = (i) => { b.shownSubtitle = i; return true; };
  }
  return b;
}

/** Lets promise callbacks run (fake timers do not touch the microtask queue). */
async function flush() {
  for (let i = 0; i < 5; i++) await Promise.resolve();
}

module.exports = { fakeDocument, withClock, fakeBackend, flush };
