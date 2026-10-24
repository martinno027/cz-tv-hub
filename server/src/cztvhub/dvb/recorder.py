"""Continuous recording of the antenna channels selected in /admin (spec/dvb-gateway.md "Continuous recording").

One ffmpeg per channel reads TVHeadend's local stream (``/stream/channel/<uuid>?profile=pass`` on 127.0.0.1, never
CT internet streams), copies the video and writes fMP4 segments with **every** audio track muxed (AAC, AC-3 and
E-AC-3 copied, others such as MPEG audio converted to AAC). A mover thread follows ffmpeg's short rolling playlist
and moves each finished segment into its 10-minute block of the store (buffer.py) with its wall-clock start
(``#EXT-X-PROGRAM-DATE-TIME``). If the source or ffmpeg ends, a new run starts after a pause. On the way into
ffmpeg the pump shows each chunk to an EIT sniffer (eit.py, T14: real programme starts), which never changes it.
"""
from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .buffer import BufferStore
from .eit import EitSniffer, EitStore, PresentTracker
from .tvheadend import TunerBusy, TvhError

SEGMENT_SECONDS = 2  # cut at the first keyframe after 2 s (ČT's GOP ~1.3 s → ~2.6 s segments)
LIST_SIZE = 15
PROBE_BYTES = 512 * 1024
PROBE_SECONDS = 8.0
CHUNK = 64 * 1024
POLL_SECONDS = 0.5
RETRY_SECONDS = 10.0
BUSY_RETRY_SECONDS = 60.0
COPY_AUDIO = ("aac", "ac3", "eac3")
LANGUAGES = {"ces": "Čeština", "cze": "Čeština", "slk": "Slovenština", "slo": "Slovenština", "eng": "Angličtina",
             "deu": "Němčina", "ger": "Němčina", "fra": "Francouzština", "fre": "Francouzština",
             "pol": "Polština", "rus": "Ruština", "ukr": "Ukrajinština", "spa": "Španělština",
             "ita": "Italština", "hun": "Maďarština", "qaa": "Původní znění", "mul": "Více jazyků"}


def probe_streams(data: bytes, ffprobe: str = "ffprobe") -> list[dict]:
    """Video and audio streams of a TS sample: [{"type", "codec", "language", "impaired"}] in stream order."""
    cmd = [ffprobe, "-v", "error", "-f", "mpegts", "-show_entries",
           "stream=index,codec_type,codec_name:stream_tags=language:stream_disposition=visual_impaired,"
           "hearing_impaired", "-of", "json", "-i", "pipe:0"]
    try:
        result = subprocess.run(cmd, input=data, capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_streams(result.stdout.decode("utf-8", "replace"))


def parse_streams(output: str) -> list[dict]:
    try:
        raw = json.loads(output).get("streams", [])
    except (ValueError, AttributeError):
        return []
    out = []
    for s in raw:
        kind = s.get("codec_type")
        if kind not in ("video", "audio") or not s.get("codec_name"):
            continue
        tags = s.get("tags") or {}
        disp = s.get("disposition") or {}
        out.append({"type": kind, "codec": s["codec_name"], "language": (tags.get("language") or "").lower(),
                    "impaired": bool(disp.get("visual_impaired"))})
    return out


def audio_label(stream: dict, position: int) -> str:
    """Czech name of an audio track for the players' menus."""
    name = LANGUAGES.get(stream.get("language", ""), "") or f"Zvuk {position + 1}"
    if stream.get("impaired"):
        name += " (pro nevidomé)"
    elif stream.get("codec") in ("ac3", "eac3"):
        name += " (Dolby)"
    return name


def build_command(work: Path, streams: list[dict], ffmpeg: str = "ffmpeg") -> list[str]:
    """TS on stdin → fMP4 HLS in ``work``: first video copied, every audio track (copy or AAC)."""
    video = next((s for s in streams if s["type"] == "video"), None)
    audio = [s for s in streams if s["type"] == "audio"]
    # one thread per recorder: several run side by side on a 4-core Pi, so they must not each grab every core
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-threads", "1",
           "-fflags", "+genpts+discardcorrupt", "-f", "mpegts", "-i", "pipe:0", "-map", "0:v:0"]
    for i in range(len(audio)):
        cmd += ["-map", f"0:a:{i}"]
    cmd += ["-c:v", "copy"]
    tag = {"hevc": "hvc1", "h264": "avc1"}.get(video["codec"] if video else "")
    if tag:
        cmd += ["-tag:v", tag]
    for i, stream in enumerate(audio):
        if stream["codec"] in COPY_AUDIO:
            cmd += [f"-c:a:{i}", "copy"]
        else:
            # DVB-T2 sends HE-AAC in LATM (aac_latm), which MP4 cannot carry: decode and re-encode, cheaply
            cmd += [f"-c:a:{i}", "aac", f"-b:a:{i}", "128k", f"-aac_coder:a:{i}", "fast"]
    cmd += ["-f", "hls", "-hls_time", str(SEGMENT_SECONDS), "-hls_segment_type", "fmp4",
            "-hls_fmp4_init_filename", "init.mp4", "-hls_segment_filename", str(work / "seg_%06d.m4s"),
            "-hls_list_size", str(LIST_SIZE), "-hls_flags", "program_date_time+temp_file",
            str(work / "live.m3u8")]
    return cmd


