"""ČT for the household web app: ``/dvb/ct/live|vod|schedule`` (spec/dvb-gateway.md "Web", "ČT for the web").

A browser cannot call ČT's API from a page that is not ``ceskatelevize.cz`` (CORS), so the gateway asks the same
API the apps ask, with exactly the request of ``api.py``, and hands back **ČT's status and JSON body unchanged**.
Stream answers (signed URLs) only go to clients on the home LAN: never to the tailnet or anything public
(legal-guard 2026-11-01; ČT's own IP check is not relied upon). This is metadata only: video never passes through.

The signed URLs are never logged and never written; they live at most 60 s in this module's memory.
"""
from __future__ import annotations

import ipaddress
import json
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from .. import api as ct_api
from ..channels import BY_ID, CHANNELS
from .epg import broadcast_day

LIVE_RE = re.compile(r"^/dvb/ct/live/([^/]*)$")
VOD_RE = re.compile(r"^/dvb/ct/vod/([^/]*)$")
SCHEDULE_PATH = "/dvb/ct/schedule"
# Real idecs are digits only: on 2026-11-01 the whole schedule feed (6 channels, 10 days, 2 200 programmes) had
# 15 or 11 digits, the archive's episodes too (160 checked). The range leaves room without allowing anything else.
IDEC_RE = re.compile(r"^[0-9]{8,20}$")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
SLUGS = tuple(c.slug for c in CHANNELS)  # ČT's GraphQL channel names (ct1, ct2, ct24, sport, decko, art)
MAX_CHANNELS = len(SLUGS)
DAYS_BACK, DAYS_AHEAD = 7, 2  # the apps' guide range (spec/apps.md "TV průvodce")
STREAM_SECONDS = 60.0  # signed URLs: at most 60 s, memory only (CLAUDE.md "Gotchas")
SCHEDULE_SECONDS = 600.0
MAX_ENTRIES = 200  # per cache; idecs are open-ended
MAX_BYTES = 4 * 1024 * 1024  # a whole day of six channels is far below this
TIMEOUT = 10.0

NOT_HOME = "ČT jde v prohlížeči přehrát jen doma. Mimo domov použijte aplikaci Televize nebo iVysílání."
UNREACHABLE = "iVysílání teď neodpovídá"

# Home LAN: RFC 1918, link-local, IPv6 ULA and loopback. The tailnet (CGNAT range and Tailscale's ULA prefix) is
# excluded explicitly: it lies inside fc00::/7, and "home only" is the gateway's rule, not ČT's.
HOME_V4 = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                                                  "169.254.0.0/16", "127.0.0.0/8"))
HOME_V6 = tuple(ipaddress.ip_network(n) for n in ("fc00::/7", "fe80::/10", "::1/128"))
TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


