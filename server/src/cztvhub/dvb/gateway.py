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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import api as ct_api
from .. import logsafe
from ..channels import BY_ID as CT_BY_ID
from ..channels import CHANNELS as CT_CHANNELS
from ..config import Config
from . import archive_catalog as catalog
from . import ctweb
from . import breaks as ads
from . import details as tmdb
from . import tmdbkeys, tmdbstore
from .archive import MAX_PROGRAMME, Archive, ArchiveError, parse_recording_id, recording_id
from .buffer import BufferStore
from .eit import EitStore
from .recorder import RecorderManager
from . import epg
from . import fmp4
from .hls import (INIT, LIVE_START_SECONDS, Codecs, EmptySource, HlsManager, HlsSession, StreamFailed,
                  build_ffmpeg_command, media_seconds, needed_target, probe_codecs, rewrite_playlist)
from .retention import RetentionLoop
from .sources import GrowingRecording
from .epgstore import EpgHistory, merge_past
from . import profiles as prof
from .defaults import DefaultsStore
from .profiles import ProfileError, ProfileStore
from .signinlog import SigninLog, client_info
from .signinlog import network as signinlog_network
from . import watchlog
from .defaults import PSP_STREAMS
from .thumbs import Settings, Thumbnails, ThumbsBusy
from .tvheadend import Channel, DvrEntry, TunerBusy, TvheadendClient, TvhError, TvhNotFound

