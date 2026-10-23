"""On-demand remux of MPEG-TS into temporary fMP4 HLS (one ffmpeg per source, shared by viewers).

Video is always copied (``-c:v copy``); audio becomes AAC unless it already is. Every
session lives in its own directory under ``hls_dir`` that is deleted when the session
has not been requested for ``idle_seconds``. Nothing here is kept after viewing stops.
"""
from __future__ import annotations

import json
import queue
import re
import secrets
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

PLAYLIST = "index.m3u8"
INIT = "init.mp4"
SEGMENT_PATTERN = "seg_%05d.m4s"
MEDIA_NAME_RE = re.compile(r"^(init\.mp4|seg_\d{5,}\.m4s)$")
SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
OWN_FILE_SUFFIXES = (".m3u8", ".m4s", ".mp4", ".tmp")
HLS_TIME = 4
# Live: cut at every keyframe after 1 s (ČT's DVB-T2 GOP is ~1.3 s), announce a fixed target of 2 s and
# answer the first playlist request only once 6 s exist (3 x target, Apple's live hold-back). With a
# target that changed from 1 to 3 and a start 1.3 s from the live edge, the Apple TV stalled and
# glitched (its log: "restarting 1.28s from end of live playlist; target duration 1s - stall danger").
LIVE_HLS_TIME = 1
LIVE_TARGET = 2
LIVE_START_SECONDS = 6.0
LIVE_LIST_SIZE = 40  # x ~1 s: a ~40 s live window as before
PROBE_BYTES = 128 * 1024
PROBE_SECONDS = 6.0
CHUNK = 64 * 1024
QUEUE_CHUNKS = 256  # up to 16 MB between the source and a slow ffmpeg


class StreamFailed(Exception):
    user_message = "Vysílání se nepodařilo spustit"


class EmptySource(StreamFailed):
    """The source closed before sending any data (no signal, or TVHeadend gave up)."""


class Source(Protocol):
    def read(self, size: int) -> bytes: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class Codecs:
    video: str = ""
    audio: str = ""


def parse_probe(output: str) -> Codecs:
    try:
        streams = json.loads(output).get("streams", [])
    except (ValueError, AttributeError):
        return Codecs()
    video = next((s.get("codec_name", "") for s in streams if s.get("codec_type") == "video"), "")
    audio = next((s.get("codec_name", "") for s in streams if s.get("codec_type") == "audio"), "")
    return Codecs(video or "", audio or "")


def probe_codecs(data: bytes, ffprobe: str = "ffprobe") -> Codecs:
    """Codecs of the first video/audio stream (the PMT of a few hundred kB of TS is enough)."""
    cmd = [ffprobe, "-v", "error", "-f", "mpegts", "-show_entries", "stream=codec_type,codec_name",
           "-of", "json", "-i", "pipe:0"]
    try:
        result = subprocess.run(cmd, input=data, capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return Codecs()
    return parse_probe(result.stdout.decode("utf-8", "replace"))


def video_tag(codec: str) -> str | None:
    return {"hevc": "hvc1", "h264": "avc1"}.get(codec)


def build_ffmpeg_command(out_dir: Path, codecs: Codecs, live: bool, ffmpeg: str = "ffmpeg",
                         hls_time: int | None = None, list_size: int = LIVE_LIST_SIZE) -> list[str]:
    if hls_time is None:
        hls_time = LIVE_HLS_TIME if live else HLS_TIME
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error",
           "-fflags", "+genpts+discardcorrupt", "-f", "mpegts", "-i", "pipe:0",
           "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy"]
    tag = video_tag(codecs.video)
    if tag:
        cmd += ["-tag:v", tag]
    if codecs.audio == "aac":
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    cmd += ["-f", "hls", "-hls_time", str(hls_time), "-hls_segment_type", "fmp4",
            "-hls_fmp4_init_filename", INIT, "-hls_segment_filename", str(out_dir / SEGMENT_PATTERN)]
    # No independent_segments: DVB-T2 HEVC (ČT) uses an open GOP, every segment starts with a CRA whose
    # RASL pictures reference the previous segment, so claiming EXT-X-INDEPENDENT-SEGMENTS let players
    # restart decoding at a segment boundary and show artefacts (seen 2026-10-23, `dvb-diag --frames`).
    if live:
        cmd += ["-hls_list_size", str(list_size), "-hls_delete_threshold", "3",
                "-hls_flags", "delete_segments+temp_file"]
    else:
        cmd += ["-hls_list_size", "0", "-hls_playlist_type", "event",
                "-hls_flags", "temp_file"]
    cmd.append(str(out_dir / PLAYLIST))
    return cmd


def media_seconds(text: str) -> float:
    """Total ``#EXTINF`` duration of a playlist."""
    total = 0.0
    for line in text.splitlines():
        if line.startswith("#EXTINF:"):
            try:
                total += float(line[8:].split(",", 1)[0])
            except ValueError:
                pass
    return total


