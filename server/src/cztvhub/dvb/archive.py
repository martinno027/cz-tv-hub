"""Programmes, live and retention on top of the continuous recording (spec/dvb-gateway.md "Continuous recording").

- A programme is a time range of a recorded channel: ``b<channel>-<start>-<end>`` (epoch seconds of the EPG
  event). Its HLS is built from the stored segments, 30 s before the start to 60 s after the end.
- With one audio track the playlist is the muxed fMP4 as before; with more, a master playlist offers the video
  and each audio track as renditions, cut from the same muxed fragments (fmp4.split_*), so they stay in sync.
- Playlists only ever cover one programme or the live window, and media are served only through such a view:
  there is no listing, no static file serving and no way to fetch "everything" (legal-guard, 2026-10-23).
- Every 60 s: age limit (always), free-space reserve (fair share per channel), hard stop when the reserve
  cannot be reached, and recorders started/stopped to match the channels selected in /admin.
"""
from __future__ import annotations

import math
import re
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from . import fmp4, space
from .buffer import GAP_SECONDS, BufferStore, Segment
from .recorder import RecorderManager
from .thumbs import Settings, Thumbnails

RECORDING_ID_RE = re.compile(r"^b([A-Za-z0-9_]{1,40})-(\d{9,11})-(\d{9,11})$")
VIEW_RE = re.compile(r"^([0-9a-f]{32})/(?:(m|v|a\d{1,2})\.m3u8|(m|v|a\d{1,2})/(init-(\d{6,12}-[0-9a-f]{4})\.mp4|"
                     r"(\d{1,12})\.m4s))$")
PRE_PADDING = 30
POST_PADDING = 60
MAX_PROGRAMME = 12 * 3600
LIVE_WINDOW = 40.0
LIVE_TARGET = 3
LIVE_HEALTH = 20.0
VIEW_IDLE = 4 * 3600
LOOP_SECONDS = 60.0
MIN_COVERED = 30.0
RUN_CACHE = 16
HLS_LANGUAGES = {"ces": "cs", "cze": "cs", "slk": "sk", "slo": "sk", "eng": "en", "deu": "de", "ger": "de",
                 "fra": "fr", "fre": "fr", "pol": "pl", "rus": "ru", "ukr": "uk", "spa": "es", "ita": "it",
                 "hun": "hu"}
M3U8 = "application/vnd.apple.mpegurl"


class ArchiveError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def recording_id(channel: str, start: int, end: int) -> str:
    return f"b{channel}-{int(start)}-{int(end)}"


def parse_recording_id(value: str) -> tuple[str, int, int] | None:
    m = RECORDING_ID_RE.match(value)
    if not m:
        return None
    start, end = int(m.group(2)), int(m.group(3))
    if not 0 < end - start <= MAX_PROGRAMME:
        return None
    return m.group(1), start, end


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(t * 1000) % 1000:03d}Z"


@dataclass
class View:
    vid: str
    channel: str
    start: float | None  # None = live
    end: float | None
    owner: str = ""
    touched: float = field(default_factory=time.monotonic)
    origin_g: int = 0  # live: first segment ever listed (discontinuity counting starts here)
    disc_g: int = 0  # live: discontinuities counted up to this segment
    disc_count: int = 0
    multi: bool | None = None  # master playlist with renditions; decided once, the entry URL never changes type

    @property
    def live(self) -> bool:
        return self.start is None


@dataclass
class RunInfo:
    init: bytes  # neutralised edit lists
    shifts: dict[int, int]
    hevc_track: int | None
    video: int | None
    audio: list[int]  # track ids in file order
    trex: dict[int, tuple[int, int]]
    labels: list[dict]


