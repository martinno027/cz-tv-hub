'use strict';
// spec/apps.md "Anténa timeline player" → Keys ← / → (T55): a click = ±10 s (exact); a press held ≥ 0.5 s starts
// rewind / fast-forward at speed 1, clicks while moving change the speed (1–4) or turn round, ⏪/⏩ act at once;
// OK / Back / ▲▼ stop it; the playhead moves every frame, the video stays paused (its picture follows at most once
// a second where the backend can), the stop seeks exactly once; without key-ups repeats lasting 0.5 s are a hold and
// a press ends 0.8 s after its last repeat. The real TimelineController on a fake clock, fake animation frames and a
// fake backend whose seek takes its time.
const test = require('node:test');
const assert = require('node:assert/strict');
const { CT } = require('./load');

global.window = global;

function fakeDocument() {
  const byId = {};
  const make = () => {
    const classes = new Set();
    const el = {
      style: {}, textContent: '', children: [], className: '',
      classList: {
        add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c),
        toggle: (c, on) => { if (on === undefined ? !classes.has(c) : on) classes.add(c); else classes.delete(c); }
      },
      appendChild: (c) => { el.children.push(c); }
    };
    Object.defineProperty(el, 'innerHTML', { set: () => { el.children = []; }, get: () => '' });
    return el;
  };
  return { getElementById: (id) => byId[id] || (byId[id] = make()), createElement: () => make() };
}

global.document = fakeDocument();
require('../app/js/ui/timeline.js');

const T0 = Date.parse('2026-11-03T18:00:00Z');
const HOUR = 3600;

/** Timers and animation frames on a clock the test moves; installed only while `fn` runs. */
async function withClock(fn) {
  const saved = {};
  const names = ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'requestAnimationFrame', 'cancelAnimationFrame'];
  names.forEach((n) => { saved[n] = global[n]; });
  const savedNow = Date.now;
  let now = T0 + 5 * HOUR * 1000;
  let nextId = 1;
  let timers = [];
  let frames = [];
  const clock = {
    now: () => now,
    frameRequests: 0,
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
  global.requestAnimationFrame = (f) => { clock.frameRequests++; frames.push({ id: nextId, fn: f }); return nextId++; };
  global.cancelAnimationFrame = (id) => { frames = frames.filter((f) => f.id !== id); };
  Date.now = () => now;
  try {
    await fn(clock);
  } finally {
    names.forEach((n) => { global[n] = saved[n]; });
    Date.now = savedNow;
  }
}

/**
 * A backend at `position` s, playing, whose seeks land only after `latencyMs` (as AVPlay; meanwhile the old
 * position); `preview` only with opts.preview (the web's fastSeek).
 */
function fakeBackend(clock, opts) {
  const b = {
    pos: opts.position * 1000, seeks: [], previews: [], landAt: null, landTo: null, playing: true, toggles: 0,
    position() {
      if (b.landAt !== null && clock.now() >= b.landAt) { b.pos = b.landTo; b.landAt = null; }
      return b.pos;
    },
    duration: () => (opts.duration || 4 * HOUR) * 1000,
    paused: () => !b.playing,
    togglePause() { b.toggles++; b.playing = !b.playing; },
    pause() { b.playing = false; },
    seekTo(ms) {
      b.seeks.push(ms / 1000);
      b.landAt = clock.now() + (opts.latencyMs || 0);
      b.landTo = ms + (opts.landOffsetMs || 0);
      return ms;
    }
  };
  if (opts.preview) b.preview = (ms) => { b.previews.push({ at: clock.now(), s: ms / 1000 }); return true; };
  return b;
}

function window4h(id, opts) {
  return {
    id, channel: 'ct1', playlist: '/dvb/x.m3u8', complete: opts.complete !== false,
    programme: { start: T0 + HOUR * 1000, end: T0 + 2 * HOUR * 1000 },
    pieces: [{ position: 0, time: opts.start || T0, duration: 4 * HOUR }],
    programmes: [
      { id: 'a', title: 'A', subtitle: '', start: T0, end: T0 + 2 * HOUR * 1000, breaks: false },
      { id: 'b', title: 'B', subtitle: '', start: T0 + 2 * HOUR * 1000, end: T0 + 4 * HOUR * 1000, breaks: false }
    ],
    previous: opts.previous || null, next: opts.next || null
  };
}

