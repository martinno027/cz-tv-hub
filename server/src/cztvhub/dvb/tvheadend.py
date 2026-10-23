"""Minimal TVHeadend HTTP API client (TVHeadend 4.2 / 4.3 JSON API).

Endpoints used (field names checked against TVHeadend's src/api/*.c and the
web UI's requests; the API is not formally versioned, so parsing is defensive):
- POST /api/channel/grid            channels: uuid, name, number, enabled, icon_public_url, services[];
                                    disabled channels only with all=1 and an admin user (api_channel.c)
- POST /api/mpegts/service/grid     services: uuid, enabled, svcname, multiplex, multiplex_uuid (admin)
- POST /api/mpegts/mux/grid         multiplexes: uuid, name, enabled (int: -1 ignore, 0 disable, 1 enable; admin)
                                    both with hidemode=none: by default (api_mpegts.c) the service grid leaves
                                    out services of disabled multiplexes, which the admin page must re-enable
- POST /api/epg/events/grid         EPG: eventId, channelUuid, start, stop, title, subtitle,
                                    summary, description, genre[], dvrUuid, dvrState
- POST /api/dvr/entry/grid          DVR entries: uuid, channel, start, stop, disp_title, disp_subtitle,
                                    disp_summary, disp_description, broadcast, sched_status, genre[], filesize
- POST /api/dvr/entry/remove        uuid=[...]  delete finished recordings incl. files
- POST /api/dvr/entry/cancel        uuid=[...]  drop scheduled entries, abort running recordings
- POST /api/status/inputs           tuners: input, subs, stream (needs an admin TVHeadend user)
- POST /api/dvr/config/grid, /api/dvr/autorec/grid, /api/dvr/autorec/create (conf={...}),
  /api/idnode/save (node={...}), /api/idnode/delete (uuid=[...])   used by ``dvb-setup``
- GET  /stream/channel/<uuid>?profile=pass   live MPEG-TS; 503 when no subscription can be created,
                                             but a subscription without a free tuner just ends
                                             without data (webui.c: SMT_NOSTART), see gateway.py
- GET  /dvrfile/<uuid>                       recording file, supports Range ("bytes=N-"); a start at
                                             or past the end gets a non-206 reply without the data
- GET  /imagecache/<n>                       channel icons
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

GRID_LIMIT = 100000


class TvhError(Exception):
    """TVHeadend failed or is unreachable (gateway answers 502)."""

    user_message = "TVHeadend není dostupný"


class TunerBusy(TvhError):
    user_message = "Tuner je obsazený"


class TvhNotFound(TvhError):
    user_message = "Nenalezeno"


@dataclass
class Channel:
    uuid: str
    name: str
    number: int
    icon: str = ""  # TVHeadend-relative ("imagecache/3") or absolute URL, "" = none
    enabled: bool = True
    services: list[str] = field(default_factory=list)


@dataclass
class EpgEvent:
    event_id: str
    channel: str
    title: str
    subtitle: str
    description: str
    genre: list[int]
    start: int
    stop: int
    dvr_uuid: str = ""
    dvr_state: str = ""


@dataclass
class DvrEntry:
    uuid: str
    channel: str
    title: str
    subtitle: str
    description: str
    genre: list[int]
    start: int
    stop: int
    sched_status: str
    broadcast: str = ""  # EPG event id, "" once TVHeadend expired the event
    filesize: int = 0


@dataclass
class Tuner:
    name: str
    in_use: bool


@dataclass
class Autorec:
    uuid: str
    channel: str
    comment: str
    raw: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class Service:
    uuid: str
    name: str
    enabled: bool
    mux: str  # multiplex uuid, "" if unknown
    mux_name: str = ""


MUX_IGNORE, MUX_DISABLE, MUX_ENABLE = -1, 0, 1


@dataclass
class Mux:
    uuid: str
    name: str
    enabled: int  # MUX_IGNORE / MUX_DISABLE / MUX_ENABLE (older TVHeadend: a boolean)


def _text(value: Any) -> str:
    """TVHeadend gives either plain strings or language maps like {"cze": "..."}."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for lang in ("cze", "ces", "cs", "eng", "en"):
            if isinstance(value.get(lang), str):
                return value[lang]
        for item in value.values():
            if isinstance(item, str):
                return item
    return ""


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _genres(value: Any) -> list[int]:
    if isinstance(value, list):
        return [g for g in (_int(v, -1) for v in value) if g >= 0]
    return []


def _id(value: Any) -> str:
    if value in (None, "", 0, "0"):
        return ""
    return str(value)


