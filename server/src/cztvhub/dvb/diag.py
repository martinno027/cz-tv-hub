"""``cztvhub dvb-diag``: why does Anténa live playback stall? Run it on the Pi while a TV plays a channel.

It follows the same live session as the TV (through the local gateway, so the TV's network is not
part of the measurement) and reports:
- reception: the tuner's signal and error counters (TVHeadend ``status/inputs``) over the run,
- the remux: how regularly segments appear, their lengths vs ``#EXT-X-TARGETDURATION``,
- what that means for a player sitting a given distance behind the live edge (simulated stalls).
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import Config
from .tvheadend import TvheadendClient, TvhError

COUNTERS = ("te", "cc", "unc")  # transport errors, continuity errors, uncorrected blocks
EXTINF_RE = re.compile(r"^#EXTINF:([\d.]+)")
TARGET_RE = re.compile(r"^#EXT-X-TARGETDURATION:(\d+)")


def parse_playlist(text: str) -> tuple[int | None, list[tuple[str, float]]]:
    """(target duration, [(segment uri, duration)])"""
    target, segments, duration = None, [], None
    for line in text.splitlines():
        line = line.strip()
        if m := TARGET_RE.match(line):
            target = int(m.group(1))
        elif m := EXTINF_RE.match(line):
            duration = float(m.group(1))
        elif line and not line.startswith("#") and duration is not None:
            segments.append((line, duration))
            duration = None
    return target, segments


@dataclass
class SegmentLog:
    """Segments in the order they appeared, with the wall-clock time they were first seen."""
    seen: dict[str, float] = field(default_factory=dict)
    arrivals: list[tuple[float, float]] = field(default_factory=list)  # (time, duration)
    targets: list[int] = field(default_factory=list)

    def feed(self, now: float, text: str) -> int:
        target, segments = parse_playlist(text)
        if target is not None and (not self.targets or self.targets[-1] != target):
            self.targets.append(target)
        new = 0
        for uri, duration in segments:
            if uri not in self.seen:
                self.seen[uri] = now
                self.arrivals.append((now, duration))
                new += 1
        return new

    def durations(self) -> list[float]:
        return [d for _, d in self.arrivals]

    def max_gap(self) -> float:
        times = [t for t, _ in self.arrivals]
        return max((b - a for a, b in zip(times, times[1:])), default=0.0)


def simulate_stalls(arrivals: list[tuple[float, float]], hold_back: float) -> tuple[int, float]:
    """A player that starts ``hold_back`` seconds of media behind the live edge and plays in real time:
    (number of stalls, longest stall in s). Media arrives in whole segments at the logged times."""
    if not arrivals:
        return 0, 0.0
    start = arrivals[0][0]
    available, buffered = 0.0, 0.0  # seconds of media that exist / that the player has left
    stalls, longest, clock = 0, 0.0, start
    pending = list(arrivals)
    # wait until hold_back of media exists, then play
    while pending and available < hold_back:
        clock, d = pending.pop(0)
        available += d
    buffered = available
    for t, d in pending:
        elapsed = t - clock
        if elapsed > buffered:  # ran dry before this segment arrived
            stalls += 1
            longest = max(longest, elapsed - buffered)
            buffered = 0.0
        else:
            buffered -= elapsed
        buffered += d
        clock = t
    return stalls, longest


def tuner_counters(entries: list[dict]) -> dict[str, dict[str, Any]]:
    out = {}
    for e in entries:
        name = str(e.get("input") or "?")
        out[name] = {k: e.get(k) for k in ("signal", "signal_scale", "snr", "snr_scale", "ber", "subs", "stream", *COUNTERS)}
    return out


def _level(value: Any, scale: Any) -> str:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return "?"
    if scale == 1:  # relative, 0..65535
        return f"{v * 100 // 65535} %"
    if scale == 2:  # 1/1000 dB(m)
        return f"{v / 1000:.1f} dB"
    return str(v)


def run_diag(cfg: Config, channel: str | None, seconds: int, out: Callable[[str], None] = print,
             sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
             opener: Callable[..., Any] = urllib.request.urlopen) -> int:
    base = f"http://127.0.0.1:{cfg.port}"
    headers = {"Authorization": f"Bearer {cfg.dvb.key}"}

    def get(path: str) -> bytes:
        with opener(urllib.request.Request(base + path, headers=headers), timeout=30) as response:
            return response.read()

    try:
        channels = json.loads(get("/dvb/channels"))["channels"]
    except Exception as exc:  # noqa: BLE001 - any failure here means the gateway is not usable
        out(f"Brána na {base} neodpovídá: {exc}")
        return 1
    pick = next((c for c in channels if channel and c["name"].casefold() == channel.casefold()), None)
    if pick is None:
        if channel:
            out(f"Kanál {channel!r} nenalezen. Kanály: " + ", ".join(c["name"] for c in channels))
            return 1
        pick = channels[0]
    tvh = TvheadendClient(cfg.tvheadend.url, cfg.tvheadend.user, cfg.tvheadend.password)

    def tuners() -> dict[str, dict[str, Any]]:
        try:
            return tuner_counters(tvh.api("status/inputs").get("entries", []))
        except (TvhError, AttributeError):
            return {}

    out(f"Měřím {seconds} s kanál {pick['name']} (stejný přenos jako televize, bez sítě k televizi)…")
    before = tuners()
    log = SegmentLog()
    playlist_times: list[float] = []
    end = clock() + seconds
    next_report = clock() + 10
    while clock() < end:
        t0 = clock()
        try:
            text = get(f"/dvb/live/{pick['id']}.m3u8").decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            out(f"  playlist selhal: {exc}")
            sleep(1)
            continue
        playlist_times.append(clock() - t0)
        log.feed(clock(), text)
        if clock() >= next_report:
            ds = log.durations()
            out(f"  {len(ds)} úseků, poslední {ds[-1]:.1f} s" if ds else "  zatím žádný úsek")
            next_report += 10
        sleep(1)
    after = tuners()

    out("")
    out("== Příjem (tuner) ==")
    if not after:
        out("TVHeadend nevrátil stav tunerů (uživatel cztvhub potřebuje právo Admin).")
    for name, now in after.items():
        old = before.get(name, {})
        errors = []
        for k, label in (("te", "chyby přenosu"), ("cc", "chyby návaznosti"), ("unc", "neopravené bloky")):
            try:
                delta = int(now.get(k) or 0) - int(old.get(k) or 0)
            except (TypeError, ValueError):
                continue
            errors.append(f"{label} +{delta}")
        used = f" (vysílá: {now.get('stream')})" if now.get("subs") else ""
        out(f"{name}{used}: signál {_level(now.get('signal'), now.get('signal_scale'))}, "
            f"SNR {_level(now.get('snr'), now.get('snr_scale'))}; " + ", ".join(errors))

    ds = log.durations()
    out("")
    out("== Převod (úseky) ==")
    if not ds:
        out("Žádné úseky: převod se nerozběhl.")
        return 1
    out(f"{len(ds)} úseků: délka {min(ds):.1f}–{max(ds):.1f} s (průměr {sum(ds) / len(ds):.1f} s), "
        f"TARGETDURATION {log.targets}; nejdelší pauza mezi úseky {log.max_gap():.1f} s; "
        f"playlist odpověděl do {max(playlist_times):.2f} s")
    if len(log.targets) > 1:
        out("POZOR: TARGETDURATION se během přenosu změnil, to přehrávače nesnášejí.")
    target = log.targets[-1] if log.targets else 2
    out("")
    out("== Co by viděl přehrávač ==")
    for hold in (3 * target, 12, 20):
        stalls, longest = simulate_stalls(log.arrivals, hold)
        label = "dnes (3 × délka úseku)" if hold == 3 * target else "s větší zásobou"
        out(f"zásoba {hold:>2} s {label}: zaseknutí {stalls}×" + (f", nejdelší {longest:.1f} s" if stalls else ""))
    return 0