function controller(clock, opts) {
  global.document = fakeDocument(); // a fresh page: the bar hidden
  const backend = fakeBackend(clock, opts);
  const player = {
    backend, skipTo: null, osdTimer: null, mute: { muted: false }, loadToken: 1, closed: 0, opened: [],
    request: { title: 'A' },
    setSkip() {}, close() { player.closed++; }, channelName: () => 'ČT1', isOpen: () => true, target: { kind: 'timeline' },
    app: { planner: { timelineWindow: (id, title, time) => {
      player.opened.push({ id, time });
      return opts.neighbour ? Promise.resolve({ timeline: opts.neighbour(id, time) }) : Promise.reject(new Error('no'));
    } } },
    switchTarget(target) { player.tl.setWindow(target.timeline); backend.playing = true; } // a new window plays
  };
  const ctl = new CT.TimelineController(player);
  player.tl = ctl;
  ctl.setWindow(window4h('bct1-1', opts));
  if (opts.keyup !== false) {
    // the first press only shows the bar; its keyup tells the controller this remote sends keyups
    ctl.onKeyDown('enter');
    ctl.onKeyUp('enter');
  } else {
    document.getElementById('osd').classList.add('visible');
  }
  return { ctl, backend, player };
}

/** Seconds of playlist the bar's playhead shows. */
function shown(ctl) {
  return (ctl.playheadTime() - T0) / 1000;
}

function badge() {
  const el = document.getElementById('tl-trick');
  return el.style.display === 'none' ? '' : el.textContent;
}

/** A press of `key` for `ms` with its keyup; repeats every `repeatMs` from 500 ms (0 = none). */
function press(clock, ctl, key, ms, repeatMs, keyup) {
  const start = clock.now();
  let next = start + 500;
  ctl.onKeyDown(key);
  clock.advance(ms, (now) => {
    if (repeatMs && now >= next && now - start < ms) {
      ctl.onKeyDown(key);
      next += repeatMs;
    }
  });
  if (keyup !== false) ctl.onKeyUp(key);
}

const click = (clock, ctl, key) => press(clock, ctl, key, 100, 0);
const near = (a, b, what) => assert.ok(Math.abs(a - b) < 1e-6, (what || '') + ': ' + a + ' ≠ ' + b);

test('a click is ±10 s, exact, nothing moves and the video keeps playing', async () => {
  await withClock((clock) => {
    const { ctl, backend } = controller(clock, { position: 2 * HOUR });
    click(clock, ctl, 'right');
    assert.deepEqual(backend.seeks, [2 * HOUR + 10]);
    assert.equal(ctl.trick, null);
    assert.equal(backend.playing, true);
    press(clock, ctl, 'left', 480, 0); // just under half a second
    assert.deepEqual(backend.seeks, [2 * HOUR + 10, 2 * HOUR]);
    assert.equal(ctl.trick, null);
  });
});

test('held 0.5 s: moves back at speed 1 already while held, smoothly every frame, and keeps moving after release', async () => {
  await withClock((clock) => {
    const { ctl, backend } = controller(clock, { position: 2 * HOUR });
    ctl.onKeyDown('left');
    clock.advance(480);
    assert.equal(ctl.trick, null, 'not before 0.5 s');
    clock.advance(32);
    assert.ok(ctl.trick, 'moving at 0.5 s');
    assert.equal(backend.playing, false, 'the video pauses');
    assert.equal(badge(), '◀◀');
    const seen = [];
    clock.advance(1000, () => seen.push(shown(ctl)));
    for (let i = 1; i < seen.length; i++) assert.ok(seen[i] < seen[i - 1], 'moved in frame ' + i);
    ctl.onKeyUp('left');
    const at = shown(ctl);
    clock.advance(1000);
    near(at - shown(ctl), 60, 'one second at speed 1 after the release');
    assert.deepEqual(backend.seeks, [], 'no seek while moving');
    assert.equal(document.getElementById('osd-state').textContent, '', 'not "Pozastaveno"');
  });
});

test('clicks while moving: same side faster up to 4 with the number, other side slower, from 1 turns round', async () => {
  await withClock((clock) => {
    const { ctl } = controller(clock, { position: 2 * HOUR });
    press(clock, ctl, 'left', 600, 100);
    const rate = () => { const a = shown(ctl); clock.advance(1008); return Math.round((shown(ctl) - a) / 1.008); };
    assert.equal(rate(), -60);
    click(clock, ctl, 'left');
    assert.equal(badge(), '◀◀ 2');
    assert.equal(rate(), -120);
    click(clock, ctl, 'left');
    click(clock, ctl, 'left');
    click(clock, ctl, 'left');
    assert.equal(badge(), '◀◀ 4', '4 is the most');
    assert.equal(rate(), -240);
    click(clock, ctl, 'right');
    assert.equal(badge(), '◀◀ 3');
    click(clock, ctl, 'right');
    click(clock, ctl, 'right');
    assert.equal(badge(), '◀◀');
    click(clock, ctl, 'right');
    assert.equal(badge(), '▶▶', 'turned round');
    assert.equal(rate(), 60);
  });
});