def _entries(data: Any) -> list[dict]:
    entries = data.get("entries") if isinstance(data, dict) else None
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _ids(value: Any) -> list[str]:
    return [str(v) for v in value if v] if isinstance(value, list) else []


def parse_channels(data: Any, include_disabled: bool = False) -> list[Channel]:
    channels = []
    for entry in _entries(data):
        enabled = entry.get("enabled") is not False
        if not entry.get("uuid") or not (enabled or include_disabled):
            continue
        icon = entry.get("icon_public_url") or ""
        channels.append(Channel(str(entry["uuid"]), _text(entry.get("name")) or "?", _int(entry.get("number")),
                                icon if isinstance(icon, str) else "", enabled, _ids(entry.get("services"))))
    channels.sort(key=lambda c: (c.number <= 0, c.number, c.name.lower()))
    return channels


def parse_events(data: Any) -> list[EpgEvent]:
    events = []
    for e in _entries(data):
        start, stop = _int(e.get("start")), _int(e.get("stop"))
        if not e.get("eventId") or not e.get("channelUuid") or stop <= start:
            continue
        events.append(EpgEvent(
            event_id=str(e["eventId"]),
            channel=str(e["channelUuid"]),
            title=_text(e.get("title")),
            subtitle=_text(e.get("subtitle")),
            description=_text(e.get("description")) or _text(e.get("summary")),
            genre=_genres(e.get("genre")),
            start=start,
            stop=stop,
            dvr_uuid=_id(e.get("dvrUuid")),
            dvr_state=_text(e.get("dvrState")),
        ))
    return events


def parse_dvr_entries(data: Any) -> list[DvrEntry]:
    entries = []
    for e in _entries(data):
        if not e.get("uuid"):
            continue
        entries.append(DvrEntry(
            uuid=str(e["uuid"]),
            channel=_id(e.get("channel")),
            title=_text(e.get("disp_title")) or _text(e.get("title")),
            subtitle=_text(e.get("disp_subtitle")) or _text(e.get("subtitle")),
            description=(_text(e.get("disp_description")) or _text(e.get("description"))
                         or _text(e.get("disp_summary")) or _text(e.get("summary"))),
            genre=_genres(e.get("genre")),
            start=_int(e.get("start")),
            stop=_int(e.get("stop")),
            sched_status=_text(e.get("sched_status")),
            broadcast=_id(e.get("broadcast")),
            filesize=_int(e.get("filesize")),
        ))
    return entries


def parse_inputs(data: Any) -> list[Tuner]:
    return [Tuner(_text(e.get("input")) or "?", _int(e.get("subs")) > 0) for e in _entries(data)]


def parse_autorecs(data: Any) -> list[Autorec]:
    return [Autorec(str(e["uuid"]), _id(e.get("channel")), _text(e.get("comment")), e, e.get("enabled") is not False)
            for e in _entries(data) if e.get("uuid")]


def parse_services(data: Any) -> list[Service]:
    return [Service(str(e["uuid"]), _text(e.get("svcname")) or _text(e.get("name")), e.get("enabled") is not False,
                    _id(e.get("multiplex_uuid")), _text(e.get("multiplex")))
            for e in _entries(data) if e.get("uuid")]


def parse_muxes(data: Any) -> list[Mux]:
    muxes = []
    for e in _entries(data):
        if not e.get("uuid"):
            continue
        value = e.get("enabled")
        enabled = (MUX_ENABLE if value else MUX_DISABLE) if isinstance(value, bool) else _int(value, MUX_ENABLE)
        muxes.append(Mux(str(e["uuid"]), _text(e.get("name")), enabled))
    return muxes


Opener = Callable[..., Any]