UUID = r"[A-Za-z0-9_-]{1,64}"
LIVE_RE = re.compile(rf"^/dvb/live/({UUID})\.m3u8$")
RECORDING_RE = re.compile(rf"^/dvb/recording/({UUID})\.m3u8$")
BREAKS_RE = re.compile(rf"^/dvb/recording/({UUID})/breaks$")
THUMB_RE = re.compile(rf"^/dvb/recording/({UUID})/thumb\.jpg$")
TIMELINE_RE = re.compile(rf"^/dvb/timeline/({UUID})(\.m3u8)?$")
JOBS_PATH = "/dvb/ads/jobs"
WATCH_PATH = "/dvb/watch"  # watch reports (spec "Watch log", T53)
PSP_NAMES = {key.split(":", 1)[1]: name for key, name in PSP_STREAMS}  # the apps' Chamber stream ids
PROFILE_PREFIX = "/dvb/profile"  # /dvb/profile, /dvb/profile/logout, /dvb/profiles, /dvb/profiles/login
PROFILE_HEADER = "X-Cztvhub-Profile"
COOKIE_NAME = "cztvhub_key"
MAX_BODY = 64 * 1024
HLS_RE = re.compile(r"^/dvb/hls/([0-9a-f]{32})/([A-Za-z0-9_.-]+)$")
VIEW_PREFIX = "/dvb/v/"  # continuous recording: playlists and media of a programme, timeline or the live window
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
THUMB_DVR_SECONDS = 5.0  # one DVR list for all thumbnail requests; a deleted recording is gone within this
DETAILS_PATH = "/dvb/details"
DETAILS_IMAGE_RE = re.compile(rf"^/dvb/details/image/w342/({tmdb.POSTER_FILE})$")
EVENT_ID_RE = re.compile(r"^[A-Za-z0-9:_-]{1,128}$")  # TVHeadend event ids, "dvr:<uuid>"
IDEC_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")
CT_SCHEDULE_SECONDS = 600.0  # the CT schedule of a day, for /dvb/details?source=ct
# Ask ČT's schedule for originalTitle and search TMDb by it first (spec "Programme details", T44). Off until
# checked on the real schedule: ČT accepted the field on 2026-11-01 but was down, so no value has been seen yet
# (which programmes carry one, whether a Czech one repeats the title). Tests turn it on with fixtures.
CT_ORIGINAL_TITLE = False
CT_DAYS_BACK, CT_DAYS_AHEAD = 7, 2  # the apps' guide range (spec/apps.md "TV průvodce")
POSTER_RETRY_AFTER = 5
ARCHIVE_PATH = "/dvb/archive"
ARCHIVE_SEARCH = "/dvb/archive/search"
ARCHIVE_CATEGORIES = "/dvb/archive/categories"
ARCHIVE_CATEGORY_RE = re.compile(r"^/dvb/archive/category/(recordings|\d{4}-[a-z-]{1,40})$")
ARCHIVE_SHOW_RE = re.compile(r"^/dvb/archive/show/([^/]{1,400})$")
CT_SHOW_ID_RE = re.compile(r"^ct:([A-Za-z0-9]{1,32})$")
REC_SHOW_ID_RE = re.compile(r"^rec:([a-z0-9]{1,600})$")  # archive_catalog.card_key: ASCII only
SUBGENRE_RE = re.compile(r"^[0-9]{1,8}$")
SEARCH_MAX = 100
PAGE_RE = re.compile(r"^[1-9][0-9]{0,2}$")
YEAR_RE = re.compile(r"^[12][0-9]{3}$")
MONTH_RE = re.compile(r"^(?:[1-9]|1[0-2])$")
CT_WEB_PREFIX = "/dvb/ct/"
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
                 log: Callable[[str], None] = print, archive: Archive | None = None,
                 profiles: ProfileStore | None = None, defaults: DefaultsStore | None = None,
                 signin: SigninLog | None = None, watch: watchlog.WatchLog | None = None,
                 clock: Callable[[], float] = time.monotonic):
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
        self.profiles = profiles or ProfileStore(self.dvb.state_dir, log=log)
        self.defaults = defaults or DefaultsStore(self.dvb.state_dir, log=log)
        # connections and profile events for /admin "Přihlášení" (spec "Sign-in log"); never served on /dvb/*
        self.signin = signin or SigninLog(self.dvb.state_dir, now=now, log=log)
        # what is watched, for /admin "Sledování" (spec "Watch log"); off by default, never served on /dvb/*
        self.watch = watch or watchlog.WatchLog(self.dvb.state_dir, self.settings, now=now, clock=clock, log=log)
        # real programme starts from the broadcast's EIT (T14 phase 1: measured only), kept as long as recordings
        self.eit = EitStore(self.dvb.state_dir, lambda: self.archive.max_days(), log=log)
        if archive is None:
            store = BufferStore(self.dvb.buffer_dir)
            recorders = RecorderManager(store, self._recording_source, self.dvb.ffmpeg, self.dvb.ffprobe, log=log,
                                        eit=self.eit)
            archive = Archive(store, recorders, self.settings, self.thumbs, self.dvb.retention_days, now=now,
                              log=log, offered=self._offered_ids, is_programme=self._is_programme,
                              max_recordings=self.dvb.max_recordings,
                              allow_system_disk=self.dvb.buffer_on_system_disk, programmes=self._programmes)
        self.archive = archive
        self._worker_rid: str | None = None
        self._worker_at = float("-inf")
        self._collector_stop = threading.Event()
        self._worker_sid: str | None = None  # the worker's one recording session (spec/ad-breaks.md)
        self._worker_lock = threading.Lock()
        self._channels: list[Channel] = []
        self._channels_at = float("-inf")
        self._channels_lock = threading.Lock()
        self._thumb_dvr: list[DvrEntry] = []
        self._thumb_dvr_at = float("-inf")
        self._thumb_dvr_lock = threading.Lock()
        # TMDb: [tmdb] key, else a key entered on /admin (tmdbkeys.py); tmdb_apply() switches it on/off live
        self.tmdb_keys = tmdbkeys.KeyFile(self.dvb.state_dir)
        self.tmdb_limiter = tmdb.Limiter()  # shared by lookups and the /admin key check (the 30/min limit)
        self.tmdb_source: str | None = None
        self.details: tmdb.Details | None = None
        self.tmdb_worker: tmdbstore.TmdbWorker | None = None
        self._tmdb_key: str | None = None
        self._tmdb_lock = threading.Lock()
        self._started = False
        self.fetch_schedules = ct_api.fetch_schedules
        self._ct_schedules: dict[tuple[str, date], tuple[float, list[ct_api.Programme]]] = {}
        self._ct_lock = threading.Lock()
        self.ct_web = ctweb.CtWeb(now=now, log=log)  # /dvb/ct/* for the household web app (spec "Web")
        # the apps' Archiv (spec "Archive"); TMDb only with the key, and then the worker fills the store
        self.catalog = catalog.ArchiveCatalog(catalog.CtArchiveClient(), self._archive_recordings,
                                              store=lambda: self.details.store if self.details else None,
                                              thumbs=lambda: self.thumbs.enabled, now=now, log=log,
                                              on_listed=lambda: self.tmdb_worker and self.tmdb_worker.poke())
        self.tmdb_apply()

    def tmdb_apply(self) -> None:
        """Use the key that wins now (config, token, API key); no key = off and the store deleted. Called at start
        and after a key is saved or removed on /admin; nothing happens while the key stays the same."""
        with self._tmdb_lock:
            key, source = tmdbkeys.effective(self.cfg.tmdb.key, self.tmdb_keys.load())
            self.tmdb_source = source
            if key == self._tmdb_key:
                return
            old = self.tmdb_worker
            self.tmdb_worker = None
            if old is not None:
                old.stop()  # waits for its round, so nothing writes the store after this
            if key:
                details = tmdb.Details(key, self.dvb.state_dir, now=self.now, log=self.log,
                                       months=self.tmdb_months, limiter=self.tmdb_limiter)
                worker = tmdbstore.TmdbWorker(details.store, self.catalog.wanted,
                                              lambda query: details.refresh(query, background=True),
                                              tmdb.TmdbBusy, tmdb.TmdbError, log=self.log)
                self.details, self.tmdb_worker = details, worker
                if self._started:
                    worker.start()
            else:
                self.details = None
                tmdb.remove_cache(self.dvb.state_dir, self.log)  # no key, nothing from TMDb kept
            self._tmdb_key = key

    def tmdb_months(self) -> int:
        """``/admin`` "Obnovit údaje z TMDb po N měsících" (default 5, any positive number)."""
        return tmdbstore.clamp_months(self.settings.get(tmdbstore.SETTING, tmdbstore.DEFAULT_MONTHS))

    # -- lifecycle -------------------------------------------------------------------------
    def start(self) -> None:
        self.hls.start()
        self.retention.start()
        self.archive.start()
        threading.Thread(target=self._collect_loop, name="dvb-epg-history", daemon=True).start()
        with self._tmdb_lock:
            self._started = True
            if self.tmdb_worker is not None:
                self.tmdb_worker.start()

    def stop(self) -> None:
        self._collector_stop.set()
        with self._tmdb_lock:
            self._started = False
            if self.tmdb_worker is not None:
                self.tmdb_worker.stop()
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
                return self._worker_route(method, path, query, body)  # never in the sign-in log
            if path.startswith(PROFILE_PREFIX):
                return self._profile_route(method, path, headers, body, client)
            if path == WATCH_PATH:
                return self._watch_route(method, headers, body, client)
            if method == "GET" and path == "/dvb/status":
                self.signin.connection(client, headers)  # the only other route that reads X-Cztvhub-Client
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
            self.log(f"dvb: {logsafe.route(path)}: {logsafe.scrub(str(exc))}")  # never which channel
            return error(502, exc.user_message)
        except TvhError as exc:
            self.log(f"dvb: {logsafe.route(path)}: {logsafe.scrub(str(exc))}")  # never which channel
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
        if path.startswith(CT_WEB_PREFIX):
            return self.ct_route(path, query)
        if path == "/dvb/status":
            return self.status()
        if path == "/dvb/channels":
            return self.channels()
        if path == "/dvb/epg":
            return self.epg(query.get("date"))
        if path == "/dvb/defaults":
            return json_response(200, self.defaults.get())
        if path == DETAILS_PATH:
            return self.programme_details(query)
        if m := DETAILS_IMAGE_RE.match(path):
            return self.details_poster(m.group(1))
        if path == ARCHIVE_PATH or path.startswith(ARCHIVE_PATH + "/"):
            return self.archive_route(path, query)
        if m := HLS_RE.match(path):
            return self.media(m.group(1), m.group(2), headers.get("Range"))
        if path.startswith(VIEW_PREFIX):
            return self.view_file(path[len(VIEW_PREFIX):], headers.get("Range"))
        if m := LIVE_RE.match(path):
            return self.live(m.group(1))
        if m := RECORDING_RE.match(path):
            return self.recording(m.group(1))
        if m := TIMELINE_RE.match(path):
            return self.timeline(m.group(1), playlist=bool(m.group(2)))
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

    def _thumb_dvr_entries(self) -> list[DvrEntry]:
        """TVHeadend's DVR list for thumbnail requests, fetched at most every 5 s (prefetching TVs)."""
        with self._thumb_dvr_lock:
            if self.thumbs.clock() - self._thumb_dvr_at >= THUMB_DVR_SECONDS:
                self._thumb_dvr = self.client.dvr_entries("grid")
                self._thumb_dvr_at = self.thumbs.clock()
            return self._thumb_dvr

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

    def _programmes(self, channel: str, start: int, end: int) -> list:
        """EPG programmes of a channel overlapping [start, end): remembered ones plus TVHeadend's current ones."""
        events = [e for e in self.history.between(start, end) if e.channel == channel]
        try:
            have = {e.event_id for e in events}
            events += [e for e in self.client.epg_events(start, end) if e.channel == channel and e.event_id not in have]
        except TvhError:
            pass
        return events

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
            self.signin.trim()  # nothing older than 90 days waits for the next entry or start
            self.watch.trim()  # ended sessions written, nothing older than 30 days kept
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
                                   "thumbnails": self.thumbs.enabled, "watchLog": self.watch.enabled})

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
                self.log(f"dvb: {logsafe.session_kind(key)}: unexpected video codec {codecs.video!r}")
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

    def timeline(self, rid: str, playlist: bool) -> Response:
        """A programme's window of its channel's recording (spec/dvb-gateway.md "Channel timeline"): the playlist,
        or the JSON with its pieces, programmes and neighbours. Only for ``b…`` programmes."""
        if not parse_recording_id(rid):
            raise HttpError(404, "Nahrávka není k dispozici")
        if playlist:
            body, content_type = self._archive_call(self.archive.timeline, rid)
            return Response(200, body, content_type, {"Cache-Control": "no-cache"})
        info = self._archive_call(self.archive.timeline_info, rid)
        if self.ads.enabled:
            stored = self.breaks.ids()
            for item in info["programmes"]:
                item["breaks"] = item["id"] is not None and item["id"] in stored
        return json_response(200, info)

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
        entry = next((e for e in self._thumb_dvr_entries() if e.uuid == dvr_uuid), None)
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
        self.log("dvb: live start: " + ", ".join(parts) + f" = {total:.1f}s")  # not which channel (T37)

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

    # -- programme details (details.py) ----------------------------------------------------
    def programme_details(self, query: Mapping[str, str]) -> Response:
        """TMDb facts of a programme the gateway knows by id (spec/dvb-gateway.md "Programme details"):
        never a title a client sends."""
        if self.details is None:
            raise HttpError(404, "Podrobnosti nejsou zapnuté")
        source = query.get("source")
        if source == "dvb":
            programme = self._dvb_programme(query.get("id") or "")
        elif source == "ct":
            programme = self._ct_programme(query.get("channel") or "", query.get("date") or "",
                                           query.get("idec") or "")
        else:
            raise HttpError(400, "Parametr source musí být dvb nebo ct")
        return json_response(200, self.details.lookup(programme))

    def _dvb_programme(self, event_id: str) -> tmdb.Programme:
        if not EVENT_ID_RE.match(event_id):
            raise HttpError(400, "Chybí nebo je neplatné id pořadu")
        offered = {c.uuid for c in self._channel_list()}  # hidden channels have no programmes
        found = next((e for e in self.history.between(0, 2 ** 62) if e.event_id == event_id), None)
        if found is None:
            today = epg.broadcast_day(datetime.fromtimestamp(self.now(), timezone.utc))
            first, last = epg.allowed_days(today, max(self.dvb.retention_days, self.archive.max_days()))
            found = next((e for e in self.client.epg_events(epg.day_bounds(first)[0], epg.day_bounds(last)[1])
                          if e.event_id == event_id), None)
        if found is None:  # a past programme known only from its DVR entry (epg.build_epg ids)
            found = next((e for e in self.client.dvr_entries("grid")
                          if e.broadcast == event_id or "dvr:" + e.uuid == event_id), None)
        if found is None or found.channel not in offered:
            raise HttpError(404, "Pořad nenalezen")
        return tmdb.Programme(found.title, description=found.description, genres=tuple(found.genre))

    def _ct_programme(self, slug: str, day_param: str, idec: str) -> tmdb.Programme:
        if slug not in {c.slug for c in CT_CHANNELS}:
            raise HttpError(404, "Neznámý kanál")
        try:
            day = date.fromisoformat(day_param)
        except ValueError:
            raise HttpError(400, "Neplatné datum (YYYY-MM-DD)")
        if not IDEC_RE.match(idec):
            raise HttpError(400, "Chybí nebo je neplatný idec")
        today = epg.broadcast_day(datetime.fromtimestamp(self.now(), timezone.utc))
        if not (today - timedelta(days=CT_DAYS_BACK)) <= day <= (today + timedelta(days=CT_DAYS_AHEAD)):
            raise HttpError(400, "Datum je mimo rozsah průvodce")
        found = next((p for p in self._ct_schedule(slug, day) if p.idec == idec), None)
        if found is None:
            raise HttpError(404, "Pořad nenalezen")
        return tmdb.Programme(found.title, episode=found.episode, description=found.description, genre=found.genre,
                              original_title=found.original_title)

    def _ct_schedule(self, slug: str, day: date) -> list[ct_api.Programme]:
        """One CT channel's day, cached 10 min (the same API call as the add-on's guide)."""
        with self._ct_lock:
            hit = self._ct_schedules.get((slug, day))
            if hit and time.monotonic() - hit[0] < CT_SCHEDULE_SECONDS:
                return hit[1]
        try:
            if CT_ORIGINAL_TITLE:
                programmes = self.fetch_schedules([slug], day, original_title=True).get(slug, [])
            else:
                programmes = self.fetch_schedules([slug], day).get(slug, [])
        except ct_api.StreamError as exc:
            self.log(f"dvb: details: CT schedule: {exc}")  # not which programme was opened
            raise HttpError(502, "Program ČT se nepodařilo načíst")
        with self._ct_lock:
            now = time.monotonic()
            self._ct_schedules = {k: v for k, v in self._ct_schedules.items() if now - v[0] < CT_SCHEDULE_SECONDS}
            self._ct_schedules[(slug, day)] = (now, programmes)
        return programmes

    def details_poster(self, file: str) -> Response:
        """A poster of one of the gateway's own TMDb matches; any other file is unknown."""
        if self.details is None:
            raise HttpError(404, "Podrobnosti nejsou zapnuté")
        try:
            found = self.details.poster(file)
        except tmdb.TmdbBusy:
            return error(503, tmdb.TmdbBusy.user_message, {"Retry-After": str(POSTER_RETRY_AFTER)})
        except tmdb.TmdbError as exc:
            self.log(f"dvb: details: {exc}")
            raise HttpError(502, "Plakát se nepodařilo načíst")
        if found is None:
            raise HttpError(404, "Plakát není k dispozici")
        return Response(200, found[0], found[1], {"Cache-Control": "private, no-store"})

    # -- ČT for the web (ctweb.py) ---------------------------------------------------------
    def ct_route(self, path: str, query: Mapping[str, str]) -> Response:
        """``/dvb/ct/live|vod|schedule``: ČT's status and JSON body unchanged, at home and away."""
        try:
            found = self.ct_web.handle(path, dict(query))
        except ctweb.CtWebError as exc:
            raise HttpError(exc.status, exc.message)
        if found is None:
            raise HttpError(404, "Nenalezeno")
        status, body = found
        return Response(status, body, JSON, {"Cache-Control": "no-store"})

    # -- archive (archive_catalog.py) ------------------------------------------------------
    def archive_route(self, path: str, query: Mapping[str, str]) -> Response:
        """``/dvb/archive``, ``…/categories``, ``…/category/<slug>``, ``…/search``, ``…/show/<id>`` (spec
        "Archive")."""
        try:
            if path == ARCHIVE_PATH:
                return json_response(200, self.catalog.home())
            if path == ARCHIVE_CATEGORIES:
                return json_response(200, self.catalog.categories())
            if path == ARCHIVE_SEARCH:
                return json_response(200, self.catalog.search(_search_text(query.get("q")),
                                                              _page(query.get("page"), catalog.MAX_PAGES)))
            if m := ARCHIVE_CATEGORY_RE.match(path):
                slug = m.group(1)
                if slug != catalog.RECORDINGS and slug not in catalog.BY_SLUG:
                    raise HttpError(404, "Neznámá kategorie")
                sort = query.get("sort") or "popular"
                if sort not in catalog.SORTS:
                    raise HttpError(400, "Řazení musí být popular, newest nebo az")
                genre = query.get("genre") or None
                if genre is not None and (slug == catalog.RECORDINGS or not SUBGENRE_RE.match(genre)):
                    raise HttpError(400, "Neplatný žánr")
                limit = catalog.MAX_RECORDINGS_PAGES if slug == catalog.RECORDINGS else catalog.MAX_PAGES
                try:
                    return json_response(200, self.catalog.category(slug, sort, genre, _page(query.get("page"),
                                                                                             limit)))
                except catalog.BadInput:
                    raise HttpError(400, "Neznámý žánr kategorie")
            if m := ARCHIVE_SHOW_RE.match(path):
                return self.archive_show(urllib.parse.unquote(m.group(1)), query)
        except catalog.NotFound:
            raise HttpError(404, "Pořad nenalezen")
        except catalog.CtError as exc:
            self.log(f"dvb: archive: {exc}")
            raise HttpError(502, catalog.CtError.user_message)
        raise HttpError(404, "Nenalezeno")

    def archive_show(self, card_id: str, query: Mapping[str, str]) -> Response:
        if not (CT_SHOW_ID_RE.match(card_id) or REC_SHOW_ID_RE.match(card_id)):
            raise HttpError(400, "Neplatné id pořadu")
        season = query.get("season") or None
        if season is not None and (card_id.startswith("rec:") or not catalog.SEASON_RE.match(season)):
            raise HttpError(400, "Neplatná řada")
        page = _page(query.get("page"), catalog.MAX_EPISODE_PAGES)
        year, month = query.get("year") or None, query.get("month") or None
        if year is not None and (card_id.startswith("rec:") or not YEAR_RE.match(year)):
            raise HttpError(400, "Neplatný rok")
        if month is not None and (year is None or not MONTH_RE.match(month)):
            raise HttpError(400, "Měsíc musí být 1 až 12 a s rokem")
        try:
            return json_response(200, self.catalog.show(card_id, season, page, int(year) if year else None,
                                                        int(month) if month else None))
        except catalog.BadPeriod:
            raise HttpError(400, "Pořad nemá díly z tohoto roku nebo měsíce")
        except catalog.BadInput:
            raise HttpError(400, "Neznámá řada pořadu")

    def _archive_recordings(self) -> list[catalog.RecProgramme]:
        """Ended, recorded programmes within retention on offered channels (the continuous recording only)."""
        now = int(self.now())
        first = now - self.archive.max_days() * 86400
        try:
            names = {c.uuid: c.name for c in self._channel_list()}
        except TvhError as exc:
            self.log(f"dvb: archive: {exc}")
            return []
        recorded = {c for c in names if self.archive.is_recorded(c)}
        if not recorded:
            return []
        events = {e.event_id: e for e in self.history.between(first, now)}
        try:
            for e in self.client.epg_events(first, now):
                events.setdefault(e.event_id, e)
        except TvhError:
            pass
        out = []
        for e in events.values():
            if e.channel not in recorded or e.start < first or e.stop > now \
                    or not 0 < e.stop - e.start <= MAX_PROGRAMME:
                continue
            if self.archive.state(e.channel, e.start, e.stop) != "completed":
                continue
            out.append(catalog.RecProgramme(recording_id(e.channel, e.start, e.stop), names[e.channel], e.title,
                                            e.description, tuple(e.genre), e.start, e.stop))
        return out

    # -- profiles (profiles.py) ------------------------------------------------------------
    def _profile_route(self, method: str, path: str, headers: Mapping[str, str], body: bytes,
                       client: str = "") -> Response:
        """Settings profiles (spec/dvb-gateway.md "Profiles"). The app key must come as the bearer header:
        the cookie is only for players, and a header cannot be sent cross-site without a CORS preflight."""
        scheme, _, key = (headers.get("Authorization") or "").partition(" ")
        if scheme.lower() != "bearer" or not _same(key.strip(), self.dvb.key):
            raise HttpError(403, "Profily potřebují klíč v hlavičce Authorization")
        routes = {
            "/dvb/profiles": {"POST": self._profile_create},
            "/dvb/profiles/login": {"POST": self._profile_login},
            "/dvb/profile": {"GET": self._profile_get, "PUT": self._profile_put},
            "/dvb/profile/logout": {"POST": self._profile_logout},
        }
        if path not in routes:
            raise HttpError(404, "Nenalezeno")
        handler = routes[path].get(method)
        if handler is None:
            raise HttpError(405, "Nepodporovaná metoda")
        try:
            return handler(headers.get(PROFILE_HEADER) or "", body, headers, client)
        except ProfileError as exc:
            return json_response(exc.status, exc.payload(), exc.headers)

    def _profile_create(self, _token: str, body: bytes, headers: Mapping[str, str], client: str) -> Response:
        payload = prof.parse_body(body, prof.MAX_BODY)
        created = self.profiles.create(payload.get("name"), payload.get("password"), payload.get("settings"))
        self.signin.profile_created(created["name"], client, headers)
        return json_response(201, created)

    def _profile_login(self, _token: str, body: bytes, headers: Mapping[str, str], client: str) -> Response:
        payload = prof.parse_body(body, prof.MAX_LOGIN_BODY)
        try:
            result = self.profiles.login(payload.get("name"), payload.get("password"))
        except ProfileError as exc:
            if exc.status == 401:  # exc.profile: the existing profile; None = unknown name (text not kept)
                self.signin.login_failed(exc.profile, client, headers)
            elif exc.status == 429:
                normalized = prof.normalize_name(payload.get("name"))
                self.signin.login_locked(normalized[1] if normalized else "", exc.profile, client, headers)
            raise
        self.signin.login(result["name"], client, headers)
        return json_response(200, result)

    def _profile_get(self, token: str, _body: bytes, _headers: Mapping[str, str], _client: str) -> Response:
        return json_response(200, self.profiles.get(token))

    def _profile_put(self, token: str, body: bytes, _headers: Mapping[str, str], _client: str) -> Response:
        if len(body) > prof.MAX_BODY:
            raise ProfileError(413, "Příliš velký požadavek")
        self.profiles.get(token)  # 401 before anything about the body
        payload = prof.parse_body(body, prof.MAX_BODY)
        return json_response(200, self.profiles.put(token, payload.get("settings")))

    def _profile_logout(self, token: str, _body: bytes, headers: Mapping[str, str], client: str) -> Response:
        self.signin.logout(self.profiles.logout(token), client, headers)
        return Response(204, b"", JSON, {"Cache-Control": "no-store"})

    # -- watch log (watchlog.py) -----------------------------------------------------------
    def _watch_route(self, method: str, headers: Mapping[str, str], body: bytes, client: str) -> Response:
        """``POST /dvb/watch``: 204 whenever the key is right (off, unknown session, over a limit: dropped), 400
        only for a body that is not a report; the body is never logged or answered. The key must come as the
        bearer header: a report writes the log, and the cookie alone never changes anything (spec/web.md
        "Security", CSRF: a foreign page could otherwise forge entries)."""
        scheme, _, key = (headers.get("Authorization") or "").partition(" ")
        if scheme.lower() != "bearer" or not _same(key.strip(), self.dvb.key):
            raise HttpError(403, "Hlášení o sledování potřebuje klíč v hlavičce Authorization")
        if method != "POST":
            raise HttpError(405, "Nepodporovaná metoda")
        try:
            report = watchlog.parse_report(body)
        except watchlog.BadReport:
            raise HttpError(400, "Neplatné hlášení o sledování")
        self.watch.report(report, client, lambda: self._watch_entry(report, headers, client))
        return Response(204, b"", JSON, {"Cache-Control": "no-store"})

    def _watch_entry(self, report: Mapping[str, Any], headers: Mapping[str, str], client: str) -> dict:
        """The fixed fields of a new session, in a neutral shape: the profile from its session token, the device
        as the sign-in log, ``source``/``mode`` from the lists, the channel's name from the gateway's own
        catalogues, the programme from the gateway's own data where it has it, else the client's title."""
        entry: dict[str, Any] = {}
        token = headers.get(PROFILE_HEADER) or ""
        if token:
            try:
                entry["profile"] = self.profiles.get(token)["name"]
            except ProfileError:
                pass
        entry.update(address=client, network=signinlog_network(client))
        entry.update({k: v for k, v in client_info(headers).items() if v is not None})
        source = watchlog.neutral(report.get("source"), watchlog.SOURCES)
        mode = watchlog.neutral(report.get("mode"), watchlog.MODES)
        raw = report.get("channel") if isinstance(report.get("channel"), str) else ""
        channel, title = watchlog.UNKNOWN, None
        try:
            if source == "ct" and raw in CT_BY_ID:
                channel = CT_BY_ID[raw].name
                title = self._watch_ct_title(CT_BY_ID[raw].slug, mode, report.get("idec"))
            elif source == "psp" and raw in PSP_NAMES:
                channel = PSP_NAMES[raw]
            elif source == "dvb":
                names = {c.uuid: c.name for c in self._channel_list()}  # hidden channels are not offered
                if raw in names:
                    channel = names[raw]
                    title = self._watch_dvb_title(raw, mode, report.get("recording"))
                elif mode in ("recording", "timeline"):
                    # Archiv / Pokračovat know only the recording: its own channel and programme (T53 follow-up)
                    found = self._watch_recording(report.get("recording"))
                    if found is not None and found[0] in names:
                        channel, title = names[found[0]], found[1]
        except TvhError:
            pass  # TVHeadend down: the channel stays unknown, the client's title is kept
        entry.update(source=source, mode=mode, channel=channel)
        title = watchlog.clean_title(title) or watchlog.clean_title(report.get("title"))
        if title:
            entry["title"] = title
        return entry

    def _watch_dvb_title(self, channel: str, mode: str, recording: Any) -> str | None:
        """The guide's programme now (live) or the recording's title (recording, timeline)."""
        if mode in ("recording", "timeline"):
            found = self._watch_recording(recording)
            return found[1] if found is not None and found[0] == channel else None
        if mode != "live":
            return None
        now = int(self.now())
        return next((e.title for e in self._programmes(channel, now, now + 1) if e.start <= now < e.stop), None)

    def _watch_recording(self, recording: Any) -> tuple[str, str | None] | None:
        """(channel id, programme title or None) of a recording: a continuous-recording id ``b<channel>-<start>-
        <end>`` (title from the EPG programme with exactly those times) or a TVHeadend DVR uuid; None = unknown."""
        if not isinstance(recording, str) or not re.match(rf"^{UUID}$", recording):
            return None
        parsed = parse_recording_id(recording)
        if parsed:
            ch, start, end = parsed
            return ch, next((e.title for e in self._programmes(ch, start, end)
                             if e.start == start and e.stop == end), None)
        entry = next((e for e in self.client.dvr_entries("grid") if e.uuid == recording), None)
        return (entry.channel, entry.title) if entry is not None else None

    def _watch_ct_title(self, slug: str, mode: str, idec: Any) -> str | None:
        """Only from ČT schedules the gateway already holds (``_ct_schedules``, filled by ``/dvb/details``);
        never a request to ČT for a report. By ``idec`` when sent, else (live) the programme airing now."""
        if mode not in ("live", "startover"):
            return None
        with self._ct_lock:
            programmes = [p for (s, _day), (_at, day) in self._ct_schedules.items() if s == slug for p in day]
        if isinstance(idec, str) and IDEC_RE.match(idec):
            return next((p.title for p in programmes if p.idec == idec), None)
        if mode != "live":
            return None
        now = datetime.fromtimestamp(self.now(), timezone.utc)
        return next((p.title for p in programmes if p.start <= now < p.end), None)

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
        with self._thumb_dvr_lock:
            self._thumb_dvr_at = float("-inf")
        self.thumbs.prune(e.uuid for e in entries)
        if self.ads.enabled:
            self._prune_breaks(entries)

    def _prune_breaks(self, entries) -> None:
        removed = self.breaks.prune(e.uuid for e in entries)
        if removed:
            self.log(f"dvb: removed ad breaks of {len(removed)} deleted recording(s)")


def _page(value: str | None, last: int) -> int:
    if value is None or value == "":
        return 1
    if not PAGE_RE.match(value) or int(value) > last:
        raise HttpError(400, f"Stránka musí být 1 až {last}")
    return int(value)


def _search_text(value: str | None) -> str:
    text = " ".join((value or "").split())
    if not text:
        raise HttpError(400, "Chybí hledaný text")
    if len(text) > SEARCH_MAX or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise HttpError(400, f"Hledaný text může mít nejvýš {SEARCH_MAX} znaků")
    return text


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