test('a press while moving acts at its keydown; holding it adds nothing', async () => {
  await withClock((clock) => {
    const { ctl } = controller(clock, { position: 2 * HOUR });
    press(clock, ctl, 'right', 600, 100);
    ctl.onKeyDown('right');
    assert.equal(ctl.trick.speed, 2, 'at the keydown');
    clock.advance(1500, () => ctl.onKeyDown('right')); // repeats
    assert.equal(ctl.trick.speed, 2);
    ctl.onKeyUp('right');
    assert.equal(ctl.trick.speed, 2);
  });
});

test('OK stops and plays from the playhead with one exact seek; its keyup and repeats do not pause or open audio', async () => {
  await withClock((clock) => {
    const { ctl, backend } = controller(clock, { position: 2 * HOUR });
    press(clock, ctl, 'left', 600, 100);
    clock.advance(1000);
    const here = shown(ctl);
    ctl.onKeyDown('enter');
    assert.equal(ctl.trick, null);
    assert.equal(backend.seeks.length, 1);
    near(backend.seeks[0], here, 'where the playhead stood');
    assert.equal(backend.playing, true, 'plays');
    clock.advance(900, () => ctl.onKeyDown('enter')); // held on
    ctl.onKeyUp('enter');
    assert.equal(backend.playing, true, 'not paused by the keyup');
    assert.equal(ctl.audioList, null, 'no long-OK audio list');
    assert.equal(badge(), '');
    assert.equal(clock.pendingFrames(), 0, 'frames stop');
  });
});

test('play-pause stops the same way', async () => {
  await withClock((clock) => {
    const { ctl, backend } = controller(clock, { position: 2 * HOUR });
    press(clock, ctl, 'right', 600, 0);
    ctl.onKeyDown('playpause');
    ctl.onKeyUp('playpause');
    assert.equal(ctl.trick, null);
    assert.equal(backend.seeks.length, 1);
    assert.equal(backend.playing, true);
  });
});

test('Back stops and returns to where it started, playing; the player stays open', async () => {
  await withClock((clock) => {
    const { ctl, backend, player } = controller(clock, { position: 2 * HOUR });
    press(clock, ctl, 'right', 600, 100);
    click(clock, ctl, 'right');
    clock.advance(2000);
    ctl.onKeyDown('back');
    ctl.onKeyUp('back');
    assert.deepEqual(backend.seeks, [2 * HOUR]);
    assert.equal(backend.playing, true);
    assert.equal(player.closed, 0);
    ctl.onKeyDown('back'); // stopped now: Back closes as before
    assert.equal(player.closed, 1);
  });
});

test('▲ / ▼ stop and jump; nothing to jump to plays where it stands', async () => {
  await withClock((clock) => {
    const { ctl, backend } = controller(clock, { position: 2 * HOUR + 600 });
    press(clock, ctl, 'right', 600, 0);
    ctl.onKeyDown('up');
    assert.equal(ctl.trick, null);
    assert.deepEqual(backend.seeks, [2 * HOUR], 'start of B');
    assert.equal(backend.playing, true);
  });
  await withClock((clock) => {
    const { ctl, backend } = controller(clock, { position: 2 * HOUR + 600 });
    press(clock, ctl, 'right', 600, 0);
    clock.advance(1000);
    const here = shown(ctl);
    ctl.onKeyDown('down'); // no programme after B
    assert.equal(ctl.trick, null);
    assert.equal(backend.seeks.length, 1);
    near(backend.seeks[0], here, 'played where it stood');
    assert.equal(backend.playing, true);
  });
});

test('reaching the start without a previous window stops there and plays; a growing end stops at the live edge', async () => {
  await withClock((clock) => {
    const { ctl, backend } = controller(clock, { position: 30 });
    press(clock, ctl, 'left', 600, 0); // moving from 0.5 s: 30 s back take 0.5 s at speed 1
    clock.advance(600);
    assert.equal(ctl.trick, null);
    assert.deepEqual(backend.seeks, [0]);
    assert.equal(backend.playing, true);
  });
  await withClock((clock) => {
    const { ctl, backend } = controller(clock, { position: 4 * HOUR - 30, complete: false, duration: 4 * HOUR });
    press(clock, ctl, 'right', 600, 0);
    clock.advance(600);
    assert.equal(ctl.trick, null);
    assert.deepEqual(backend.seeks, [4 * HOUR]);
    assert.equal(backend.playing, true);
  });
});

