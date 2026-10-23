"""Fake TVHeadend for developing the gateway and the apps without a tuner (``cztvhub dvb-fake``).

Implements the JSON API subset in tvheadend.py, generated HEVC + MPEG-1 Layer II MPEG-TS
live streams (``ffmpeg -f lavfi testsrc``), and a small DVR:
- channel 1 is "autorecorded": past slots are completed recordings (two short test clips,
  one entry failed), the current slot is recording in real time, the next ones are scheduled;
- channel 2 has completed recordings of past slots only; channels 3 and 4 have none.
Each channel has one service; channels 1 + 2 share a multiplex, 3 and 4 have their own (for /admin).
Like the real TVHeadend, EPG events disappear once they end (past programmes = DVR entries).
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import shutil
import socketserver
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

# name, number, slot minutes, recording mode ("auto" | "past" | ""), test pattern, tone Hz, multiplex
CHANNELS = [
    ("ČT1 HD", 1, 30, "auto", "testsrc2", 440, "626MHz"),
    ("ČT2 HD", 2, 45, "past", "testsrc", 550, "626MHz"),
    ("Nova HD", 3, 60, "", "smptehdbars", 660, "538MHz"),
    ("Prima HD", 4, 60, "", "rgbtestsrc", 770, "690MHz"),
]
PROGRAMMES = [
    ("Zprávy", 0x20), ("Film: Testovací obrazec", 0x10), ("Sportovní přenos", 0x40), ("Pohádka", 0x50),
    ("Dokument o anténách", 0x90), ("Hudební klip", 0x60), ("Soutěž", 0x30), ("Kulturní magazín", 0x70),
]
FAILED_SLOT_AGO = 5  # the 5th past slot of channel 1 is a failed recording
UPCOMING_SCHEDULED = 3
ICON_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def stable_uuid(*parts: Any) -> str:
    return hashlib.md5("/".join(map(str, parts)).encode("utf-8")).hexdigest()


def encode_args(pattern: str, tone: int, ffmpeg: str = "ffmpeg") -> list[str]:
    return [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error",
            "-f", "lavfi", "-i", f"{pattern}=size=640x360:rate=25",
            "-f", "lavfi", "-i", f"sine=frequency={tone}:sample_rate=48000",
            "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error",
            "-g", "50", "-b:v", "1000k", "-pix_fmt", "yuv420p",
            "-c:a", "mp2", "-b:a", "128k", "-f", "mpegts"]


class FakeTvheadend:
    def __init__(self, data_dir: Path, host: str = "127.0.0.1", port: int = 9981, tuners: int = 2,
                 user: str = "", password: str = "", clip_seconds: int = 60, retention_days: int = 3,
                 ffmpeg: str = "ffmpeg", log: Callable[[str], None] = print):
        self.data_dir = Path(data_dir)
        self.host, self.port, self.tuners = host, port, tuners
        self.user, self.password = user, password
        self.clip_seconds, self.retention_days = clip_seconds, retention_days
        self.ffmpeg, self.log = ffmpeg, log
        self.lock = threading.Lock()
        self.channels = [
            {"uuid": stable_uuid("channel", name), "name": name, "number": number, "enabled": True,
             "icon_public_url": f"imagecache/{number}" if number == 1 else "",
             "services": [stable_uuid("service", name)], "_slot": slot * 60,
             "_mode": mode, "_pattern": pattern, "_tone": tone, "_index": i}
            for i, (name, number, slot, mode, pattern, tone, _mux) in enumerate(CHANNELS)
        ]
        self.muxes = {stable_uuid("mux", mux): {"uuid": stable_uuid("mux", mux), "name": mux, "enabled": 1}
                      for *_, mux in CHANNELS}
        self.services = {
            stable_uuid("service", name): {"uuid": stable_uuid("service", name), "svcname": name, "enabled": True,
                                           "multiplex": mux, "multiplex_uuid": stable_uuid("mux", mux)}
            for name, *_, mux in CHANNELS
        }
        self.entries: dict[str, dict] = {}
        self.recorders: dict[str, subprocess.Popen] = {}
        self.active_streams: dict[int, str] = {}  # tuner index -> channel name
        self.dvr_config = {"uuid": stable_uuid("dvrconfig"), "name": "", "retention-days": 31,
                           "removal-days": 31, "enabled": True}
        self.autorecs: dict[str, dict] = {}
        self.clips: list[Path] = []
        self.server: socketserver.TCPServer | None = None
        self._stop = threading.Event()

    # -- schedule --------------------------------------------------------------------------
    def event_for(self, channel: dict, k: int) -> dict:
        slot = channel["_slot"]
        title, genre = PROGRAMMES[(k + channel["_index"] * 3) % len(PROGRAMMES)]
        return {
            "eventId": channel["_index"] * 100_000_000 + k,
            "channelUuid": channel["uuid"], "channelName": channel["name"], "channelNumber": channel["number"],
            "start": k * slot, "stop": (k + 1) * slot,
            "title": title, "subtitle": f"Díl {k % 100}",
            "summary": "Testovací pořad.",
            "description": f"Testovací pořad falešného TVHeadendu na kanálu {channel['name']}.",
            "genre": [genre],
        }

    def received(self, channel: dict) -> bool:
        return channel["enabled"] and any(
            self.services[s]["enabled"] and self.muxes[self.services[s]["multiplex_uuid"]]["enabled"] == 1
            for s in channel["services"])

    def events_between(self, start: int, stop: int) -> list[dict]:
        out = []
        for ch in self.channels:
            if not self.received(ch):
                continue
            slot = ch["_slot"]
            for k in range(start // slot, stop // slot + 1):
                ev = self.event_for(ch, k)
                if ev["stop"] > start and ev["start"] < stop:
                    out.append(ev)
        return out

    def _entry(self, channel: dict, k: int, status: str, filename: str, broadcast: bool) -> dict:
        ev = self.event_for(channel, k)
        return {
            "uuid": stable_uuid("dvr", ev["eventId"]), "channel": channel["uuid"], "channelname": channel["name"],
            "start": ev["start"], "stop": ev["stop"], "disp_title": ev["title"], "disp_subtitle": ev["subtitle"],
            "disp_summary": ev["summary"], "disp_description": ev["description"], "genre": ev["genre"],
            "broadcast": ev["eventId"] if broadcast else 0, "sched_status": status, "filename": filename,
            "filesize": 0, "_k": k, "_channel": channel,
        }

    def build_dvr(self, now: float) -> None:
        oldest = now - (self.retention_days + 0.5) * 86400
        for ch in self.channels:
            if not ch["_mode"]:
                continue
            slot = ch["_slot"]
            current = int(now) // slot
            for n, k in enumerate(range(current - 1, int(oldest) // slot, -1), start=1):
                if ch["_mode"] == "auto" and n == FAILED_SLOT_AGO:
                    entry = self._entry(ch, k, "completedError", "", False)
                else:
                    entry = self._entry(ch, k, "completed", str(self.clips[k % len(self.clips)]), False)
                self.entries[entry["uuid"]] = entry
            if ch["_mode"] == "auto":
                for k in range(current, current + 1 + UPCOMING_SCHEDULED):
                    entry = self._entry(ch, k, "scheduled", "", True)
                    self.entries[entry["uuid"]] = entry

    def tick(self, now: float) -> None:
        """Advance the fake DVR: start/stop real-time recorders, keep upcoming slots scheduled."""
        with self.lock:
            for uuid, entry in list(self.entries.items()):
                if entry["sched_status"] == "scheduled" and entry["start"] <= now < entry["stop"]:
                    path = self.data_dir / "rec" / f"{uuid}.ts"
                    ch = entry["_channel"]
                    cmd = encode_args(ch["_pattern"], ch["_tone"], self.ffmpeg)
                    cmd = cmd[:1] + ["-re"] + cmd[1:] + ["-t", str(int(entry["stop"] - now)), "-y", str(path)]
                    self.recorders[uuid] = subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
                    entry.update(sched_status="recording", filename=str(path))
                elif entry["sched_status"] == "scheduled" and entry["stop"] <= now:
                    entry["sched_status"] = "completedError"
                elif entry["sched_status"] == "recording" and entry["stop"] <= now:
                    self._stop_recorder(uuid)
                    entry["sched_status"] = "completed"
                    entry["broadcast"] = 0
            for ch in self.channels:
                if ch["_mode"] != "auto" or not ch["enabled"]:
                    continue
                current = int(now) // ch["_slot"]
                for k in range(current, current + 1 + UPCOMING_SCHEDULED):
                    entry = self._entry(ch, k, "scheduled", "", True)
                    self.entries.setdefault(entry["uuid"], entry)

    def _stop_recorder(self, uuid: str) -> None:
        proc = self.recorders.pop(uuid, None)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    # -- media -----------------------------------------------------------------------------
    def make_clips(self) -> None:
        clip_dir = self.data_dir / "clips"
        clip_dir.mkdir(parents=True, exist_ok=True)
        for name, pattern, tone in (("a", "testsrc2", 440), ("b", "testsrc", 880)):
            path = clip_dir / f"clip_{name}_{self.clip_seconds}s.ts"
            if not path.exists():
                self.log(f"fake-tvh: generating {path.name}")
                tmp = path.with_suffix(".part")
                cmd = encode_args(pattern, tone, self.ffmpeg) + ["-t", str(self.clip_seconds), "-y", str(tmp)]
                subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL)
                tmp.rename(path)
            self.clips.append(path)

    # -- lifecycle -------------------------------------------------------------------------
    def start(self) -> None:
        rec_dir = self.data_dir / "rec"
        shutil.rmtree(rec_dir, ignore_errors=True)
        rec_dir.mkdir(parents=True, exist_ok=True)
        self.make_clips()
        self.build_dvr(time.time())
        self.tick(time.time())
        self.server = _Server((self.host, self.port), _make_handler(self))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, name="fake-tvh", daemon=True).start()
        threading.Thread(target=self._tick_loop, name="fake-tvh-dvr", daemon=True).start()

    def _tick_loop(self) -> None:
        while not self._stop.wait(2):
            self.tick(time.time())

    def stop(self) -> None:
        self._stop.set()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        with self.lock:
            for uuid in list(self.recorders):
                self._stop_recorder(uuid)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    # -- API -------------------------------------------------------------------------------
    def public_entry(self, entry: dict) -> dict:
        out = {k: v for k, v in entry.items() if not k.startswith("_")}
        try:
            out["filesize"] = Path(entry["filename"]).stat().st_size if entry["filename"] else 0
        except OSError:
            out["filesize"] = 0
        return out

    def api(self, path: str, args: dict[str, str]) -> Any:
        now = time.time()
        if path == "serverinfo":
            return {"sw_version": "fake", "api_version": 19, "name": "Tvheadend (cztvhub fake)"}
        if path == "channel/grid":
            # like TVHeadend: disabled channels only with all=1
            rows = [{k: v for k, v in c.items() if not k.startswith("_")} for c in self.channels
                    if c["enabled"] or args.get("all") in ("1", "true")]
            return {"entries": rows, "total": len(rows)}
        if path == "epg/events/grid":
            return self._epg(args, now)
        if path.startswith("dvr/entry/grid"):
            kind = path[len("dvr/entry/grid"):]
            with self.lock:
                rows = [self.public_entry(e) for e in self.entries.values() if _in_grid(e["sched_status"], kind)]
            rows.sort(key=lambda e: e["start"])
            return {"entries": rows, "total": len(rows)}
        if path == "dvr/entry/remove":
            with self.lock:
                for uuid in _list_arg(args.get("uuid", "")):
                    entry = self.entries.get(uuid)
                    if entry is None or entry["sched_status"] in ("scheduled", "recording"):
                        continue  # like TVHeadend: remove is for finished entries
                    del self.entries[uuid]
                    if entry["filename"].startswith(str(self.data_dir / "rec")):
                        Path(entry["filename"]).unlink(missing_ok=True)
            return {}
        if path == "dvr/entry/cancel":
            with self.lock:
                for uuid in _list_arg(args.get("uuid", "")):
                    entry = self.entries.get(uuid)
                    if entry and entry["sched_status"] == "scheduled":
                        del self.entries[uuid]
                    elif entry and entry["sched_status"] == "recording":
                        self._stop_recorder(uuid)
                        entry.update(sched_status="completedError", broadcast=0)
            return {}
        if path == "mpegts/service/grid":
            # like TVHeadend: without hidemode=none, services of disabled multiplexes are left out
            rows = [s for s in self.services.values()
                    if args.get("hidemode") == "none" or self.muxes[s["multiplex_uuid"]]["enabled"] == 1]
            return {"entries": rows, "total": len(rows)}
        if path == "mpegts/mux/grid":
            rows = [m for m in self.muxes.values() if args.get("hidemode") != "all" or m["enabled"] == 1]
            return {"entries": rows, "total": len(rows)}
        if path == "status/inputs":
            with self.lock:
                busy = dict(self.active_streams)
            rows = [{"uuid": stable_uuid("tuner", i), "input": f"Fake DVB-T2 tuner #{i + 1}",
                     "stream": busy.get(i, ""), "subs": 1 if i in busy else 0, "weight": 0, "signal": 0}
                    for i in range(self.tuners)]
            return {"entries": rows, "totalCount": len(rows)}
        if path == "dvr/config/grid":
            return {"entries": [self.dvr_config], "total": 1}
        if path == "dvr/autorec/grid":
            return {"entries": list(self.autorecs.values()), "total": len(self.autorecs)}
        if path == "dvr/autorec/create":
            conf = json.loads(args.get("conf") or "{}")
            uuid = stable_uuid("autorec", conf.get("channel"), len(self.autorecs), now)
            self.autorecs[uuid] = {"uuid": uuid, **conf}
            return {"uuid": uuid}
        if path == "idnode/save":
            nodes = json.loads(args.get("node") or "{}")
            channels = {c["uuid"]: c for c in self.channels}
            for node in nodes if isinstance(nodes, list) else [nodes]:
                uuid = node.get("uuid")
                target = self.dvr_config if uuid == self.dvr_config["uuid"] else (
                    self.autorecs.get(uuid) or channels.get(uuid) or self.services.get(uuid) or self.muxes.get(uuid))
                if target is not None:
                    target.update(node)
            return {}
        if path == "idnode/delete":
            for uuid in _list_arg(args.get("uuid", "")):
                self.autorecs.pop(uuid, None)
            return {}
        return None

    def _epg(self, args: dict[str, str], now: float) -> dict:
        lo, hi = int(now), int(now) + 3 * 86400
        try:
            filters = json.loads(args.get("filter") or "[]")
        except ValueError:
            filters = []
        conditions = []
        for f in filters if isinstance(filters, list) else []:
            if f.get("type") == "numeric" and f.get("field") in ("start", "stop"):
                conditions.append((f["field"], f.get("comparison"), int(f.get("value", 0))))
                if f["field"] == "stop" and f.get("comparison") == "gt":
                    lo = max(lo, int(f["value"]) - 86400)
                if f["field"] == "start" and f.get("comparison") == "lt":
                    hi = min(hi, int(f["value"]) + 86400)
        rows = []
        with self.lock:
            by_broadcast = {e["broadcast"]: e for e in self.entries.values() if e["broadcast"]}
        for ev in self.events_between(lo, hi):
            if ev["stop"] <= now:  # expired, like TVHeadend
                continue
            if not all(_compare(ev[field], op, value) for field, op, value in conditions):
                continue
            entry = by_broadcast.get(ev["eventId"])
            if entry:
                ev.update(dvrUuid=entry["uuid"], dvrState=entry["sched_status"])
            rows.append(ev)
        limit = int(args.get("limit") or 50)
        return {"entries": rows[:limit], "totalCount": len(rows)}

    def tuner_acquire(self, channel_name: str) -> int | None:
        with self.lock:
            for i in range(self.tuners):
                if i not in self.active_streams:
                    self.active_streams[i] = channel_name
                    return i
        return None

    def tuner_release(self, index: int) -> None:
        with self.lock:
            self.active_streams.pop(index, None)


def _in_grid(status: str, kind: str) -> bool:
    if kind == "":
        return True
    if kind == "_upcoming":
        return status in ("scheduled", "recording")
    if kind == "_finished":
        return status == "completed"
    if kind == "_failed":
        return status not in ("scheduled", "recording", "completed")
    return False


def _compare(actual: int, op: str, value: int) -> bool:
    return {"gt": actual > value, "lt": actual < value, "eq": actual == value}.get(op, True)


def _list_arg(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except ValueError:
        return [value] if value else []
    return [str(v) for v in parsed] if isinstance(parsed, list) else [str(parsed)]


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _make_handler(fake: FakeTvheadend):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        def _send(self, status: int, body: bytes = b"", content_type: str = "application/json",
                  headers: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _authorized(self) -> bool:
            if not fake.password:
                return True
            expected = "Basic " + base64.b64encode(f"{fake.user}:{fake.password}".encode()).decode()
            if self.headers.get("Authorization") == expected:
                return True
            self._send(401, b"", "text/plain", {"WWW-Authenticate": 'Basic realm="tvheadend"'})
            return False

        def do_POST(self):
            self.do_GET()

        def do_GET(self):
            path, _, query = self.path.partition("?")
            args = dict(urllib.parse.parse_qsl(query))
            if self.command == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                args.update(urllib.parse.parse_qsl(self.rfile.read(length).decode("utf-8")))
            if not self._authorized():
                return
            if path.startswith("/api/"):
                result = fake.api(path[len("/api/"):], args)
                if result is None:
                    return self._send(404, b"{}")
                return self._send(200, json.dumps(result, ensure_ascii=False).encode("utf-8"))
            if path.startswith("/stream/channel/"):
                return self._live(path[len("/stream/channel/"):])
            if path.startswith("/dvrfile/"):
                return self._dvrfile(path[len("/dvrfile/"):])
            if path == "/imagecache/1":
                return self._send(200, ICON_PNG, "image/png")
            self._send(404, b"", "text/plain")

        def _live(self, uuid: str) -> None:
            channel = next((c for c in fake.channels if c["uuid"] == uuid), None)
            if channel is None or not fake.received(channel):
                return self._send(404, b"", "text/plain")
            tuner = fake.tuner_acquire(channel["name"])
            if tuner is None:
                return self._send(503, b"No free adapter", "text/plain")
            cmd = encode_args(channel["_pattern"], channel["_tone"], fake.ffmpeg)
            cmd = cmd[:1] + ["-re"] + cmd[1:] + ["-flush_packets", "1", "pipe:1"]
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                while not fake._stop.is_set():
                    chunk = proc.stdout.read1(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except OSError:
                pass  # client went away
            finally:
                proc.kill()
                proc.wait()
                proc.stdout.close()
                fake.tuner_release(tuner)

        def _dvrfile(self, uuid: str) -> None:
            with fake.lock:
                entry = fake.entries.get(uuid)
            if entry is None or not entry["filename"]:
                return self._send(404, b"", "text/plain")
            path = Path(entry["filename"])
            try:
                size = path.stat().st_size
            except OSError:
                return self._send(404, b"", "text/plain")
            first, status, headers = 0, 200, {"Accept-Ranges": "bytes"}
            range_header = self.headers.get("Range", "")
            if range_header.startswith("bytes="):
                first = int(range_header[6:].split("-")[0] or 0)
                if first >= size:
                    return self._send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
                status = 206
                headers["Content-Range"] = f"bytes {first}-{size - 1}/{size}"
            self.send_response(status)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Content-Length", str(size - first))
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command == "HEAD":
                return
            try:
                with path.open("rb") as f:
                    f.seek(first)
                    remaining = size - first
                    while remaining > 0:
                        chunk = f.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except OSError:
                self.close_connection = True

    return Handler
