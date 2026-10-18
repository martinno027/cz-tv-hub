"""``/dvb/*`` routes: JSON for channels/EPG/status and on-demand HLS (see spec/dvb-gateway.md).

Framework-free: ``handle()`` takes the request parts and returns a ``Response``, so the
routes are unit-tested without sockets. No download/export endpoint, by design.
"""
from __future__ import annotations

import hmac
import http.cookies
import json
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from ..config import Config
from . import epg
from .hls import (Codecs, EmptySource, HlsManager, HlsSession, StreamFailed, build_ffmpeg_command, probe_codecs,
                  rewrite_playlist)
from .retention import RetentionLoop
from .sources import GrowingRecording
from .tvheadend import Channel, TunerBusy, TvheadendClient, TvhError, TvhNotFound

UUID = r"[A-Za-z0-9_-]{1,64}"
LIVE_RE = re.compile(rf"^/dvb/live/({UUID})\.m3u8$")
RECORDING_RE = re.compile(rf"^/dvb/recording/({UUID})\.m3u8$")
COOKIE_NAME = "cztvhub_key"
HLS_RE = re.compile(r"^/dvb/hls/([0-9a-f]{32})/([A-Za-z0-9_.-]+)$")
ICON_RE = re.compile(rf"^/dvb/icon/({UUID})$")
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
TVH_ICON_RE = re.compile(r"^imagecache/\d+$")
CHANNEL_CACHE_SECONDS = 60.0
PLAYLIST_TIMEOUT = 25.0
VOD_WAIT_SECONDS = 5.0  # short finished recordings are fully remuxed by then and served as VOD
ZAP_IDLE_SECONDS = 8.0  # a live session nobody polled for this long no longer has a viewer
JSON = "application/json; charset=utf-8"
M3U8 = "application/vnd.apple.mpegurl"
MEDIA_TYPES = {".m4s": "video/iso.segment", ".mp4": "video/mp4"}


@dataclass
class Response:
    status: int
    body: bytes = b""
    content_type: str = JSON
    headers: dict[str, str] = field(default_factory=dict)


def json_response(status: int, payload: Any, headers: dict[str, str] | None = None) -> Response:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return Response(status, body, JSON, {"Cache-Control": "no-store", **(headers or {})})