test('reaching the end with a next window goes on moving there from the same wall clock', async () => {
  await withClock(async (clock) => {
    const { ctl, backend, player } = controller(clock, {
      position: 4 * HOUR - 30, next: 'bct1-2',
      neighbour: (id, time) => window4h(id, { start: T0 + 4 * HOUR * 1000 })
    });
    press(clock, ctl, 'right', 600, 0);
    clock.advance(600);
    assert.equal(player.opened.length, 1);
    assert.equal(player.opened[0].id, 'bct1-2');
    const at = player.opened[0].time;
    assert.ok(at >= T0 + 4 * HOUR * 1000 && at < T0 + 4 * HOUR * 1000 + 1000, 'just past the end');
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(ctl.tl.id, 'bct1-2');
    assert.ok(ctl.trick, 'still moving');
    const from = shown(ctl);
    clock.advance(1008);
    near(shown(ctl) - from, 60.48, 'on at speed 1');
    assert.equal(backend.playing, false, 'the new window is paused again');
    assert.deepEqual(backend.seeks, []);
  });
});

test('a neighbour that does not open stops the movement at the edge (no retry every frame)', async () => {
  await withClock(async (clock) => {
    const { ctl, backend, player } = controller(clock, { position: 4 * HOUR - 30, next: 'bct1-2' });
    press(clock, ctl, 'right', 600, 0);
    clock.advance(600);
    await Promise.resolve();
    await Promise.resolve();
    clock.advance(48); // a few frames: the movement would hit the edge in each
    assert.equal(player.opened.length, 1);
    assert.equal(ctl.trick, null);
    assert.deepEqual(backend.seeks, [4 * HOUR - 1], 'inside this window, before its end');
    assert.equal(backend.playing, true);
  });
});

test('⏪ / ⏩: a press starts at speed 1 at once, then ± 1 like the clicks; repeats add nothing', async () => {
  await withClock((clock) => {
    const { ctl } = controller(clock, { position: 2 * HOUR });
    ctl.onKeyDown('rewind');
    assert.equal(badge(), '◀◀', 'at the keydown');
    clock.advance(1000, () => ctl.onKeyDown('rewind'));
    ctl.onKeyUp('rewind');
    assert.equal(badge(), '◀◀');
    click(clock, ctl, 'rewind');
    assert.equal(badge(), '◀◀ 2');
    click(clock, ctl, 'forward');
    click(clock, ctl, 'forward');
    assert.equal(badge(), '▶▶');
  });
});

test('picture: follows at most once a second where the backend can preview; the exact seek on the stop', async () => {
  await withClock((clock) => {
    const { ctl, backend } = controller(clock, { position: 2 * HOUR, preview: true });
    press(clock, ctl, 'left', 600, 0);
    clock.advance(3500);
    assert.ok(backend.previews.length >= 3 && backend.previews.length <= 4, 'previews ' + backend.previews.length);
    for (let i = 1; i < backend.previews.length; i++) {
      assert.ok(backend.previews[i].at - backend.previews[i - 1].at >= 1000, 'at most once a second');
    }
    ctl.onKeyDown('enter');
    assert.equal(backend.seeks.length, 1);
  });
  await withClock((clock) => {
    const { ctl, backend } = controller(clock, { position: 2 * HOUR }); // AVPlay: no preview
    press(clock, ctl, 'left', 600, 0);
    clock.advance(3500);
    assert.deepEqual(backend.seeks, []);
  });
});

test('after the stop the bar shows the target until the player is there (no jump back)', async () => {
  await withClock((clock) => {
    const { ctl, backend } = controller(clock, { position: 2 * HOUR, latencyMs: 1500, landOffsetMs: 1000 });
    press(clock, ctl, 'left', 600, 0);
    clock.advance(1000);
    ctl.onKeyDown('enter');
    const target = backend.seeks[0];
    clock.advance(1000);
    assert.equal(backend.position() / 1000, 2 * HOUR, 'the player is still at the old position');
    assert.equal(shown(ctl), target, 'the bar stays at the target');
    clock.advance(1000);
    assert.equal(shown(ctl), target + 1, 'the player is there: the bar follows it again');
    assert.equal(ctl.pending, null);
  });
});

