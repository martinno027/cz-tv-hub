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
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from ..config import Config
from . import breaks as ads
from .archive import Archive, ArchiveError, parse_recording_id
from .buffer import BufferStore
from .recorder import RecorderManager
from . import epg
from . import fmp4
from .hls import (INIT, LIVE_START_SECONDS, Codecs, EmptySource, HlsManager, HlsSession, StreamFailed,
                  build_ffmpeg_command, media_seconds, needed_target, probe_codecs, rewrite_playlist)
from .retention import RetentionLoop
from .sources import GrowingRecording
from .epgstore import EpgHistory, merge_past
from .thumbs import Settings, Thumbnails, ThumbsBusy
from .tvheadend import Channel, DvrEntry, TunerBusy, TvheadendClient, TvhError, TvhNotFound

UUID = r"[A-Za-z0-9_-]{1,64}"
LIVE_RE = re.compile(rf"^/dvb/live/({UUID})\.m3u8$")
RECORDING_RE = re.compile(rf"^/dvb/recording/({UUID})\.m3u8$")
BREAKS_RE = re.compile(rf"^/dvb/recording/({UUID})/breaks$")
THUMB_RE = re.compile(rf"^/dvb/recording/({UUID})/thumb\.jpg$")
JOBS_PATH = "/dvb/ads/jobs"
COOKIE_NAME = "cztvhub_key"
MAX_BODY = 64 * 1024
HLS_RE = re.compile(r"^/dvb/hls/([0-9a-f]{32})/([A-Za-z0-9_.-]+)$")
VIEW_PREFIX = "/dvb/v/"  # continuous recording: playlists and media of one programme or the live window
WORKER_IDLE = 120.0  # the worker may switch programmes after this long without a request
ICON_RE = re.compile(rf"^/dvb/icon/({UUID})$")
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
TVH_ICON_RE = re.compile(r"^imagecache/\d+$")
CHANNEL_CACHE_SECONDS = 60.0
EPG_COLLECT_SECONDS = 1800.0  # how often the coming days' EPG is remembered
EPG_AHEAD_SECONDS = 3 * 86400
PLAYLIST_TIMEOUT = 25.0
VOD_WAIT_SECONDS = 5.0  # short finished recordings are fully remuxed by then and served as VOD
ZAP_IDLE_SECONDS = 8.0  # a live session nobody polled for this long no longer has a viewer
THUMB_RETRY_AFTER = 5  # seconds; both ffmpeg slots stayed busy (several TVs prefetching)
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
                 log: Callable[[str], None] = print, archive: Archive | None = None):
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
        self.ads = cfg.ads
        self.breaks = ads.BreakStore(self.ads.breaks_dir)
        self.settings = Settings(self.dvb.state_dir)
        self.thumbs = Thumbnails(self.client, self.settings, self.dvb.ffmpeg, now=now, log=log)
        self.retention = RetentionLoop(self.client, self.dvb.retention_days, log=log, after=self._after_retention)
        self.history = EpgHistory(self.dvb.state_dir)
        if archive is None:
            store = BufferStore(self.dvb.buffer_dir)
            recorders = RecorderManager(store, self._recording_source, self.dvb.ffmpeg, self.dvb.ffprobe, log=log)
            archive = Archive(store, recorders, self.settings, self.thumbs, self.dvb.retention_days, now=now,
                              log=log, offered=self._offered_ids, is_programme=self._is_programme,
                              max_recordings=self.dvb.max_recordings,
                              allow_system_disk=self.dvb.buffer_on_system_disk)
        self.archive = archive
        self._worker_rid: str | None = None
        self._worker_at = float("-inf")
        self._collector_stop = threading.Event()
        self._worker_sid: str | None = None  # the worker's one recording session (spec/ad-breaks.md)
        self._worker_lock = threading.Lock()
        self._channels: list[Channel] = []
        self._channels_at = float("-inf")
        self._channels_lock = threading.Lock()

    # -- lifecycle -------------------------------------------------------------------------
    def start(self) -> None:
        self.hls.start()
        self.retention.start()
        self.archive.start()
        threading.Thread(target=self._collect_loop, name="dvb-epg-history", daemon=True).start()

    def stop(self) -> None:
        self._collector_stop.set()
        self.archive.stop()
        self.retention.stop()
        self.hls.shutdown()

    # -- entry point -----------------------------------------------------------------------
    def handle(self, path: str, query: Mapping[str, str], headers: Mapping[str, str], method: str = "GET",
               body: bytes = b"", client: str = "") -> Response:
        if not self.dvb.enabled:
            return error(404, "DVB není zapnuté")
        role = self._role(headers.get("Authorization") or "", headers.get("Cookie") or "", client)
        if role is None:
            return error(401, "Chybí nebo nesedí klíč", {"WWW-Authenticate": 'Bearer realm="cztvhub"'})
        try:
            if role == "worker":
                return self._worker_route(method, path, query, body)
            if method != "GET":
                if BREAKS_RE.match(path) and self.ads.enabled:
                    raise HttpError(403, "Bloky reklam ukládá jen pracovník")
                raise HttpError(405, "Nepodporovaná metoda")
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

    def _role(self, header: str, cookie: str, client: str) -> str | None:
        """"app" ([dvb] key as bearer or cookie), "worker" ([ads] worker_key, bearer header only) or None."""
        scheme, _, token = header.partition(" ")
        if scheme.lower() == "bearer":
            if _same(token.strip(), self.dvb.key):
                return "app"
            if self.ads.enabled and _same(token.strip(), self.ads.worker_key):
                if self.ads.worker_hosts and client not in self.ads.worker_hosts:
                    return None
                return "worker"
            return None
        return "app" if _same(_cookie_key(cookie) or "", self.dvb.key) else None

    def _authorized(self, header: str, cookie: str = "") -> bool:
        """The app key: bearer, or the same key as cookie ``cztvhub_key`` (Samsung AVPlay cannot send headers)."""
        return self._role(header, cookie, "") == "app"

    def _route(self, path: str, query: Mapping[str, str], headers: Mapping[str, str]) -> Response:
        if path == "/dvb/status":
            return self.status()
        if path == "/dvb/channels":
            return self.channels()
        if path == "/dvb/epg":
            return self.epg(query.get("date"))
        if m := HLS_RE.match(path):
            return self.media(m.group(1), m.group(2), headers.get("Range"))
        if path.startswith(VIEW_PREFIX):
            return self.view_file(path[len(VIEW_PREFIX):], headers.get("Range"))
        if m := LIVE_RE.match(path):
            return self.live(m.group(1))
        if m := RECORDING_RE.match(path):
            return self.recording(m.group(1))
        if m := BREAKS_RE.match(path):
            return self.get_breaks(m.group(1))
        if m := THUMB_RE.match(path):
            return self.thumbnail(m.group(1))
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

    def _recording_source(self, channel: str):
        """The only input of the continuous recording: TVHeadend's stream of an antenna channel on this Pi
        (never CT internet streams, never another host)."""
        if not local_url(self.client.base):
            raise TvhError("recording needs TVHeadend on 127.0.0.1")
        return self.client.open_live(channel)

    def _offered_ids(self) -> set[str] | None:
        """Channels offered to the apps, or None when TVHeadend cannot be asked (nothing is deleted then)."""
        try:
            return {c.uuid for c in self._channel_list()}
        except TvhError:
            return None

    def _is_programme(self, channel: str, start: int, end: int) -> bool:
        """An EPG programme (remembered or current) of the channel with exactly these times."""
        if any(e.channel == channel and e.start == start and e.stop == end
               for e in self.history.between(start, start + 1)):
            return True
        try:
            return any(e.channel == channel and e.start == start and e.stop == end
                       for e in self.client.epg_events(start, start + 1))
        except TvhError:
            return False

    # -- EPG history (epgstore.py) -----------------------------------------------------------
    def _remember(self, events: list, window: tuple[int, int]) -> None:
        now = self.now()
        changed = self.history.merge(events, now, window)
        changed = self.history.prune(now - (max(self.dvb.retention_days, self.archive.max_days()) + 1) * 86400) \
            or changed
        if changed:
            try:
                self.history.save()
            except OSError as exc:
                self.log(f"dvb: cannot save EPG history: {exc}")

    def collect_epg(self) -> None:
        """Remember the coming days' programmes (TVHeadend lists them days ahead, then drops them)."""
        now = int(self.now())
        window = (now, now + EPG_AHEAD_SECONDS)
        self._remember(self.client.epg_events(*window), window)

    def _collect_loop(self) -> None:
        while True:
            try:
                self.collect_epg()
            except TvhError as exc:
                self.log(f"dvb: EPG history: {exc}")
            if self._collector_stop.wait(EPG_COLLECT_SECONDS):
                return

    def channels_changed(self, hidden: list[str] | None = None) -> None:
        """After the admin page changed TVHeadend's channels: reload them, end hidden channels' live streams."""
        with self._channels_lock:
            self._channels_at = float("-inf")
        for channel_id in hidden or []:
            self.hls.stop_key(f"live:{channel_id}")
            self.archive.forget_channel(channel_id)  # a hidden channel is not recorded (legal-guard)
        self.archive.sync()

    def status(self) -> Response:
        try:
            tuners = [{"name": t.name, "inUse": t.in_use} for t in self.client.tuners()]
            reachable = True
        except TvhError:
            tuners, reachable = [], False
        # the recordings disk (buffer_dir); before it exists, the disk of the temporary playback files
        try:
            path = self.dvb.buffer_dir if self.dvb.buffer_dir.is_dir() else self.dvb.hls_dir
            usage = shutil.disk_usage(path)
            disk = {"freeBytes": usage.free, "totalBytes": usage.total}
        except OSError:
            disk = {"freeBytes": 0, "totalBytes": 0}
        return json_response(200, {"enabled": True, "retentionDays": self.archive.max_days(), "tuners": tuners,
                                   "disk": disk, "tvheadendReachable": reachable,
                                   "thumbnails": self.thumbs.enabled})

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
        first, last = epg.allowed_days(today, max(self.dvb.retention_days, self.archive.max_days()))
        if not first <= day <= last:
            raise HttpError(400, f"Program je k dispozici jen od {first.isoformat()} do {last.isoformat()}")
        start, end = epg.day_bounds(day)
        # Channels hidden in the admin page (disabled in TVHeadend) are not offered, nor their recordings.
        offered = {c.uuid for c in self._channel_list()}
        live = self.client.epg_events(start, end)
        self._remember(live, (start, end))
        # continuously recorded channels take their recordings from the buffer, not from TVHeadend's DVR
        recorded = {c for c in offered if self.archive.is_recorded(c)}
        entries = [e for e in self.client.dvr_entries("grid") if e.channel in offered and e.channel not in recorded]
        # TVHeadend forgets ended programmes; the remembered ones fill the past (spec "EPG history")
        events = [e for e in merge_past(live, self.history.between(start, end), entries) if e.channel in offered]
        built = epg.build_epg(events, entries, start, end)
        self.archive.annotate(built, recorded)
        if self.ads.enabled:
            stored = self.breaks.ids()
            for event in built:
                if event["recording"] and event["recording"]["id"] in stored:
                    event["recording"]["breaks"] = True
        return json_response(200, {"date": day.isoformat(), "events": built})

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
        if session.live and not session.answered:
            # Start with a cushion: the player begins as far back as the playlist goes.
            deadline = time.monotonic() + self.playlist_timeout
            while media_seconds(text) < LIVE_START_SECONDS and session.usable() and time.monotonic() < deadline:
                time.sleep(0.2)
                text = session.wait_playlist(self.playlist_timeout)
            session.answered = True
            self._log_startup(session, media_seconds(text))
        if session.live:
            session.target = max(session.target, needed_target(text))
            body = rewrite_playlist(text, f"/dvb/hls/{session.sid}/", target=session.target)
            return Response(200, body.encode("utf-8"), M3U8, {"Cache-Control": "no-cache"})
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
        if self.archive.live_ready(channel_id):
            # a recorded channel plays from its recording, a few seconds behind, without another tuner
            body, content_type = self._archive_call(self.archive.live, channel_id)
            return Response(200, body, content_type, {"Cache-Control": "no-cache"})
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

    def _archive_call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ArchiveError as exc:
            raise HttpError(exc.status, exc.message)

    def view_file(self, rest: str, range_header: str | None, owner: str = "") -> Response:
        data, content_type = self._archive_call(self.archive.view_file, rest, owner)
        if content_type.endswith("mpegurl"):
            return Response(200, data, content_type, {"Cache-Control": "no-cache"})
        return _ranged(data, content_type, range_header)

    def recording(self, dvr_uuid: str, on_session: Callable[[HlsSession], None] | None = None) -> Response:
        if parse_recording_id(dvr_uuid):
            body, content_type = self._archive_call(self.archive.programme, dvr_uuid)
            return Response(200, body, content_type, {"Cache-Control": "no-cache"})
        def entry() -> DvrEntry | None:
            return next((e for e in self.client.dvr_entries("grid") if e.uuid == dvr_uuid), None)

        def state() -> str:
            found = entry()
            return epg.recording_state(found.sched_status) if found else ""

        def open_source():
            found = entry()
            # recordings of channels hidden in the admin page are not offered
            if found is None or found.channel not in {c.uuid for c in self._channel_list()}:
                raise HttpError(404, "Nahrávka není k dispozici")
            current = epg.recording_state(found.sched_status)
            if current == "recording":
                return GrowingRecording(self.client, dvr_uuid, lambda: state() == "recording")
            if current == "completed":
                return self.client.open_recording(dvr_uuid)
            raise HttpError(404, "Nahrávka není k dispozici")

        session = self._session(f"rec:{dvr_uuid}", False, open_source)
        if on_session is not None:
            on_session(session)
        return self._playlist(session)

    def thumbnail(self, dvr_uuid: str) -> Response:
        """One still of a recording (spec/dvb-gateway.md "Thumbnails"); checked against TVHeadend every time.
        Busy ffmpeg slots answer 503 + Retry-After, so the apps ask again instead of remembering a 404."""
        try:
            return self._thumbnail(dvr_uuid)
        except ThumbsBusy:
            return error(503, "Náhledy se právě připravují", {"Retry-After": str(THUMB_RETRY_AFTER)})

    def _thumbnail(self, dvr_uuid: str) -> Response:
        if not self.thumbs.enabled:
            raise HttpError(404, "Náhledy jsou vypnuté")
        if parse_recording_id(dvr_uuid):
            data = self.archive.thumbnail(dvr_uuid)
            if data is None:
                raise HttpError(404, "Náhled není k dispozici")
            return Response(200, data, "image/jpeg", {"Cache-Control": "private, no-store"})
        entry = next((e for e in self.client.dvr_entries("grid") if e.uuid == dvr_uuid), None)
        state = epg.recording_state(entry.sched_status) if entry else ""
        if entry is None or state not in ("completed", "recording") \
                or entry.channel not in {c.uuid for c in self._channel_list()}:
            self.thumbs.forget(dvr_uuid)
            raise HttpError(404, "Náhled není k dispozici")
        data = self.thumbs.get(entry, recording=state == "recording")
        if data is None:
            raise HttpError(404, "Náhled není k dispozici")
        return Response(200, data, "image/jpeg", {"Cache-Control": "private, no-store"})

    def media(self, sid: str, name: str, range_header: str | None) -> Response:
        session = self.hls.get(sid)
        path = session.media_path(name) if session else None
        if path is None:
            raise HttpError(404, "Segment nenalezen")
        try:
            data = self._synced(session, path.name, path.read_bytes())
        except OSError:
            raise HttpError(404, "Segment nenalezen")
        return _ranged(data, MEDIA_TYPES.get(path.suffix, "application/octet-stream"), range_header)

    def _log_startup(self, session: HlsSession, media: float) -> None:
        """One line per live start: where the time goes (spec/dvb-gateway.md "Start-up timing")."""
        t = session.timing
        start = t.get("start")
        if start is None:
            return
        steps = [("tuner+TVHeadend", "opened"), ("first bytes", "first_bytes"), ("probe sample", "probe_sample"),
                 ("ffprobe", "probed"), ("first segment", "first_segment")]
        parts, prev = [], start
        for label, key in steps:
            if key in t:
                parts.append(f"{label} +{t[key] - prev:.1f}s")
                prev = t[key]
        total = time.monotonic() - start
        parts.append(f"cushion {media:.1f}s +{time.monotonic() - prev:.1f}s")
        self.log(f"dvb: live start {session.key}: " + ", ".join(parts) + f" = {total:.1f}s")

    @staticmethod
    def _synced(session: HlsSession, name: str, data: bytes) -> bytes:
        """A/V sync (spec/dvb-gateway.md "A/V sync", fmp4.py): neutral edit lists in the init segment,
        their offsets moved into every fragment's tfdt. Same sizes, so Range requests still fit."""
        if name == INIT:
            return fmp4.neutralise_edits(data)
        if session.shifts is None:
            try:
                init = (session.directory / INIT).read_bytes()
            except OSError:
                return data  # init not written yet (cannot happen for a listed segment)
            session.shifts = fmp4.track_shifts(init)
            session.hevc_track = fmp4.hevc_track(init)
        # open-GOP HEVC: keyframe after its leading pictures again (ffmpeg HLS muxer bug, fmp4.py)
        data = fmp4.fix_leading_pictures(data, session.hevc_track)
        return fmp4.shift_fragments(data, session.shifts)

    # -- ad breaks (spec/ad-breaks.md) -----------------------------------------------------
    def get_breaks(self, dvr_uuid: str) -> Response:
        if not self.ads.enabled:
            raise HttpError(404, "Přeskakování reklam není zapnuté")
        stored = self.breaks.get(dvr_uuid)
        if stored is None:
            return json_response(200, {"status": "pending", "breaks": []})
        return json_response(200, {"status": "done", "breaks": stored.get("breaks", [])})

    def _worker_route(self, method: str, path: str, query: Mapping[str, str], body: bytes) -> Response:
        """The worker key reaches only the job list, job recordings and the break upload."""
        if not self.ads.enabled:  # unreachable: the worker role needs [ads] enabled
            raise HttpError(404, "Přeskakování reklam není zapnuté")
        if method == "GET" and path == JOBS_PATH:
            return self.ad_jobs(query.get("analyzer") or "")
        if method == "GET" and (m := RECORDING_RE.match(path)):
            return self._worker_recording(m.group(1))
        if method == "GET" and (m := HLS_RE.match(path)) and m.group(1) == self._worker_sid:
            return self.media(m.group(1), m.group(2), None)
        if method == "GET" and path.startswith(VIEW_PREFIX):
            return self.view_file(path[len(VIEW_PREFIX):], None, owner="worker")
        if method == "PUT" and (m := BREAKS_RE.match(path)):
            return self.put_breaks(m.group(1), body)
        raise HttpError(403, "Klíč pracovníka sem nemá přístup")

    def _analysable(self, dvr_uuid: str) -> bool:
        parsed = parse_recording_id(dvr_uuid)
        if parsed:
            channel, start, end = parsed
            channels = {c.uuid: c for c in self._channel_list()}
            return ads.channel_selected(channels.get(channel), self.ads.channels) \
                and self.now() >= end and self._is_programme(channel, start, end) \
                and self.archive.state(channel, start, end) == "completed"
        entry = next((e for e in self.client.dvr_entries("grid") if e.uuid == dvr_uuid), None)
        channels = {c.uuid: c for c in self._channel_list()}
        return ads.analysable(entry, channels, self.ads.channels)

    def ad_jobs(self, analyzer: str) -> Response:
        if not ads.ANALYZER_RE.match(analyzer):
            raise HttpError(400, "Chybí parametr analyzer")
        channels = {c.uuid: c for c in self._channel_list()}
        pending = ads.jobs(self.client.dvr_entries("grid_finished"), channels, self.ads.channels, self.breaks,
                           analyzer)
        return json_response(200, {"analyzer": analyzer, "jobs": [{
            "recordingId": e.uuid,
            "channelId": e.channel,
            "channelName": channels[e.channel].name,
            "start": epg.iso(e.start),
            "end": epg.iso(e.stop),
            "playlist": f"/dvb/recording/{e.uuid}.m3u8",
        } for e in pending]})

    def _worker_recording(self, dvr_uuid: str) -> Response:
        if not self._analysable(dvr_uuid):
            raise HttpError(403, "Nahrávka není k analýze")
        if parse_recording_id(dvr_uuid):
            with self._worker_lock:
                busy = self._worker_rid not in (None, dvr_uuid) and time.monotonic() - self._worker_at < WORKER_IDLE
                if busy:
                    raise HttpError(409, "Pracovník už zpracovává jinou nahrávku")
                self._worker_rid, self._worker_at = dvr_uuid, time.monotonic()
            body, content_type = self._archive_call(self.archive.programme, dvr_uuid, owner="worker")
            return Response(200, body, content_type, {"Cache-Control": "no-cache"})
        key = f"rec:{dvr_uuid}"
        with self._worker_lock:
            # Looked up without touching it, so a refused request does not keep the old session alive.
            current = next((x for x in self.hls.sessions() if x.sid == self._worker_sid), None)
            if current is not None and current.key != key and current.usable():
                raise HttpError(409, "Pracovník už zpracovává jinou nahrávku")

        def remember(session: HlsSession) -> None:
            with self._worker_lock:
                self._worker_sid = session.sid

        return self.recording(dvr_uuid, on_session=remember)

    def put_breaks(self, dvr_uuid: str, body: bytes) -> Response:
        if len(body) > MAX_BODY:
            raise HttpError(400, "Příliš velký požadavek")
        if not self._analysable(dvr_uuid):
            raise HttpError(403, "Nahrávka není k analýze")
        try:
            analyzer, items = ads.validate(json.loads(body.decode("utf-8")))
        except (UnicodeDecodeError, ValueError) as exc:
            raise HttpError(400, f"Neplatné bloky: {exc}")
        self.breaks.put(dvr_uuid, analyzer, items)
        return Response(204, b"", JSON, {"Cache-Control": "no-store"})

    def _after_retention(self) -> None:
        """Derived data never outlives its recording: ad breaks, in-memory thumbnails."""
        entries = self.client.dvr_entries("grid") + self.client.dvr_entries("grid_finished") + \
            self.client.dvr_entries("grid_failed")
        self.thumbs.prune(e.uuid for e in entries)
        if self.ads.enabled:
            self._prune_breaks(entries)

    def _prune_breaks(self, entries) -> None:
        removed = self.breaks.prune(e.uuid for e in entries)
        if removed:
            self.log(f"dvb: removed ad breaks of {len(removed)} deleted recording(s)")


def local_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1")


def _ranged(data: bytes, content_type: str, range_header: str | None) -> Response:
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


def _same(token: str, key: str) -> bool:
    return bool(key) and hmac.compare_digest(token.encode("utf-8"), key.encode("utf-8"))


def _cookie_key(header: str) -> str | None:
    cookies = http.cookies.SimpleCookie()
    try:
        cookies.load(header)
    except http.cookies.CookieError:
        return None
    morsel = cookies.get(COOKIE_NAME)
    return morsel.value if morsel is not None else None
