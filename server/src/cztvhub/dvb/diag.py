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
import struct
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


def run_diag(cfg: Config, channel: str | None, seconds: int, out: Callable[[str], None] = print, frames: bool = False,
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
    init: bytes | None = None
    kept: list[bytes] = []  # --frames: segments in memory only
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
        before_feed = set(log.seen)
        log.feed(clock(), text)
        if frames:
            if init is None and (m := re.search(r'#EXT-X-MAP:URI="([^"]+)"', text)):
                init = get(m.group(1))
            for uri, _ in parse_playlist(text)[1]:
                if uri not in before_feed and len(kept) < MAX_SEGMENTS:
                    try:
                        kept.append(get(uri))
                    except Exception:  # noqa: BLE001 - already deleted from the live window
                        pass
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
    if frames:
        out("")
        out("== Obraz (rozbor úseků, dekóduji…) ==")
        if init is None or not kept:
            out("Žádná data k rozboru.")
        else:
            for line in analyse_segments(init, kept, cfg.dvb.ffmpeg):
                out(line)
    return 0


# -- `--frames`: what the picture itself looks like at segment boundaries -------------------------
HEVC_NAMES = {19: "IDR", 20: "IDR", 21: "CRA", 16: "BLA", 17: "BLA", 18: "BLA", 1: "P/B", 0: "P/B"}
RASL = (8, 9)
MAX_SEGMENTS = 60  # ~2 min of video kept in memory for the analysis, never written to disk


def video_track_id(init: bytes) -> int | None:
    from . import fmp4

    for tb, te in fmp4.find(init, 0, len(init), [b"moov", b"trak"]):
        handler = next((init[b + 8:b + 12] for b, _ in fmp4.find(init, tb, te, [b"mdia", b"hdlr"])), b"")
        if handler == b"vide":
            return next((fmp4._full_box_u32(init, b, 12, 20) for b, _ in fmp4.find(init, tb, te, [b"tkhd"])), None)
    return None


def video_samples(segment: bytes, track: int) -> tuple[int | None, list[tuple[int, int, int]]]:
    """(tfdt, [(offset, size, duration)]) of the video track's samples in a fragment."""
    from . import fmp4

    tfdt, samples = None, []
    for moof_body, moof_end in fmp4.find(segment, 0, len(segment), [b"moof"]):
        moof_start = moof_body - 8
        for tb, te in fmp4.find(segment, moof_body, moof_end, [b"traf"]):
            hd = next(fmp4.find(segment, tb, te, [b"tfhd"]), None)
            if hd is None or struct.unpack(">I", segment[hd[0] + 4:hd[0] + 8])[0] != track:
                continue
            flags = int.from_bytes(segment[hd[0] + 1:hd[0] + 4], "big")
            p = hd[0] + 8 + (8 if flags & 1 else 0) + (4 if flags & 2 else 0)
            default_duration = struct.unpack(">I", segment[p:p + 4])[0] if flags & 8 else 0
            p += 4 if flags & 8 else 0
            default_size = struct.unpack(">I", segment[p:p + 4])[0] if flags & 0x10 else 0
            for db, _ in fmp4.find(segment, tb, te, [b"tfdt"]):
                fmt = ">Q" if segment[db] == 1 else ">I"
                tfdt = struct.unpack(fmt, segment[db + 4:db + 4 + struct.calcsize(fmt)])[0]
            for rb, _ in fmp4.find(segment, tb, te, [b"trun"]):
                rflags = int.from_bytes(segment[rb + 1:rb + 4], "big")
                count = struct.unpack(">I", segment[rb + 4:rb + 8])[0]
                q = rb + 8
                offset = moof_start
                if rflags & 1:
                    offset += struct.unpack(">i", segment[q:q + 4])[0]
                    q += 4
                if rflags & 4:
                    q += 4
                for _ in range(count):
                    duration, size = default_duration, default_size
                    if rflags & 0x100:
                        duration = struct.unpack(">I", segment[q:q + 4])[0]
                        q += 4
                    if rflags & 0x200:
                        size = struct.unpack(">I", segment[q:q + 4])[0]
                        q += 4
                    q += (4 if rflags & 0x400 else 0) + (4 if rflags & 0x800 else 0)
                    samples.append((offset, size, duration))
                    offset += size
    return tfdt, samples


def first_picture_type(sample: bytes) -> int | None:
    """NAL unit type of the first slice in a length-prefixed (4 bytes) HEVC sample."""
    p = 0
    while p + 5 <= len(sample):
        length = struct.unpack(">I", sample[p:p + 4])[0]
        if length <= 0:
            return None
        nal = (sample[p + 4] >> 1) & 0x3F
        if nal < 32:
            return nal
        p += 4 + length
    return None


def analyse_segments(init: bytes, segments: list[bytes], ffmpeg: str = "ffmpeg",
                     run: Callable[..., Any] | None = None) -> list[str]:
    import subprocess

    run = run or subprocess.run
    lines: list[str] = []
    track = video_track_id(init)
    if track is None:
        return ["Úseky nemají obrazovou stopu."]
    starts: dict[str, int] = {}
    rasl_after_cra = 0
    gaps, last_end = 0, None
    for seg in segments:
        tfdt, samples = video_samples(seg, track)
        if not samples:
            continue
        types = [first_picture_type(seg[o:o + s]) for o, s, _ in samples]
        name = HEVC_NAMES.get(types[0], f"typ {types[0]}") if types[0] is not None else "?"
        starts[name] = starts.get(name, 0) + 1
        if types[0] == 21:
            rasl_after_cra += sum(1 for t in types[1:8] if t in RASL)
        if tfdt is not None:
            if last_end is not None and abs(tfdt - last_end) > 1:
                gaps += 1
            last_end = tfdt + sum(d for _, _, d in samples)
    lines.append("Začátky úseků: " + ", ".join(f"{k} {v}×" for k, v in sorted(starts.items())))
    if rasl_after_cra:
        lines.append(f"Otevřený GOP: za CRA na začátku úseku následuje {rasl_after_cra} snímků RASL, které odkazují "
                     "do předchozího úseku. Přehrávač, který na začátku úseku dekodér resetuje, je vykreslí s artefakty.")
    lines.append(f"Díry/překryvy v časech obrazu mezi úseky: {gaps}")

    def errors(data: bytes) -> int:
        try:
            result = run([ffmpeg, "-hide_banner", "-nostats", "-v", "error", "-i", "pipe:0", "-map", "0:v:0",
                          "-f", "null", "-"], input=data, capture_output=True, timeout=600)
        except (OSError, subprocess.TimeoutExpired):
            return -1
        return len([l for l in result.stderr.decode("utf-8", "replace").splitlines() if l.strip()])

    try:
        probe = run(["ffprobe" if ffmpeg == "ffmpeg" else ffmpeg.replace("ffmpeg", "ffprobe"), "-v", "error",
                     "-select_streams", "v:0", "-show_entries",
                     "stream=codec_name,profile,width,height,field_order,r_frame_rate,avg_frame_rate",
                     "-of", "default=nw=1", "pipe:0"], input=init + segments[0], capture_output=True, timeout=60)
        info = dict(l.split("=", 1) for l in probe.stdout.decode("utf-8", "replace").splitlines() if "=" in l)
        field = info.get("field_order", "?")
        lines.append(f"Obraz: {info.get('codec_name', '?')} {info.get('profile', '')} {info.get('width', '?')}×"
                     f"{info.get('height', '?')}, snímky {info.get('r_frame_rate', '?')}, prokládání: {field}")
        if field not in ("progressive", "unknown", "?"):
            lines.append("Prokládané video: přehrávače v televizích ho u HEVC často neprokládají správně "
                         "(zubaté hrany, trojúhelníky při pohybu).")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    lines.append(f"Chyby dekodéru při plynulém přehrávání: {errors(init + b''.join(segments))}")
    single = [errors(init + seg) for seg in segments[:12]]
    lines.append(f"Chyby dekodéru, když se úsek dekóduje samostatně (prvních {len(single)}): "
                 + ", ".join(str(n) for n in single))
    return lines