test('a seek that never arrives gives the bar back to the player after 10 s', async () => {
  await withClock((clock) => {
    const { ctl, backend } = controller(clock, { position: 2 * HOUR, latencyMs: 60 * 1000 });
    click(clock, ctl, 'left');
    clock.advance(9800);
    assert.equal(shown(ctl), 2 * HOUR - 10);
    clock.advance(200);
    assert.equal(shown(ctl), backend.position() / 1000);
  });
});

test('quick clicks add up while the player is still on its way', async () => {
  await withClock((clock) => {
    const { ctl, backend } = controller(clock, { position: 2 * HOUR, latencyMs: 2000 });
    for (let i = 0; i < 3; i++) {
      click(clock, ctl, 'right');
      clock.advance(100);
    }
    assert.deepEqual(backend.seeks, [2 * HOUR + 10, 2 * HOUR + 20, 2 * HOUR + 30]);
  });
});

test('no keyup: repeats lasting 0.5 s are a hold, a lone keydown a click 0.8 s later', async () => {
  await withClock((clock) => {
    const { ctl, backend } = controller(clock, { position: 2 * HOUR, keyup: false });
    ctl.onKeyDown('right');
    clock.advance(784);
    assert.deepEqual(backend.seeks, [], 'not yet: no keyup comes');
    clock.advance(32);
    assert.deepEqual(backend.seeks, [2 * HOUR + 10], 'the click, 0.8 s after the press');
    assert.equal(ctl.trick, null);
    clock.advance(500);
    // held: the remote repeats every 100 ms from 0.5 s; no timer may start it (a lone keydown would look the same)
    press(clock, ctl, 'left', 1000, 100, false);
    assert.ok(ctl.trick, 'moving');
    assert.equal(badge(), '◀◀');
  });
});

test('no keyup: a new press after 0.8 s of silence is a new click while moving; its repeats are the same press', async () => {
  await withClock((clock) => {
    const { ctl } = controller(clock, { position: 2 * HOUR, keyup: false });
    press(clock, ctl, 'left', 700, 100, false);
    clock.advance(900); // the hold's end noticed
    assert.equal(ctl.press, null);
    ctl.onKeyDown('left');
    assert.equal(badge(), '◀◀ 2');
    clock.advance(300);
    ctl.onKeyDown('left'); // within 0.8 s: a repeat of that press
    assert.equal(badge(), '◀◀ 2');
    clock.advance(900);
    ctl.onKeyDown('left');
    assert.equal(badge(), '◀◀ 3');
  });
});

test('no keyup: a press that ends before 0.5 s of repeats is a click', async () => {
  await withClock((clock) => {
    const { ctl, backend } = controller(clock, { position: 2 * HOUR, keyup: false });
    ctl.onKeyDown('left');
    clock.advance(300);
    ctl.onKeyDown('left'); // a repeat at 0.3 s, then nothing
    clock.advance(1200);
    assert.equal(ctl.trick, null);
    assert.deepEqual(backend.seeks, [2 * HOUR - 10]);
  });
});

/** A controller whose bar is hidden again (as after its 5 s), the remote's key-ups known or not. */
function hiddenBar(clock, opts) {
  const c = controller(clock, opts);
  document.getElementById('osd').classList.remove('visible');
  return c;
}

test('bar hidden: ⏪ / ⏩ show it and act at once; a click of ◀/▶ only shows it; a held ◀/▶ moves', async () => {
  await withClock((clock) => {
    const { ctl } = hiddenBar(clock, { position: 2 * HOUR });
    ctl.onKeyDown('forward');
    assert.equal(document.getElementById('osd').classList.contains('visible'), true);
    assert.equal(badge(), '▶▶', 'moving at the keydown');
  });
  await withClock((clock) => {
    const { ctl, backend } = hiddenBar(clock, { position: 2 * HOUR });
    click(clock, ctl, 'left');
    assert.equal(document.getElementById('osd').classList.contains('visible'), true);
    assert.deepEqual(backend.seeks, [], 'the click only showed the bar');
    click(clock, ctl, 'left');
    assert.deepEqual(backend.seeks, [2 * HOUR - 10], 'the next click seeks');
  });
  await withClock((clock) => {
    const { ctl } = hiddenBar(clock, { position: 2 * HOUR });
    press(clock, ctl, 'left', 600, 100);
    assert.equal(badge(), '◀◀', 'a hold of the first press moves');
  });
  await withClock((clock) => {
    const { ctl, backend } = hiddenBar(clock, { position: 2 * HOUR, keyup: false });
    press(clock, ctl, 'right', 1000, 100, false); // no key-up: repeats lasting 0.5 s
    assert.equal(badge(), '▶▶');
    assert.deepEqual(backend.seeks, []);
  });
});