def parse_rolling(text: str) -> list[tuple[str, float, float | None]]:
    """(file name, duration, wall-clock start or None) of every segment of ffmpeg's rolling playlist."""
    out, duration, pdt = [], 0.0, None
    for line in text.splitlines():
        if line.startswith("#EXTINF:"):
            try:
                duration = float(line[8:].split(",", 1)[0])
            except ValueError:
                duration = 0.0
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            pdt = _parse_pdt(line.split(":", 1)[1].strip())
        elif line and not line.startswith("#"):
            out.append((line.rsplit("/", 1)[-1], duration, pdt))
            duration, pdt = 0.0, None
    return out


def _parse_pdt(value: str) -> float | None:
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value.replace("Z", "+0000"), fmt).timestamp()
        except ValueError:
            continue
    return None


def segment_number(name: str) -> int | None:
    if name.startswith("seg_") and name.endswith(".m4s"):
        try:
            return int(name[4:-4])
        except ValueError:
            return None
    return None


class ChannelRecorder:
    """Records one channel until stopped; each source/ffmpeg session is one run."""

    def __init__(self, channel: str, store: BufferStore, open_source: Callable[[], Any],
                 ffmpeg: str = "ffmpeg", prober: Callable[[bytes], list[dict]] | None = None,
                 popen: Callable[..., Any] = subprocess.Popen, log: Callable[[str], None] = print,
                 retry_seconds: float = RETRY_SECONDS, busy_retry_seconds: float = BUSY_RETRY_SECONDS,
                 eit: EitStore | None = None):
        self.channel, self.store, self.open_source = channel, store, open_source
        self.ffmpeg, self.popen, self.log = ffmpeg, popen, log
        self.prober = prober or (lambda data: probe_streams(data))
        self.retry_seconds, self.busy_retry_seconds = retry_seconds, busy_retry_seconds
        self.state = "starting"
        self.run: str | None = None
        self.started_at: float | None = None
        self.last_segment_at: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: Any = None
        self._source: Any = None
        self._eit = PresentTracker(channel, eit.append, log) if eit is not None else None
        self._eit_failed = False

    # -- lifecycle -------------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name=f"rec-{self.channel[:8]}", daemon=True)
        self._thread.start()

    def stop(self, wait: float = 10.0) -> None:
        self._stop.set()
        self._kill()
        if self._thread is not None:
            self._thread.join(timeout=wait)

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def healthy(self, now: float | None = None, within: float = 20.0) -> bool:
        """Recording right now: a segment arrived recently."""
        now = time.time() if now is None else now
        return self.last_segment_at is not None and now - self.last_segment_at < within

    def _loop(self) -> None:
        while not self._stop.is_set():
            delay = self.retry_seconds
            try:
                self._run_once()
            except TunerBusy:
                self.state = "čeká na volný tuner"
                delay = self.busy_retry_seconds
            except TvhError as exc:
                self.state = "TVHeadend nedostupný"
                self.log(f"dvb: recorder {self.channel}: {exc}")
            except Exception as exc:  # keep recording the other channels whatever happens here
                self.state = "chyba nahrávání"
                self.log(f"dvb: recorder {self.channel}: {exc!r}")
            finally:
                self._kill()
            if self._stop.wait(delay):
                break
        self.state = "zastaveno"

    def _kill(self) -> None:
        source, self._source = self._source, None
        if source is not None:
            try:
                source.close()
            except Exception:
                pass
        proc = self._process
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    # -- one run ---------------------------------------------------------------------------
    def _run_once(self) -> None:
        self.state = "ladí"
        source = self.open_source()
        self._source = source
        read = getattr(source, "read1", source.read)
        head = bytearray()
        deadline = time.monotonic() + PROBE_SECONDS
        while len(head) < PROBE_BYTES and time.monotonic() < deadline and not self._stop.is_set():
            chunk = read(CHUNK)
            if not chunk:
                break
            head += chunk
        if not head:
            raise TvhError("source sent no data")
        streams = self.prober(bytes(head))
        if not any(s["type"] == "video" for s in streams):
            raise TvhError("no video stream in the source")
        run = f"{int(time.time())}-{secrets.token_hex(2)}"
        work = self.store.work_dir(self.channel, run)
        work.mkdir(parents=True, exist_ok=True)
        audio = [s for s in streams if s["type"] == "audio"]
        meta = {"run": run, "started": time.time(),
                "video": next(s["codec"] for s in streams if s["type"] == "video"),
                "audio": [{"codec": s["codec"], "language": s["language"], "label": audio_label(s, i),
                           "impaired": s["impaired"]} for i, s in enumerate(audio)]}
        self._process = self.popen(build_command(work, streams, self.ffmpeg), stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL)
        self.run, self.started_at, self.state = run, time.time(), "nahrává"
        sniffer = EitSniffer(self._eit) if self._eit is not None else None
        pump = threading.Thread(target=self._pump, args=(source, read, bytes(head), sniffer), daemon=True,
                                name=f"rec-pump-{self.channel[:8]}")
        pump.start()
        saved = False
        moved: set[str] = set()
        try:
            while not self._stop.is_set():
                # the init segment is final once ffmpeg lists its first segment (it is written as streams appear)
                if not saved and (work / "init.mp4").is_file() and self._listed(work):
                    self.store.save_run(self.channel, run, (work / "init.mp4").read_bytes(), meta)
                    saved = True
                if saved:
                    self._move(work, run, moved)
                if self._process.poll() is not None:
                    break
                time.sleep(POLL_SECONDS)
            if saved:
                self._move(work, run, moved)  # what ffmpeg finished before it exited
        finally:
            shutil.rmtree(work, ignore_errors=True)
            self.store.prune_runs(self.channel)
        if not self._stop.is_set():
            raise TvhError(f"recording ended (ffmpeg exit {self._process.poll()})")

    @staticmethod
    def _listed(work: Path) -> bool:
        try:
            return bool(parse_rolling((work / "live.m3u8").read_text(encoding="utf-8")))
        except OSError:
            return False

    def _pump(self, source: Any, read: Callable[[int], bytes], head: bytes, sniffer: EitSniffer | None = None) -> None:
        stdin = self._process.stdin
        try:
            stdin.write(head)
            sniffer = self._sniff(sniffer, head)
            while not self._stop.is_set():
                chunk = read(CHUNK)
                if not chunk:
                    break
                stdin.write(chunk)
                if sniffer is not None:
                    sniffer = self._sniff(sniffer, chunk)
        except Exception:
            pass  # ffmpeg or the source went away; the run loop notices
        finally:
            try:
                stdin.close()
            except Exception:
                pass

    def _sniff(self, sniffer: EitSniffer | None, chunk: bytes) -> EitSniffer | None:
        """EIT measurement only looks; whatever goes wrong in it, recording goes on (without it for this run)."""
        if sniffer is None:
            return None
        try:
            sniffer.feed(chunk)
            return sniffer
        except Exception as exc:
            if not self._eit_failed:
                self._eit_failed = True
                self.log(f"dvb: eit: {self.channel}: parser stopped for this run: {exc!r}")
            return None

    def _move(self, work: Path, run: str, moved: set[str]) -> None:
        try:
            text = (work / "live.m3u8").read_text(encoding="utf-8")
        except OSError:
            return
        for name, duration, start in parse_rolling(text):
            n = segment_number(name)
            if name in moved or n is None or start is None or duration <= 0:
                continue
            path = work / name
            if not path.is_file():
                moved.add(name)
                continue
            self.store.add(self.channel, run, n, start, duration, path)
            moved.add(name)
            self.last_segment_at = time.time()