class Archive:
    def __init__(self, store: BufferStore, recorders: RecorderManager, settings: Settings, thumbs: Thumbnails,
                 default_days: int, now: Callable[[], float] = time.time, log: Callable[[str], None] = print,
                 offered: Callable[[], set[str] | None] | None = None,
                 is_programme: Callable[[str, int, int], bool] | None = None, max_recordings: int = 3,
                 allow_system_disk: bool = False):
        self.store, self.recorders, self.settings, self.thumbs = store, recorders, settings, thumbs
        self.default_days, self.now, self.log = default_days, now, log
        # offered channels (None = TVHeadend not reachable: then nothing is deleted or stopped)
        self.offered = offered or (lambda: None)
        # only real EPG programmes get a playlist (no arbitrary ranges, legal-guard 2026-10-23)
        self.is_programme = is_programme or (lambda channel, start, end: True)
        self.max_recordings = max_recordings
        self.allow_system_disk = allow_system_disk
        self._views: dict[str, View] = {}
        self._view_keys: dict[str, str] = {}
        self._runs: OrderedDict[tuple[str, str], RunInfo] = OrderedDict()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.last_plan: space.Plan | None = None

    # -- settings (/admin) -----------------------------------------------------------------
    def record_channels(self) -> set[str]:
        value = self.settings.get("recordChannels", [])
        return {c for c in value if isinstance(c, str)} if isinstance(value, list) else set()

    def set_record(self, channels: Iterable[str], on: bool) -> list[str]:
        """Switch recording of channels; switching off deletes their recording at once."""
        current = self.record_channels()
        targets = set(channels)
        changed = targets - current if on else targets & current
        if not changed:
            return []
        self.settings.update(recordChannels=sorted(current | changed if on else current - changed))
        freed = 0
        if not on:
            for channel in changed:
                freed += self.forget_channel(channel)
        self.sync()
        done = [f"nahrávání {'zapnuto' if on else 'vypnuto'}: {len(changed)}"]
        if freed:
            done.append(f"smazáno {freed / 1000 ** 3:.1f} GB záznamu")
        return done

    def on_system_disk(self) -> bool:
        """The recording directory is on the same filesystem as / (the SD card): not for recordings."""
        path = self.store.root
        while not path.exists() and path != path.parent:
            path = path.parent
        try:
            return path.stat().st_dev == Path("/").stat().st_dev
        except OSError:
            return True

    def can_record(self) -> str | None:
        """Czech reason why recording cannot be switched on, or None."""
        if self.on_system_disk() and not self.allow_system_disk:
            return ("Nejdřív připojte disk na nahrávky a nastavte na něj buffer_dir (docs/dvb-setup.md, kapitola 2.4); "
                    "na systémovou SD kartu se nenahrává.")
        return None

    def forget_channel(self, channel: str) -> int:
        """Delete a channel's whole recording (recording switched off or channel hidden)."""
        rec = self.recorders.recorder(channel)
        if rec is not None and channel not in (self._wanted() or set()):
            rec.stop(wait=0)
        with self._lock:
            for vid in [v for v, view in self._views.items() if view.channel == channel]:
                del self._views[vid]
        return self.store.delete_channel(channel)

    def max_days(self) -> int:
        value = self.settings.get("maxDays", self.default_days)
        return space.clamp_days(value if isinstance(value, int) else self.default_days)

    def set_max_days(self, days: int) -> int:
        days = space.clamp_days(days)
        self.settings.update(maxDays=days)
        self.enforce()
        return days

    # -- recording state for the guide -----------------------------------------------------
    def is_recorded(self, channel: str) -> bool:
        return channel in self.record_channels() or self.store.newest(channel) is not None

    def state(self, channel: str, start: int, end: int) -> str | None:
        now = self.now()
        recording = channel in self.record_channels()
        if now < start:
            return "scheduled" if recording else None
        covered = self.store.covered(channel, start, min(end, now))
        if now < end:
            rec = self.recorders.recorder(channel)
            return "recording" if covered > 0 and rec is not None and rec.healthy(now) else None
        if covered >= min(MIN_COVERED, 0.5 * (end - start)):
            return "completed"
        return None

    def annotate(self, events: list[dict], channels: set[str]) -> None:
        """Set ``recording`` of guide events of recorded channels from the continuous recording."""
        for event in events:
            if event.get("channel") not in channels:
                continue
            start = int(_epoch(event["start"]))
            end = int(_epoch(event["end"]))
            state = self.state(event["channel"], start, end) if end - start <= MAX_PROGRAMME else None
            event["recording"] = None if state is None else \
                {"id": recording_id(event["channel"], start, end), "state": state}

    # -- views -----------------------------------------------------------------------------
    def _view(self, key: str, make: Callable[[], View]) -> View:
        now = time.monotonic()
        with self._lock:
            for vid in [v for v, view in self._views.items() if now - view.touched > VIEW_IDLE]:
                del self._views[vid]
            vid = self._view_keys.get(key)
            view = self._views.get(vid) if vid else None
            if view is None:
                view = make()
                self._views[view.vid] = view
                self._view_keys[key] = view.vid
            view.touched = now
            return view

    def programme(self, rid: str, owner: str = "") -> tuple[bytes, str]:
        """Playlist (master or media) of a programme; ArchiveError when not available."""
        parsed = parse_recording_id(rid)
        if parsed is None:
            raise ArchiveError(404, "Nahrávka není k dispozici")
        channel, start, end = parsed
        if channel not in (self.offered() or set()) or not self.is_programme(channel, start, end) \
                or self.state(channel, start, end) not in ("completed", "recording"):
            raise ArchiveError(404, "Nahrávka není k dispozici")
        view = self._view(f"p:{owner}:{rid}", lambda: View(secrets.token_hex(16), channel, start, end, owner))
        return self._entry_playlist(view)

    def live_ready(self, channel: str) -> bool:
        rec = self.recorders.recorder(channel)
        return rec is not None and rec.healthy(self.now(), LIVE_HEALTH) and self.store.newest(channel) is not None

    def live(self, channel: str) -> tuple[bytes, str]:
        def make() -> View:
            first = self.store.latest(channel, LIVE_WINDOW)
            g = first[0].g if first else 0
            return View(secrets.token_hex(16), channel, None, None, "", origin_g=g, disc_g=g)
        return self._entry_playlist(self._view(f"live:{channel}", make))

    def _segments(self, view: View) -> list[Segment]:
        if view.live:
            return [s for s in self.store.latest(view.channel, LIVE_WINDOW) if s.g >= view.origin_g]
        return self.store.between(view.channel, view.start - PRE_PADDING, view.end + POST_PADDING)

    def _entry_playlist(self, view: View) -> tuple[bytes, str]:
        segments = self._segments(view)
        if not segments:
            raise ArchiveError(404, "Nahrávka není k dispozici")
        # the run with the most audio tracks decides the renditions (runs without a track leave a gap in it)
        infos = [i for i in (self._run(view.channel, r) for r in dict.fromkeys(s.run for s in segments)) if i]
        if not infos:
            raise ArchiveError(404, "Nahrávka není k dispozici")
        info = max(infos, key=lambda i: len(i.audio))
        if view.multi is None:
            view.multi = len(info.audio) > 1
        if not view.multi:
            return self._media_playlist(view, "m", segments), M3U8
        return self._master(view, info, segments), M3U8

    def _master(self, view: View, info: RunInfo, segments: list[Segment]) -> bytes:
        lines = ["#EXTM3U", "#EXT-X-VERSION:7"]
        default = next((i for i, a in enumerate(info.labels) if a.get("language") in ("ces", "cze")
                        and not a.get("impaired")), 0)
        for i, label in enumerate(info.labels[:len(info.audio)]):
            attrs = [f'TYPE=AUDIO', 'GROUP-ID="aud"', f'NAME="{_quote(label.get("label") or f"Zvuk {i + 1}")}"']
            lang = HLS_LANGUAGES.get(label.get("language", ""))
            if lang:
                attrs.append(f'LANGUAGE="{lang}"')
            attrs.append(f'DEFAULT={"YES" if i == default else "NO"}')
            attrs.append("AUTOSELECT=YES")
            if label.get("impaired"):
                attrs.append('CHARACTERISTICS="public.accessibility.describes-video"')
            attrs.append(f'URI="/dvb/v/{view.vid}/a{i}.m3u8"')
            lines.append("#EXT-X-MEDIA:" + ",".join(attrs))
        lines.append(f'#EXT-X-STREAM-INF:BANDWIDTH={self._bandwidth(segments)},AUDIO="aud"')
        lines.append(f"/dvb/v/{view.vid}/v.m3u8")
        return ("\n".join(lines) + "\n").encode("utf-8")

    def _bandwidth(self, segments: list[Segment]) -> int:
        sample = segments[:20]
        size, seconds = 0, 0.0
        for seg in sample:
            try:
                size += self.store.segment_path(seg).stat().st_size
                seconds += seg.duration
            except OSError:
                continue
        return int(size * 8 / seconds * 1.2) if seconds > 0 else 8_000_000

    def _media_playlist(self, view: View, role: str, segments: list[Segment]) -> bytes:
        track = None
        if role.startswith("a"):
            track = int(role[1:])
        listed: list[Segment] = []
        for seg in segments:
            if track is not None:
                info = self._run(view.channel, seg.run)
                if info is None or track >= len(info.audio):
                    continue
            listed.append(seg)
        complete = not view.live and self.now() >= (view.end or 0) + POST_PADDING
        if complete:
            target = max([1] + [math.ceil(s.duration) for s in listed])
        else:  # a live or growing playlist must not change its target later: a fixed one, as for live
            target = max([LIVE_TARGET] + [math.ceil(s.duration) for s in listed])
        lines = ["#EXTM3U", "#EXT-X-VERSION:7", f"#EXT-X-TARGETDURATION:{target}"]
        if view.live:
            lines.append(f"#EXT-X-MEDIA-SEQUENCE:{listed[0].g if listed else 0}")
            lines.append(f"#EXT-X-DISCONTINUITY-SEQUENCE:{self._discontinuities(view, listed)}")
        else:
            lines.append(f"#EXT-X-PLAYLIST-TYPE:{'VOD' if complete else 'EVENT'}")
        prev: Segment | None = None
        for seg in listed:
            if prev is None or seg.run != prev.run:
                if prev is not None:
                    lines.append("#EXT-X-DISCONTINUITY")
                lines.append(f'#EXT-X-MAP:URI="/dvb/v/{view.vid}/{role}/init-{seg.run}.mp4"')
            elif seg.start - prev.end > GAP_SECONDS:
                lines.append("#EXT-X-DISCONTINUITY")
            lines.append(f"#EXT-X-PROGRAM-DATE-TIME:{iso(seg.start)}")
            lines.append(f"#EXTINF:{seg.duration:.3f},")
            lines.append(f"/dvb/v/{view.vid}/{role}/{seg.g}.m4s")
            prev = seg
        if complete:
            lines.append("#EXT-X-ENDLIST")
        return ("\n".join(lines) + "\n").encode("utf-8")

    def _discontinuities(self, view: View, listed: list[Segment]) -> int:
        """Live: discontinuities between the view's first segment and the window, counted incrementally."""
        if not listed:
            return view.disc_count
        first = listed[0]
        if first.g > view.disc_g:
            prev = self.store.by_g(view.channel, view.disc_g)
            for seg in self.store.between(view.channel, prev.start if prev else first.start, first.start + 0.001):
                if seg.g <= view.disc_g or seg.g > first.g:
                    continue
                if prev is not None and (seg.run != prev.run or seg.start - prev.end > GAP_SECONDS):
                    view.disc_count += 1
                prev = seg
            view.disc_g = first.g
        return view.disc_count

    def view_file(self, path: str, owner: str = "") -> tuple[bytes, str]:
        """``/dvb/v/<rest>``: a rendition playlist, init segment or media segment of a live view."""
        m = VIEW_RE.match(path)
        if not m:
            raise ArchiveError(404, "Nenalezeno")
        with self._lock:
            view = self._views.get(m.group(1))
            if view is not None:
                view.touched = time.monotonic()
        if view is None or (owner and view.owner != owner):
            raise ArchiveError(404, "Platnost odkazu vypršela")
        if m.group(2):
            return self._media_playlist(view, m.group(2), self._segments(view)), M3U8
        role = m.group(3)
        if m.group(5):
            run = m.group(5)
            if not any(s.run == run for s in self._segments(view)) and not self._run_in_view(view, run):
                raise ArchiveError(404, "Segment nenalezen")
            info = self._run(view.channel, run)
            if info is None:
                raise ArchiveError(404, "Segment nenalezen")
            return self._init_for(info, role), "video/mp4"
        seg = self.store.by_g(view.channel, int(m.group(6)))
        if seg is None or not self._in_view(view, seg):
            raise ArchiveError(404, "Segment nenalezen")
        info = self._run(view.channel, seg.run)
        try:
            data = self.store.segment_path(seg).read_bytes()
        except OSError:
            raise ArchiveError(404, "Segment nenalezen")
        if info is None:
            raise ArchiveError(404, "Segment nenalezen")
        data = fmp4.shift_fragments(fmp4.fix_leading_pictures(data, info.hevc_track), info.shifts)
        track = self._track_for(info, role)
        if track is not None:
            data = fmp4.split_segment(data, track, info.trex)
        return data, "video/iso.segment"

    def _in_view(self, view: View, seg: Segment) -> bool:
        if view.live:
            return seg.g >= view.origin_g
        return seg.end > view.start - PRE_PADDING and seg.start < view.end + POST_PADDING

    def _run_in_view(self, view: View, run: str) -> bool:
        if view.live:
            newest = self.store.newest(view.channel)
            return newest is not None and newest.run == run
        return False

    def _init_for(self, info: RunInfo, role: str) -> bytes:
        track = self._track_for(info, role)
        return info.init if track is None else fmp4.split_init(info.init, track)

    @staticmethod
    def _track_for(info: RunInfo, role: str) -> int | None:
        if role == "m":
            return None
        if role == "v":
            return info.video
        index = int(role[1:])
        if index >= len(info.audio):
            raise ArchiveError(404, "Zvuková stopa není k dispozici")
        return info.audio[index]

    def _run(self, channel: str, run: str) -> RunInfo | None:
        key = (channel, run)
        with self._lock:
            hit = self._runs.get(key)
            if hit is not None:
                self._runs.move_to_end(key)
                return hit
        raw = self.store.run_init(channel, run)
        if raw is None:
            return None
        tracks = fmp4.tracks(raw)
        meta = self.store.run_meta(channel, run)
        labels = meta.get("audio") if isinstance(meta.get("audio"), list) else []
        info = RunInfo(init=fmp4.neutralise_edits(raw), shifts=fmp4.track_shifts(raw), hevc_track=fmp4.hevc_track(raw),
                       video=next((t for t, h in tracks if h == "vide"), None),
                       audio=[t for t, h in tracks if h == "soun"], trex=fmp4.trex_defaults(raw),
                       labels=[x if isinstance(x, dict) else {} for x in labels])
        while len(info.labels) < len(info.audio):
            info.labels.append({})
        with self._lock:
            self._runs[key] = info
            while len(self._runs) > RUN_CACHE:
                self._runs.popitem(last=False)
        return info

    # -- thumbnails --------------------------------------------------------------------------
    def thumbnail(self, rid: str) -> bytes | None:
        parsed = parse_recording_id(rid)
        if parsed is None or not self.thumbs.enabled:
            return None
        channel, start, end = parsed
        if channel not in (self.offered() or set()) or not self.is_programme(channel, start, end):
            return None
        now = self.now()
        recorded = min(end, now) - start
        if recorded <= 0:
            return None
        at = start + min(300, 0.2 * recorded)
        final = at - start >= 300 or now >= end
        segs = self.store.between(channel, at, at + 1) or self.store.between(channel, start, min(end, now))
        if not segs:
            return None
        seg = segs[0]

        def produce() -> bytes | None:
            info = self._run(channel, seg.run)
            try:
                data = self.store.segment_path(seg).read_bytes()
            except OSError:
                return None
            if info is None:
                return None
            return self.thumbs.jpeg_from_mp4(info.init + data)

        return self.thumbs.get_from(rid, final, produce)

    # -- retention / recorders ----------------------------------------------------------------
    def _wanted(self) -> set[str] | None:
        offered = self.offered()
        if offered is None:
            return None
        if self.can_record() is not None:
            return set()  # never onto the SD card, whatever the settings say
        # never more than the Pi manages, whatever the settings say (the admin page refuses more anyway)
        return set(sorted(self.record_channels() & offered)[:self.max_recordings])

    def sync(self) -> None:
        wanted = self._wanted()
        if wanted is not None:  # TVHeadend unreachable: leave the recorders as they are
            self.recorders.sync(wanted)

    def enforce(self) -> space.Plan:
        """Age limit, free-space reserve (fair share), hard stop; and recordings of channels no longer
        selected or offered are deleted."""
        wanted = self._wanted()
        for channel in self.store.channels() if wanted is not None else []:
            if channel not in wanted:
                self.forget_channel(channel)
        blocks = {c: self.store.blocks(c) for c in self.store.channels()}
        free, total = self.store.disk()
        plan = space.plan(blocks, self.now(), self.max_days(), free, total)
        for block in plan.delete:
            self.store.delete_block(block.channel, block.name)
        if plan.delete:
            ages = sum(1 for r in plan.reasons.values() if r == "age")
            self.log(f"dvb: recording: deleted {len(plan.delete)} block(s) ({ages} by age, "
                     f"{len(plan.delete) - ages} for space)")
            for channel in blocks:
                self.store.prune_runs(channel, self.recorders.active_runs(channel))
        if plan.shortfall > 0:
            self.recorders.pause(True)
        elif self.recorders.paused:
            free_now, total_now = self.store.disk()
            if free_now >= space.reserve_bytes(total_now) + space.RESUME_MARGIN:
                self.recorders.pause(False)
        self.sync()
        self.last_plan = plan
        return plan

    def start(self) -> None:
        threading.Thread(target=self._loop, name="dvb-recording", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self.recorders.stop_all()

    def _loop(self) -> None:
        while True:
            try:
                self.enforce()
            except Exception as exc:  # never let the retention thread die
                self.log(f"dvb: recording maintenance failed: {exc!r}")
            if self._stop.wait(LOOP_SECONDS):
                return

    # -- overview for /admin -------------------------------------------------------------------
    def overview(self, names: dict[str, str]) -> dict:
        free, total = self.store.disk()
        recorded = self.record_channels()
        channels = []
        for channel in sorted(set(self.store.channels()) | recorded, key=lambda c: names.get(c, c).casefold()):
            stats = self.store.stats(channel)
            rec = self.recorders.recorder(channel)
            channels.append({
                "id": channel,
                "name": names.get(channel, channel),
                "recording": channel in recorded,
                "state": rec.state if rec is not None else ("pozastaveno – plný disk" if self.recorders.paused and
                                                           channel in recorded else "nenahrává se"),
                "hours": round(stats["seconds"] / 3600, 2),
                "oldest": iso(stats["oldest"]) if stats["oldest"] else None,
                "newest": iso(stats["newest"]) if stats["newest"] else None,
                "bytes": stats["bytes"],
                "gbPerHour": round(stats["bytesPerHour"] / 1000 ** 3, 2) if stats["bytesPerHour"] else None,
                "gaps": stats["gaps"],
                "gapMinutes": round(stats["gapSeconds"] / 60, 1),
            })
        blocks = {c: self.store.blocks(c) for c in self.store.channels()}
        return {
            "disk": {"freeBytes": free, "totalBytes": total, "reserveBytes": space.reserve_bytes(total)},
            "usedBytes": sum(c["bytes"] for c in channels),
            "maxDays": self.max_days(),
            "paused": self.recorders.paused,
            "hoursPerChannel": space.hours_per_channel(blocks, free, total, self.max_days()),
            "channels": channels,
        }


def _epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _quote(text: str) -> str:
    return text.replace('"', "'").replace("\n", " ")
