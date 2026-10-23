"""On-disk store of the continuous antenna recording (spec/dvb-gateway.md "Continuous recording").

Layout under ``buffer_dir``, per TVHeadend channel uuid::

    <channel>/runs/<run>/init.mp4, run.json    one ffmpeg run (its init segment and track list)
    <channel>/blocks/<YYYYMMDDTHHMM>/          one 10-minute block (UTC start), the unit of deletion
        <run>-<n>.m4s                          fMP4 segments (video + every audio track, muxed)
        index.jsonl                            {"r": run, "n": n, "g": global no., "t": start, "d": duration}
    <channel>/incoming/<run>/                  ffmpeg's working directory (recorder.py moves segments out)

Segments are numbered per channel (``g``) so playlists and media URLs stay stable across runs. Nothing
here knows about programmes: a programme is a time range (archive.py). Local disk only, never exported.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

BLOCK_SECONDS = 600
CHANNEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
RUN_RE = re.compile(r"^[0-9]{6,12}-[0-9a-f]{4}$")
BLOCK_RE = re.compile(r"^\d{8}T\d{4}$")
INDEX = "index.jsonl"
GAP_SECONDS = 1.0  # a hole longer than this between two segments is a gap (discontinuity)


def block_name(start: float) -> str:
    return time.strftime("%Y%m%dT%H%M", time.gmtime(int(start // BLOCK_SECONDS) * BLOCK_SECONDS))


def block_start(name: str) -> int:
    return calendar.timegm(time.strptime(name, "%Y%m%dT%H%M"))


@dataclass(frozen=True)
class Segment:
    channel: str
    run: str
    n: int
    g: int
    start: float
    duration: float
    block: str

    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def file(self) -> str:
        return f"{self.run}-{self.n:06d}.m4s"


@dataclass
class BlockInfo:
    channel: str
    name: str
    start: float
    end: float
    seconds: float
    bytes: int


@dataclass
class _Channel:
    segments: list[Segment] = field(default_factory=list)  # sorted by g (= by time within a run)
    next_g: int = 0
    sizes: dict[str, int] = field(default_factory=dict)  # block → bytes


class BufferStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.Lock()
        self._channels: dict[str, _Channel] = {}
        self._load()

    # -- loading ---------------------------------------------------------------------------
    def _load(self) -> None:
        if not self.root.is_dir():
            return
        for cdir in self.root.iterdir():
            if not (cdir.is_dir() and CHANNEL_RE.match(cdir.name)):
                continue
            chan = _Channel()
            blocks = cdir / "blocks"
            for bdir in sorted(blocks.iterdir()) if blocks.is_dir() else []:
                if not (bdir.is_dir() and BLOCK_RE.match(bdir.name)):
                    continue
                size = 0
                for line in _read_lines(bdir / INDEX):
                    try:
                        item = json.loads(line)
                        seg = Segment(cdir.name, str(item["r"]), int(item["n"]), int(item["g"]),
                                      float(item["t"]), float(item["d"]), bdir.name)
                    except (ValueError, KeyError, TypeError):
                        continue
                    if not RUN_RE.match(seg.run):
                        continue
                    path = bdir / seg.file
                    try:
                        size += path.stat().st_size
                    except OSError:
                        continue  # file gone (partly deleted block)
                    chan.segments.append(seg)
                chan.sizes[bdir.name] = size
            chan.segments.sort(key=lambda s: s.g)
            chan.next_g = chan.segments[-1].g + 1 if chan.segments else 0
            self._channels[cdir.name] = chan
            # ffmpeg working dirs of a previous run are useless after a restart
            shutil.rmtree(cdir / "incoming", ignore_errors=True)

    # -- paths -----------------------------------------------------------------------------
    def channel_dir(self, channel: str) -> Path:
        if not CHANNEL_RE.match(channel):
            raise ValueError(f"bad channel id {channel!r}")
        return self.root / channel

    def run_dir(self, channel: str, run: str) -> Path:
        if not RUN_RE.match(run):
            raise ValueError(f"bad run id {run!r}")
        return self.channel_dir(channel) / "runs" / run

    def work_dir(self, channel: str, run: str) -> Path:
        if not RUN_RE.match(run):
            raise ValueError(f"bad run id {run!r}")
        return self.channel_dir(channel) / "incoming" / run

    def segment_path(self, seg: Segment) -> Path:
        return self.channel_dir(seg.channel) / "blocks" / seg.block / seg.file

    # -- runs ------------------------------------------------------------------------------
    def save_run(self, channel: str, run: str, init: bytes, meta: dict) -> None:
        rdir = self.run_dir(channel, run)
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / "init.mp4").write_bytes(init)
        tmp = rdir / "run.json.tmp"
        tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        tmp.replace(rdir / "run.json")

    def run_meta(self, channel: str, run: str) -> dict:
        try:
            data = json.loads((self.run_dir(channel, run) / "run.json").read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def run_init(self, channel: str, run: str) -> bytes | None:
        try:
            return (self.run_dir(channel, run) / "init.mp4").read_bytes()
        except (OSError, ValueError):
            return None

    # -- writing ---------------------------------------------------------------------------
    def add(self, channel: str, run: str, n: int, start: float, duration: float, source: Path) -> Segment:
        """Move a finished segment from ffmpeg's working dir into its block and index it."""
        block = block_name(start)
        bdir = self.channel_dir(channel) / "blocks" / block
        bdir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            chan = self._channels.setdefault(channel, _Channel())
            seg = Segment(channel, run, n, chan.next_g, start, duration, block)
            chan.next_g += 1
        target = bdir / seg.file
        os.replace(source, target)
        size = target.stat().st_size
        with open(bdir / INDEX, "a", encoding="utf-8") as f:
            f.write(json.dumps({"r": run, "n": n, "g": seg.g, "t": round(start, 3), "d": round(duration, 3)}) + "\n")
        with self._lock:
            chan.segments.append(seg)
            chan.sizes[block] = chan.sizes.get(block, 0) + size
        return seg

    # -- reading ---------------------------------------------------------------------------
    def channels(self) -> list[str]:
        with self._lock:
            return [c for c, ch in self._channels.items() if ch.segments]

    def between(self, channel: str, start: float, end: float) -> list[Segment]:
        """Segments overlapping [start, end), in order."""
        with self._lock:
            chan = self._channels.get(channel)
            return [s for s in chan.segments if s.end > start and s.start < end] if chan else []

    def by_g(self, channel: str, g: int) -> Segment | None:
        with self._lock:
            chan = self._channels.get(channel)
            if not chan or not chan.segments:
                return None
            # g is dense but deletions remove the oldest ones: offset from the first kept segment
            first = chan.segments[0].g
            i = g - first
            if 0 <= i < len(chan.segments) and chan.segments[i].g == g:
                return chan.segments[i]
            return next((s for s in chan.segments if s.g == g), None)

    def latest(self, channel: str, seconds: float) -> list[Segment]:
        """The newest segments covering about ``seconds`` (live window), oldest first."""
        with self._lock:
            chan = self._channels.get(channel)
            if not chan or not chan.segments:
                return []
            out, total = [], 0.0
            for seg in reversed(chan.segments):
                out.append(seg)
                total += seg.duration
                if total >= seconds:
                    break
            return list(reversed(out))

    def newest(self, channel: str) -> Segment | None:
        with self._lock:
            chan = self._channels.get(channel)
            return chan.segments[-1] if chan and chan.segments else None

    def covered(self, channel: str, start: float, end: float) -> float:
        """Seconds of [start, end) that have recorded media."""
        total = 0.0
        for seg in self.between(channel, start, end):
            total += max(0.0, min(seg.end, end) - max(seg.start, start))
        return total

    def blocks(self, channel: str) -> list[BlockInfo]:
        with self._lock:
            chan = self._channels.get(channel)
            if not chan:
                return []
            info: dict[str, BlockInfo] = {}
            for seg in chan.segments:
                b = info.get(seg.block)
                if b is None:
                    info[seg.block] = BlockInfo(channel, seg.block, seg.start, seg.end, seg.duration,
                                                chan.sizes.get(seg.block, 0))
                else:
                    b.start, b.end = min(b.start, seg.start), max(b.end, seg.end)
                    b.seconds += seg.duration
            return sorted(info.values(), key=lambda b: b.name)

    def stats(self, channel: str) -> dict:
        """Hours, how far back, bytes, measured bitrate and gaps of one channel's recording."""
        blocks = self.blocks(channel)
        with self._lock:
            chan = self._channels.get(channel)
            segs = list(chan.segments) if chan else []
        seconds = sum(s.duration for s in segs)
        size = sum(b.bytes for b in blocks)
        gaps, gap_seconds = 0, 0.0
        for a, b in zip(segs, segs[1:]):
            hole = b.start - a.end
            if hole > GAP_SECONDS:
                gaps += 1
                gap_seconds += hole
        return {
            "seconds": round(seconds, 1),
            "bytes": size,
            "oldest": segs[0].start if segs else None,
            "newest": segs[-1].end if segs else None,
            "bytesPerHour": int(size / seconds * 3600) if seconds >= 60 else None,
            "gaps": gaps,
            "gapSeconds": round(gap_seconds, 1),
            "blocks": len(blocks),
        }

    # -- deleting --------------------------------------------------------------------------
    def delete_block(self, channel: str, block: str) -> int:
        """Delete one 10-minute block; returns the bytes freed."""
        if not BLOCK_RE.match(block):
            raise ValueError(f"bad block {block!r}")
        with self._lock:
            chan = self._channels.get(channel)
            if chan:
                chan.segments = [s for s in chan.segments if s.block != block]
                freed = chan.sizes.pop(block, 0)
            else:
                freed = 0
        shutil.rmtree(self.channel_dir(channel) / "blocks" / block, ignore_errors=True)
        return freed

    def delete_channel(self, channel: str) -> int:
        """The whole recording of a channel (deselected or hidden in /admin)."""
        with self._lock:
            chan = self._channels.pop(channel, None)
        freed = sum(chan.sizes.values()) if chan else 0
        shutil.rmtree(self.channel_dir(channel), ignore_errors=True)
        return freed

    def prune_runs(self, channel: str, keep: Iterable[str] = ()) -> None:
        """Remove run directories no segment refers to any more (``keep``: the running one)."""
        with self._lock:
            chan = self._channels.get(channel)
            used = {s.run for s in chan.segments} if chan else set()
        used |= set(keep)
        runs = self.channel_dir(channel) / "runs"
        for rdir in runs.iterdir() if runs.is_dir() else []:
            if rdir.is_dir() and RUN_RE.match(rdir.name) and rdir.name not in used:
                shutil.rmtree(rdir, ignore_errors=True)

    def disk(self) -> tuple[int, int]:
        """(free, total) bytes of the filesystem holding the buffer."""
        self.root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(self.root)
        return usage.free, usage.total


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