class CtWebError(Exception):
    """An answer of the gateway itself (not ČT's): status + Czech text."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class Unreachable(Exception):
    """ČT did not answer with JSON (network error, timeout, too large, not JSON). The text carries no URL."""


def home_lan(peer: str) -> bool:
    """Whether the peer address is on the home LAN (not the tailnet, not public). IPv4-mapped IPv6 counts as
    its IPv4 address; a zone id (``fe80::1%eth0``) is ignored; anything unparsable is not home."""
    try:
        addr = ipaddress.ip_address(peer.split("%", 1)[0])
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if addr.version == 4:
        return addr not in TAILNET_V4 and any(addr in net for net in HOME_V4)
    return addr not in TAILNET_V6 and any(addr in net for net in HOME_V6)


def fetch_raw(url: str, body: dict | None = None, timeout: float = TIMEOUT) -> tuple[int, bytes]:
    """ČT's status and body as they came; only our honest User-Agent, nothing of the client's request."""
    headers = {"User-Agent": ct_api.USER_AGENT, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, _read(response)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, _read(exc)
        finally:
            exc.close()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise Unreachable(type(exc).__name__) from None  # str(exc) can name the host; the URL never


def _read(response) -> bytes:
    data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise Unreachable(f"larger than {MAX_BYTES} bytes")
    return data


class CtWeb:
    """The three routes. ``fetch(url, body)`` → (status, bytes); tests pass a fake, never the network."""

    def __init__(self, fetch: Callable[..., tuple[int, bytes]] = fetch_raw, now: Callable[[], float] = time.time,
                 clock: Callable[[], float] = time.monotonic, log: Callable[[str], None] = print,
                 today: Callable[[float], date] | None = None):
        self.fetch = fetch
        self.now = now
        self.clock = clock
        self.log = log
        self.today = today or _broadcast_day
        self._lock = threading.Lock()
        self._streams: dict[str, tuple[float, int, bytes]] = {}
        self._schedules: dict[tuple[tuple[str, ...], date], tuple[float, int, bytes]] = {}

    def handle(self, path: str, query: dict, client: str) -> tuple[int, bytes] | None:
        """(status, body) for a ``/dvb/ct/*`` path, None when the path is not one of them."""
        if m := LIVE_RE.match(path):
            channel = m.group(1)
            if channel not in BY_ID:
                raise CtWebError(404, "Neznámý kanál ČT")
            self._home_only(client)
            return self._stream("live", ct_api.LIVE_URL.format(channel=channel))  # the log names no channel
        if m := VOD_RE.match(path):
            idec = m.group(1)
            if not IDEC_RE.match(idec):
                raise CtWebError(400, "Neplatný idec")
            self._home_only(client)
            return self._stream("vod", ct_api.VOD_URL.format(idec=idec))  # nor the programme
        if path == SCHEDULE_PATH:
            return self._schedule(query.get("channels"), query.get("date"))
        return None

    @staticmethod
    def _home_only(client: str) -> None:
        if not home_lan(client):
            raise CtWebError(403, NOT_HOME)

    def _stream(self, what: str, url: str) -> tuple[int, bytes]:
        return self._cached(self._streams, url, STREAM_SECONDS, what, lambda: self.fetch(url))

    def _schedule(self, channels: str | None, day_param: str | None) -> tuple[int, bytes]:
        slugs = (channels or "").split(",")
        if not 1 <= len(slugs) <= MAX_CHANNELS or any(s not in SLUGS for s in slugs) or len(set(slugs)) < len(slugs):
            raise CtWebError(400, "Kanály: 1 až 6 z " + ", ".join(SLUGS))
        if not DATE_RE.match(day_param or ""):
            raise CtWebError(400, "Neplatné datum (YYYY-MM-DD)")
        try:
            day = date.fromisoformat(day_param)
        except ValueError:
            raise CtWebError(400, "Neplatné datum (YYYY-MM-DD)") from None
        today = self.today(self.now())
        if not today - timedelta(days=DAYS_BACK) <= day <= today + timedelta(days=DAYS_AHEAD):
            raise CtWebError(400, "Datum je mimo rozsah průvodce")
        body = ct_api.schedule_request(slugs, day)
        return self._cached(self._schedules, (tuple(slugs), day), SCHEDULE_SECONDS,
                            f"schedule {','.join(slugs)} {day.isoformat()}",
                            lambda: self.fetch(ct_api.GRAPHQL_URL, body))

    def _cached(self, cache: dict, key, ttl: float, what: str, call: Callable[[], tuple[int, bytes]]):
        with self._lock:
            hit = cache.get(key)
            if hit and self.clock() - hit[0] < ttl:
                return hit[1], hit[2]
        try:
            status, data = call()
            json.loads(data.decode("utf-8"))  # only JSON is passed on; the bytes stay exactly as ČT sent them
        except (Unreachable, UnicodeDecodeError, ValueError) as exc:
            reason = str(exc) if isinstance(exc, Unreachable) else "not JSON"
            self.log(f"dvb: ct web: {what}: {reason}")
            raise CtWebError(502, UNREACHABLE) from None
        if status < 500:  # ČT's answers incl. its 403/404 texts; a server error is asked again next time
            with self._lock:
                now = self.clock()
                for k in [k for k, v in cache.items() if now - v[0] >= ttl]:
                    del cache[k]
                while len(cache) >= MAX_ENTRIES:
                    del cache[min(cache, key=lambda k: cache[k][0])]
                cache[key] = (now, status, data)
        return status, data


def _broadcast_day(now: float) -> date:
    return broadcast_day(datetime.fromtimestamp(now, timezone.utc))