def error(status: int, message: str, headers: dict[str, str] | None = None) -> Response:
    return json_response(status, {"error": message}, headers)


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class DvbGateway:
    def __init__(self, cfg: Config, client: TvheadendClient | None = None, hls: HlsManager | None = None,
                 now: Callable[[], float] = time.time, prober: Callable[[bytes], Codecs] | None = None,
                 popen: Callable[..., Any] | None = None, playlist_timeout: float = PLAYLIST_TIMEOUT,
                 log: Callable[[str], None] = print):
        self.cfg = cfg
        self.dvb = cfg.dvb
        tvh = cfg.tvheadend
        self.client = client or TvheadendClient(tvh.url, tvh.user, tvh.password)
        self.hls = hls or HlsManager(self.dvb.hls_dir, self.dvb.idle_seconds, log=log)
        self.now = now
        self.prober = prober or (lambda data: probe_codecs(data, self.dvb.ffprobe))
        self.popen = popen
        self.playlist_timeout = playlist_timeout
        self.log = log
        self.retention = RetentionLoop(self.client, self.dvb.retention_days, log=log)
        self._channels: list[Channel] = []
        self._channels_at = float("-inf")
        self._channels_lock = threading.Lock()

    # -- lifecycle -------------------------------------------------------------------------
    def start(self) -> None:
        self.hls.start()
        self.retention.start()

    def stop(self) -> None:
        self.retention.stop()
        self.hls.shutdown()

    # -- entry point -----------------------------------------------------------------------
    def handle(self, path: str, query: Mapping[str, str], headers: Mapping[str, str]) -> Response:
        if not self.dvb.enabled:
            return error(404, "DVB není zapnuté")
        if not self._authorized(headers.get("Authorization") or "", headers.get("Cookie") or ""):
            return error(401, "Chybí nebo nesedí klíč", {"WWW-Authenticate": 'Bearer realm="cztvhub"'})
        try:
            return self._route(path, query, headers)
        except HttpError as exc:
            return error(exc.status, exc.message)
        except TunerBusy:
            return error(503, TunerBusy.user_message)
        except TvhNotFound:
            return error(404, "Nenalezeno")
        except StreamFailed as exc:
            self.log(f"dvb: {path}: {exc}")
            return error(502, exc.user_message)
        except TvhError as exc:
            self.log(f"dvb: {path}: {exc}")
            return error(502, TvhError.user_message)

    def _authorized(self, header: str, cookie: str = "") -> bool:
        """Bearer key, or the same key as cookie ``cztvhub_key`` (Samsung AVPlay cannot send headers)."""
        if not self.dvb.key:
            return False
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer":
            token = _cookie_key(cookie)
            if token is None:
                return False
        return hmac.compare_digest(token.strip().encode("utf-8"), self.dvb.key.encode("utf-8"))

    def _route(self, path: str, query: Mapping[str, str], headers: Mapping[str, str]) -> Response:
        if path == "/dvb/status":
            return self.status()
        if path == "/dvb/channels":
            return self.channels()
        if path == "/dvb/epg":
            return self.epg(query.get("date"))
        if m := HLS_RE.match(path):
            return self.media(m.group(1), m.group(2), headers.get("Range"))
        if m := LIVE_RE.match(path):
            return self.live(m.group(1))
        if m := RECORDING_RE.match(path):
            return self.recording(m.group(1))
        if m := ICON_RE.match(path):
            return self.icon(m.group(1))
        return error(404, "Nenalezeno")

    # -- JSON ------------------------------------------------------------------------------
    def _channel_list(self) -> list[Channel]:
        with self._channels_lock:
            if time.monotonic() - self._channels_at > CHANNEL_CACHE_SECONDS:
                self._channels = self.client.channels()
                self._channels_at = time.monotonic()
            return self._channels

    def status(self) -> Response:
        try:
            tuners = [{"name": t.name, "inUse": t.in_use} for t in self.client.tuners()]
            reachable = True
        except TvhError:
            tuners, reachable = [], False
        try:
            usage = shutil.disk_usage(self.dvb.hls_dir)
            disk = {"freeBytes": usage.free, "totalBytes": usage.total}
        except OSError:
            disk = {"freeBytes": 0, "totalBytes": 0}
        return json_response(200, {"enabled": True, "retentionDays": self.dvb.retention_days, "tuners": tuners,
                                   "disk": disk, "tvheadendReachable": reachable})

    def channels(self) -> Response:
        return json_response(200, {"channels": [
            {"id": c.uuid, "number": c.number, "name": c.name, "icon": f"/dvb/icon/{c.uuid}" if c.icon else None}
            for c in self._channel_list()
        ]})

    def epg(self, day_param: str | None) -> Response:
        today = epg.broadcast_day(datetime.fromtimestamp(self.now(), timezone.utc))
        if day_param:
            try:
                day = date.fromisoformat(day_param)
            except ValueError:
                raise HttpError(400, "Neplatné datum (YYYY-MM-DD)")
        else:
            day = today
        first, last = epg.allowed_days(today, self.dvb.retention_days)
        if not first <= day <= last:
            raise HttpError(400, f"Program je k dispozici jen od {first.isoformat()} do {last.isoformat()}")
        start, end = epg.day_bounds(day)
        events = self.client.epg_events(start, end)
        entries = self.client.dvr_entries("grid")
        return json_response(200, {"date": day.isoformat(), "events": epg.build_epg(events, entries, start, end)})

    def icon(self, channel_id: str) -> Response:
        channel = next((c for c in self._channel_list() if c.uuid == channel_id), None)
        if channel is None or not channel.icon:
            raise HttpError(404, "Kanál nemá logo")
        if not (TVH_ICON_RE.match(channel.icon) or channel.icon.startswith(self.client.base + "/imagecache/")):
            raise HttpError(404, "Kanál nemá logo")
        body, content_type = self.client.fetch_icon(channel.icon)
        return Response(200, body, content_type, {"Cache-Control": "max-age=86400"})

    # -- HLS -------------------------------------------------------------------------------
    def _session(self, key: str, live: bool, open_source: Callable[[], Any]) -> HlsSession:
        def build(directory: Path, codecs: Codecs) -> list[str]:
            if codecs.video and codecs.video not in ("hevc", "h264"):
                self.log(f"dvb: {key}: unexpected video codec {codecs.video!r}")
            return build_ffmpeg_command(directory, codecs, live, self.dvb.ffmpeg)

        def factory(sid: str, directory: Path) -> HlsSession:
            extra = {"popen": self.popen} if self.popen else {}
            return HlsSession(sid, key, directory, live, open_source, build, self.prober, **extra)

        return self.hls.open(key, factory)

    def _playlist(self, session: HlsSession) -> Response:
        text = session.wait_playlist(self.playlist_timeout)
        if not session.live and session.vod is None:
            # Decided once per session: the playlist type must not change while a player follows it.
            if not isinstance(session.source, GrowingRecording):
                deadline = time.monotonic() + VOD_WAIT_SECONDS
                while not session.finished() and session.usable() and time.monotonic() < deadline:
                    time.sleep(0.1)
                text = session.wait_playlist(self.playlist_timeout)
            session.vod = session.finished() and "#EXT-X-ENDLIST" in text
        body = rewrite_playlist(text, f"/dvb/hls/{session.sid}/", vod=bool(session.vod))
        return Response(200, body.encode("utf-8"), M3U8, {"Cache-Control": "no-cache"})

    def live(self, channel_id: str) -> Response:
        if not any(c.uuid == channel_id for c in self._channel_list()):
            raise HttpError(404, "Neznámý kanál")
        open_source = lambda: self.client.open_live(channel_id)  # noqa: E731
        try:
            session = self._open_live(channel_id, open_source)
        except TunerBusy:
            if not self.hls.stop_idle_live(ZAP_IDLE_SECONDS):
                raise
            session = self._open_live(channel_id, open_source)
        return self._playlist(session)

    def _open_live(self, channel_id: str, open_source: Callable[[], Any]) -> HlsSession:
        try:
            return self._session(f"live:{channel_id}", True, open_source)
        except EmptySource:
            if self._all_tuners_busy():
                raise TunerBusy("no data and every tuner busy")
            raise StreamFailed("Kanál se nepodařilo naladit")

    def _all_tuners_busy(self) -> bool:
        try:
            tuners = self.client.tuners()
        except TvhError:
            return False
        return bool(tuners) and all(t.in_use for t in tuners)

    def recording(self, dvr_uuid: str) -> Response:
        def state() -> str:
            entry = next((e for e in self.client.dvr_entries("grid") if e.uuid == dvr_uuid), None)
            return epg.recording_state(entry.sched_status) if entry else ""

        def open_source():
            current = state()
            if current == "recording":
                return GrowingRecording(self.client, dvr_uuid, lambda: state() == "recording")
            if current == "completed":
                return self.client.open_recording(dvr_uuid)
            raise HttpError(404, "Nahrávka není k dispozici")

        return self._playlist(self._session(f"rec:{dvr_uuid}", False, open_source))

    def media(self, sid: str, name: str, range_header: str | None) -> Response:
        session = self.hls.get(sid)
        path = session.media_path(name) if session else None
        if path is None:
            raise HttpError(404, "Segment nenalezen")
        try:
            data = path.read_bytes()
        except OSError:
            raise HttpError(404, "Segment nenalezen")
        content_type = MEDIA_TYPES.get(path.suffix, "application/octet-stream")
        headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-cache"}
        if range_header:
            m = RANGE_RE.match(range_header.strip())
            size = len(data)
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    first = int(m.group(1))
                    last = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                else:
                    first, last = max(0, size - int(m.group(2))), size - 1
                if first <= last < size:
                    headers["Content-Range"] = f"bytes {first}-{last}/{size}"
                    return Response(206, data[first:last + 1], content_type, headers)
            return Response(416, b"", content_type, {"Content-Range": f"bytes */{size}"})
        return Response(200, data, content_type, headers)


def _cookie_key(header: str) -> str | None:
    cookies = http.cookies.SimpleCookie()
    try:
        cookies.load(header)
    except http.cookies.CookieError:
        return None
    morsel = cookies.get(COOKIE_NAME)
    return morsel.value if morsel is not None else None