class RecorderManager:
    """The set of running recorders follows the channels selected in /admin; ``paused`` when the disk is full."""

    def __init__(self, store: BufferStore, open_source: Callable[[str], Any], ffmpeg: str = "ffmpeg",
                 ffprobe: str = "ffprobe", log: Callable[[str], None] = print,
                 factory: Callable[[str], ChannelRecorder] | None = None, eit: EitStore | None = None):
        self.store, self.log = store, log
        self._factory = factory or (lambda ch: ChannelRecorder(
            ch, store, lambda: open_source(ch), ffmpeg, lambda data: probe_streams(data, ffprobe), log=log, eit=eit))
        self._lock = threading.Lock()
        self._recorders: dict[str, ChannelRecorder] = {}
        self.paused = False
        self.wanted: set[str] = set()

    def sync(self, wanted: set[str]) -> None:
        with self._lock:
            self.wanted = set(wanted)
            target = set() if self.paused else self.wanted
            for channel in [c for c in self._recorders if c not in target]:
                self._recorders.pop(channel).stop(wait=0)
            for channel in sorted(target - set(self._recorders)):
                recorder = self._factory(channel)
                self._recorders[channel] = recorder
                recorder.start()

    def pause(self, paused: bool) -> None:
        if paused == self.paused:
            return
        self.paused = paused
        self.log("dvb: recording paused, disk full" if paused else "dvb: recording resumed")
        self.sync(self.wanted)

    def recorder(self, channel: str) -> ChannelRecorder | None:
        with self._lock:
            return self._recorders.get(channel)

    def active_runs(self, channel: str) -> list[str]:
        rec = self.recorder(channel)
        return [rec.run] if rec is not None and rec.run else []

    def stop_all(self) -> None:
        with self._lock:
            recorders, self._recorders = list(self._recorders.values()), {}
        for rec in recorders:
            rec.stop()