class TvheadendClient:
    def __init__(self, url: str, user: str = "", password: str = "", timeout: float = 10.0,
                 opener: Opener | None = None):
        self.base = url.rstrip("/")
        self.timeout = timeout
        self._auth_header = ""
        if user:
            token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
            # Basic is sent up front (TVHeadend's "plain" / "both" auth modes); the digest
            # handler takes over when TVHeadend only accepts digest.
            self._auth_header = "Basic " + token
        if opener is None:
            manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            manager.add_password(None, self.base, user, password)
            opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(manager)).open
        self._open = opener

    # -- transport -------------------------------------------------------------------------
    def _request(self, path: str, data: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
                 timeout: float | None = None):
        body = None
        if data is not None:
            fields = {k: v if isinstance(v, str) else json.dumps(v) for k, v in data.items()}
            body = urllib.parse.urlencode(fields).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=body, headers=dict(headers or {}))
        if self._auth_header:
            request.add_header("Authorization", self._auth_header)
        try:
            return self._open(request, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code == 503:
                raise TunerBusy(f"{path}: 503") from exc
            if exc.code == 404:
                raise TvhNotFound(f"{path}: 404") from exc
            raise TvhError(f"{path}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise TvhError(f"{path}: {exc}") from exc

    def api(self, path: str, data: dict[str, Any] | None = None) -> Any:
        with self._request("/api/" + path, data if data is not None else {}) as response:
            raw = response.read()
        try:
            return json.loads(raw.decode("utf-8")) if raw.strip() else {}
        except (UnicodeDecodeError, ValueError) as exc:
            raise TvhError(f"/api/{path}: invalid JSON") from exc

    # -- read ------------------------------------------------------------------------------
    def channels(self, include_disabled: bool = False) -> list[Channel]:
        args = {"start": "0", "limit": str(GRID_LIMIT)}
        if include_disabled:
            args["all"] = "1"  # else TVHeadend leaves disabled channels out
        return parse_channels(self.api("channel/grid", args), include_disabled)

    def services(self) -> list[Service]:
        return parse_services(self.api("mpegts/service/grid",
                                       {"start": "0", "limit": str(GRID_LIMIT), "hidemode": "none"}))

    def muxes(self) -> list[Mux]:
        return parse_muxes(self.api("mpegts/mux/grid", {"start": "0", "limit": str(GRID_LIMIT), "hidemode": "none"}))

    def epg_events(self, start: int, stop: int) -> list[EpgEvent]:
        """Events overlapping [start, stop) (epoch seconds). TVHeadend drops events once they end."""
        filters = [
            {"field": "stop", "type": "numeric", "value": start, "comparison": "gt"},
            {"field": "start", "type": "numeric", "value": stop, "comparison": "lt"},
        ]
        data = self.api("epg/events/grid", {"start": "0", "limit": str(GRID_LIMIT), "filter": filters})
        return [e for e in parse_events(data) if e.stop > start and e.start < stop]

    def dvr_entries(self, grid: str = "grid") -> list[DvrEntry]:
        """``grid`` is one of grid, grid_upcoming, grid_finished, grid_failed."""
        return parse_dvr_entries(self.api("dvr/entry/" + grid, {"start": "0", "limit": str(GRID_LIMIT)}))

    def tuners(self) -> list[Tuner]:
        return parse_inputs(self.api("status/inputs"))

    def dvr_configs(self) -> list[dict]:
        return _entries(self.api("dvr/config/grid", {"start": "0", "limit": str(GRID_LIMIT)}))

    def autorecs(self) -> list[Autorec]:
        return parse_autorecs(self.api("dvr/autorec/grid", {"start": "0", "limit": str(GRID_LIMIT)}))

    # -- write -----------------------------------------------------------------------------
    def remove_dvr_entries(self, uuids: list[str]) -> None:
        if uuids:
            self.api("dvr/entry/remove", {"uuid": uuids})

    def cancel_dvr_entries(self, uuids: list[str]) -> None:
        if uuids:
            self.api("dvr/entry/cancel", {"uuid": uuids})

    def save_node(self, node: dict[str, Any] | list[dict[str, Any]]) -> None:
        self.api("idnode/save", {"node": node})

    def delete_nodes(self, uuids: list[str]) -> None:
        if uuids:
            self.api("idnode/delete", {"uuid": uuids})

    def create_autorec(self, conf: dict[str, Any]) -> None:
        self.api("dvr/autorec/create", {"conf": conf})

    # -- media -----------------------------------------------------------------------------
    def open_live(self, channel_uuid: str):
        """Live MPEG-TS response. Raises TunerBusy on 503."""
        path = "/stream/channel/" + urllib.parse.quote(channel_uuid) + "?profile=pass"
        return self._request(path, timeout=30)

    def open_recording(self, dvr_uuid: str, offset: int = 0):
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        return self._request("/dvrfile/" + urllib.parse.quote(dvr_uuid), headers=headers)

    def fetch_icon(self, icon: str) -> tuple[bytes, str]:
        if icon.startswith(("http://", "https://")):
            if not icon.startswith(self.base + "/"):
                raise TvhNotFound("external icon")
            icon = icon[len(self.base) + 1:]
        with self._request("/" + icon.lstrip("/")) as response:
            return response.read(), response.headers.get("Content-Type", "image/png")