def needed_target(text: str) -> int:
    """Smallest TARGETDURATION the playlist's segments allow (EXTINF rounded to the nearest integer)."""
    longest = 0
    for line in text.splitlines():
        if line.startswith("#EXTINF:"):
            try:
                longest = max(longest, int(float(line[8:].split(",", 1)[0]) + 0.5))
            except ValueError:
                pass
    return longest


def rewrite_playlist(text: str, prefix: str, vod: bool = False, target: int | None = None) -> str:
    """Point segment and init URIs at ``prefix`` (e.g. /dvb/hls/<sid>/); optionally mark as VOD and
    announce a fixed ``#EXT-X-TARGETDURATION`` (ffmpeg's grows with the longest segment so far)."""
    out = []
    for line in text.splitlines():
        if target is not None and line.startswith("#EXT-X-TARGETDURATION:"):
            line = f"#EXT-X-TARGETDURATION:{target}"
        elif line.startswith("#EXT-X-MAP:"):
            line = re.sub(r'URI="([^"]+)"', lambda m: f'URI="{prefix}{m.group(1).rsplit("/", 1)[-1]}"', line)
        elif line and not line.startswith("#"):
            line = prefix + line.rsplit("/", 1)[-1]
        elif vod and line.startswith("#EXT-X-PLAYLIST-TYPE:"):
            line = "#EXT-X-PLAYLIST-TYPE:VOD"
        out.append(line)
    return "\n".join(out) + "\n"


Popen = Callable[..., Any]