test('no keyup: the press that showed the bar is that press only; the same key pressed later acts', async () => {
  await withClock((clock) => {
    const { ctl, backend } = hiddenBar(clock, { position: 2 * HOUR + 600, keyup: false });
    ctl.onKeyDown('up'); // shows the bar
    clock.advance(100);
    ctl.onKeyDown('up'); // a repeat of it
    assert.deepEqual(backend.seeks, []);
    clock.advance(900); // 0.8 s after its last keydown: over
    ctl.onKeyDown('up');
    assert.deepEqual(backend.seeks, [2 * HOUR], 'the new press jumps');
  });
});

function crossing(clock, extra) {
  return controller(clock, Object.assign({
    position: 4 * HOUR - 30, next: 'bct1-2',
    // the next window's `previous` is not the start window (crossed more than once, or ids moved on): Back must
    // still open the window where it started
    neighbour: (id) => window4h(id, { start: id === 'bct1-2' ? T0 + 4 * HOUR * 1000 : T0, previous: 'bct1-0b' })
  }, extra || {}));
}

test('moving on in the next window keeps the direction and speed from the mapped position', async () => {
  await withClock(async (clock) => {
    const { ctl } = crossing(clock);
    press(clock, ctl, 'right', 520, 0); // moving from about 0.5 s
    click(clock, ctl, 'right'); // speed 2: 30 s take a quarter second
    clock.advance(300);
    assert.equal(ctl.switching !== null, true, 'opening the next window');
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(ctl.tl.id, 'bct1-2');
    assert.equal(badge(), '▶▶ 2');
    const from = shown(ctl);
    assert.ok(from >= 4 * HOUR && from < 4 * HOUR + 60, 'at the mapped position (the new window starts at 4 h): ' + from);
    clock.advance(1008);
    near(shown(ctl) - from, 120.96, 'speed 2 on');
  });
});

test('OK or Back while the next window opens: the movement ends and the new window plays where it opened', async () => {
  for (const key of ['enter', 'back']) {
    await withClock(async (clock) => {
      const { ctl, backend, player } = crossing(clock);
      press(clock, ctl, 'right', 600, 0);
      clock.advance(600);
      assert.equal(ctl.switching !== null, true, key + ': opening');
      ctl.onKeyDown(key);
      ctl.onKeyUp(key);
      assert.equal(ctl.trick, null, key);
      assert.equal(player.closed, 0, key);
      await Promise.resolve();
      await Promise.resolve();
      assert.equal(ctl.tl.id, 'bct1-2', key);
      clock.advance(500);
      assert.equal(backend.playing, true, key + ': plays (not paused again)');
      assert.deepEqual(backend.seeks, [], key + ': where it opened, no seek of its own');
      assert.equal(player.opened.length, 1, key);
    });
  }
});

test('Back after crossing into the next window opens the start window at the start position', async () => {
  await withClock(async (clock) => {
    const { ctl, player } = crossing(clock);
    press(clock, ctl, 'right', 600, 0);
    clock.advance(600);
    await Promise.resolve();
    await Promise.resolve();
    clock.advance(500);
    assert.equal(ctl.tl.id, 'bct1-2');
    ctl.onKeyDown('back');
    assert.equal(ctl.trick, null);
    assert.deepEqual(player.opened[1], { id: 'bct1-1', time: T0 + (4 * HOUR - 30) * 1000 });
    assert.equal(player.closed, 0);
  });
});

test('web: a drag on the bar while moving shows the dragged moment, and its seek ends the movement and plays', async () => {
  await withClock((clock) => {
    const { ctl, backend } = controller(clock, { position: 2 * HOUR });
    press(clock, ctl, 'left', 600, 0);
    ctl.scrub = HOUR; // web-input.js dragTo while dragging
    assert.equal(shown(ctl), HOUR, 'the drag, not the movement');
    ctl.scrub = null;
    ctl.seekToTime(T0 + HOUR * 1000); // the drag's release
    assert.equal(ctl.trick, null);
    assert.deepEqual(backend.seeks, [HOUR]);
    assert.equal(backend.playing, true);
    assert.equal(clock.pendingFrames(), 0);
  });
});