class HlsSession:
    """One source → one ffmpeg → one directory. Thread-safe; started by the first viewer."""

    def __init__(self, sid: str, key: str, directory: Path, live: bool,
                 open_source: Callable[[], Source],
                 build_command: Callable[[Path, Codecs], list[str]],
                 prober: Callable[[bytes], Codecs],
                 popen: Popen = subprocess.Popen,
                 clock: Callable[[], float] = time.monotonic):
        self.sid, self.key, self.directory, self.live = sid, key, directory, live
        self._open_source, self._build_command, self._prober, self._popen = open_source, build_command, prober, popen
        self._clock = clock
        self.last_access = clock()
        self.process: Any = None
        self.error: Exception | None = None
        self.vod: bool | None = None  # decided at the first playlist response of a recording
        self.shifts: dict[int, int] | None = None  # per-track tfdt shifts for A/V sync (fmp4.py)
        self.target = LIVE_TARGET  # live: announced TARGETDURATION, only ever grows (should stay 2)
        self.answered = False  # live: the first playlist answer waits for LIVE_START_SECONDS of media
        self.source: Source | None = None
        self._chunks: queue.Queue[bytes | None] = queue.Queue(maxsize=QUEUE_CHUNKS)
        self._start_lock = threading.Lock()
        self._started = False
        self._stopped = threading.Event()

    def touch(self) -> None:
        self.last_access = self._clock()

    def idle_for(self) -> float:
        return self._clock() - self.last_access

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    def finished(self) -> bool:
        """ffmpeg exited cleanly (a complete recording remux)."""
        return self.process is not None and self.process.poll() == 0

    def usable(self) -> bool:
        if self.stopped or self.error is not None:
            return False
        if self.process is None:
            return True  # still starting
        code = self.process.poll()
        return code is None or (code == 0 and not self.live)

    def ensure_started(self) -> None:
        with self._start_lock:
            if not self._started:
                self._started = True
                try:
                    self._start()
                except Exception as exc:
                    self.error = exc
                    self.stop()
            if self.error is not None:
                raise self.error

    def _start(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.source = self._open_source()
        threading.Thread(target=self._reader, name=f"hls-read-{self.sid[:8]}", daemon=True).start()
        head = bytearray()
        ended = False
        deadline = time.monotonic() + PROBE_SECONDS
        while len(head) < PROBE_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                chunk = self._chunks.get(timeout=remaining)
            except queue.Empty:
                break
            if chunk is None:
                ended = True
                break
            head += chunk
        if not head:
            raise EmptySource("source sent no data")
        codecs = self._prober(bytes(head))
        cmd = self._build_command(self.directory, codecs)
        self.process = self._popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
        threading.Thread(target=self._pump, args=(bytes(head), ended), name=f"hls-pump-{self.sid[:8]}",
                         daemon=True).start()

    def _reader(self) -> None:
        """Source → queue, so a stalled source can never block the viewer's request."""
        read = getattr(self.source, "read1", self.source.read)
        try:
            while not self.stopped:
                chunk = read(CHUNK)
                if not chunk:
                    break
                while not self.stopped:
                    try:
                        self._chunks.put(chunk, timeout=0.5)
                        break
                    except queue.Full:
                        pass
        except Exception:
            pass  # source failed or was closed by stop()
        finally:
            while not self.stopped:
                try:
                    self._chunks.put(None, timeout=0.5)
                    break
                except queue.Full:
                    pass

    def _pump(self, head: bytes, ended: bool) -> None:
        stdin = self.process.stdin
        try:
            stdin.write(head)
            while not ended and not self.stopped:
                try:
                    chunk = self._chunks.get(timeout=0.5)
                except queue.Empty:
                    continue
                if chunk is None:
                    break
                stdin.write(chunk)
        except Exception:
            pass  # viewer gone (session stopped) or ffmpeg died
        finally:
            try:
                stdin.close()
            except Exception:
                pass

    def wait_playlist(self, timeout: float) -> str:
        """Playlist text once it lists at least one segment."""
        path = self.directory / PLAYLIST
        deadline = time.monotonic() + timeout
        while True:
            self.touch()
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                text = ""
            if "#EXTINF" in text:
                return text
            if self.stopped or (self.process is not None and self.process.poll() is not None):
                raise StreamFailed("ffmpeg exited before the first segment")
            if time.monotonic() >= deadline:
                raise StreamFailed("timed out waiting for the first segment")
            time.sleep(0.2)

    def media_path(self, name: str) -> Path | None:
        if not MEDIA_NAME_RE.match(name):
            return None
        path = self.directory / name
        return path if path.is_file() else None

    def stop(self) -> None:
        self._stopped.set()
        if self.source is not None:
            try:
                self.source.close()
            except Exception:
                pass
        proc = self.process
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        shutil.rmtree(self.directory, ignore_errors=True)


SessionFactory = Callable[[str, Path], HlsSession]


class HlsManager:
    def __init__(self, root: Path, idle_seconds: float, reap_interval: float | None = None,
                 log: Callable[[str], None] = print):
        self.root = Path(root)
        self.idle_seconds = idle_seconds
        self.reap_interval = reap_interval if reap_interval is not None else max(0.5, min(5.0, idle_seconds / 3))
        self.log = log
        self._lock = threading.Lock()
        self._by_key: dict[str, HlsSession] = {}
        self._by_id: dict[str, HlsSession] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle -------------------------------------------------------------------------
    def cleanup_root(self) -> None:
        """Remove leftovers of a previous run (only our own session dirs and files)."""
        self.root.mkdir(parents=True, exist_ok=True)
        for child in self.root.iterdir():
            if child.is_dir() and SESSION_ID_RE.match(child.name):
                shutil.rmtree(child, ignore_errors=True)
            elif child.is_file() and child.suffix in OWN_FILE_SUFFIXES:
                child.unlink(missing_ok=True)

    def start(self) -> None:
        self.cleanup_root()
        self._thread = threading.Thread(target=self._reap_loop, name="hls-reaper", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        for session in self.sessions():
            self._remove(session)

    def _reap_loop(self) -> None:
        while not self._stop.wait(self.reap_interval):
            self.reap()

    # -- sessions --------------------------------------------------------------------------
    def sessions(self) -> list[HlsSession]:
        with self._lock:
            return list(self._by_id.values())

    def get(self, sid: str) -> HlsSession | None:
        with self._lock:
            session = self._by_id.get(sid)
        if session is not None:
            session.touch()
        return session

    def open(self, key: str, factory: SessionFactory) -> HlsSession:
        """Shared session for ``key`` (e.g. ``live:<uuid>``), started on first use."""
        stale = None
        with self._lock:
            session = self._by_key.get(key)
            if session is not None and not session.usable():
                stale, session = session, None
                self._forget(stale)
            if session is None:
                sid = secrets.token_hex(16)
                session = factory(sid, self.root / sid)
                self._by_key[key] = session
                self._by_id[sid] = session
            session.touch()
        if stale is not None:
            stale.stop()
        try:
            session.ensure_started()
        except Exception:
            self._remove(session)
            raise
        return session

    def stop_idle_live(self, older_than: float) -> int:
        """Free tuners held by live sessions nobody polled recently (zapping with one tuner)."""
        victims = [s for s in self.sessions() if s.live and s.idle_for() > older_than]
        for session in victims:
            self._remove(session)
        return len(victims)

    def stop_key(self, key: str) -> bool:
        """Stop the session of ``key`` now (e.g. the live stream of a channel just hidden)."""
        with self._lock:
            session = self._by_key.get(key)
        if session is not None:
            self._remove(session)
        return session is not None

    def reap(self) -> None:
        for session in self.sessions():
            if session.idle_for() > self.idle_seconds:
                self.log(f"hls: session {session.key} idle, removing")
                self._remove(session)
            elif session.process is not None and not session.usable():
                self._remove(session)

    def _forget(self, session: HlsSession) -> None:
        if self._by_key.get(session.key) is session:
            del self._by_key[session.key]
        self._by_id.pop(session.sid, None)

    def _remove(self, session: HlsSession) -> None:
        with self._lock:
            self._forget(session)
        session.stop()
