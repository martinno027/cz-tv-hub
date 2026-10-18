"""Client for Ceska televize's public stream and programme APIs.

These are the same endpoints the official iVysilani web player uses. They
return short-lived, signed HLS URLs (bound to the requester's IP). We never
touch the video data ourselves: clients play the signed URL straight from
CT's CDN.

Kept compatible with Kodi's Python (3.8+) because the Kodi add-on bundles it.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime

API_BASE = "https://api.ceskatelevize.cz"
STREAM_PARAMS = (
    "canPlayDrm=false&quality=web&streamType=hls&origin=ivysilaniweb"
    "&usePlayability=true&clientVersion=0.0.0"
)
LIVE_URL = API_BASE + "/video/v1/playlist-live/v1/stream-data/channel/{channel}?" + STREAM_PARAMS
VOD_URL = API_BASE + "/video/v1/playlist-vod/v1/stream-data/media/external/{idec}?" + STREAM_PARAMS
GRAPHQL_URL = API_BASE + "/graphql/?client=iVysilaniWeb&version=1.131.1&use-new-playability=true"
USER_AGENT = "Mozilla/5.0 (compatible; cz-tv-hub/0.2)"

SCHEDULE_QUERY = """
query($channels: [String!]!, $date: Date!) {
  TVProgramDailyChannelsPlanV2(channels: $channels, date: $date) {
    channel
    program { idec title episodeTitle description genre imageUrl start end isPlayableNow liveOnly }
  }
}
"""


class StreamError(Exception):
    """The API did not give us a playable stream URL.

    `user_message` is a short Czech text for end users (CT's own wording when
    the API provides one); `status` is CT's status name, e.g. "UNSUPPORTED_DRM".
    """

    def __init__(self, message: str, user_message: str = "Stream se nepodařilo načíst", status: str = ""):
        super().__init__(message)
        self.user_message = user_message
        self.status = status


class StillLive(StreamError):
    """The requested archive item is airing right now; play the channel instead."""

    def __init__(self, idec: str, channel_id: str):
        super().__init__(f"{idec} is airing live on {channel_id}", "Pořad se právě vysílá")
        self.channel_id = channel_id


@dataclass(frozen=True)
class LiveStreams:
    main: str
    timeshift: str | None  # ~3 h rewind buffer ("Zpet v case"), if offered


@dataclass(frozen=True)
class Programme:
    idec: str
    title: str
    start: datetime  # timezone-aware
    end: datetime
    playable: bool  # available in the archive right now
    live_only: bool
    episode: str = ""
    description: str = ""
    genre: str = ""
    image: str = ""


def _request_json(url: str, timeout: float, body: dict | None = None):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise _error_from_http(exc) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise StreamError(f"request to {url.split('?')[0]} failed: {exc}") from exc


def _error_from_http(exc: urllib.error.HTTPError) -> StreamError:
    """Turn an HTTP error into a StreamError, keeping CT's explanation if present."""
    status, text = "", ""
    try:
        message = (json.load(exc) or {}).get("message") or {}
        status = (message.get("status") or {}).get("name") or ""
        text = (message.get("labels") or {}).get("textShort") or ""
    except (ValueError, AttributeError, OSError):
        pass
    detail = f"HTTP {exc.code} {status}".strip()
    return StreamError(detail, text or "Video není k dispozici", status)


def fetch_live_streams(channel_id: str, timeout: float = 10.0) -> LiveStreams:
    """Ask the CT API for fresh signed HLS URLs of a live channel."""
    data = _request_json(LIVE_URL.format(channel=channel_id), timeout)
    urls = (data or {}).get("streamUrls") or {}
    if urls.get("isBlocked"):
        raise StreamError(f"{channel_id} is blocked (geo restriction?)", "Vysílání je blokováno")
    main = urls.get("main")
    if not main:
        raise StreamError(f"{channel_id}: no stream URL in API response")
    return LiveStreams(main=main, timeshift=urls.get("timeshift") or None)


def fetch_stream_url(channel_id: str, timeout: float = 10.0) -> str:
    """Fresh signed HLS master-playlist URL of a live channel."""
    return fetch_live_streams(channel_id, timeout).main


def fetch_vod_url(idec: str, timeout: float = 10.0) -> str:
    """Fresh signed HLS URL of an archive item (iVysilani), identified by its IDEC."""
    data = _request_json(VOD_URL.format(idec=idec), timeout) or {}
    for stream in data.get("streams") or []:
        if stream.get("url"):
            return stream["url"]
    if data.get("platformChannel"):
        raise StillLive(idec, data["platformChannel"])
    raise StreamError(f"{idec}: no stream URL in API response", "Video není k dispozici")


def _parse_time(value: str) -> datetime:
    # Python < 3.11 (Kodi) does not accept a trailing "Z".
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fetch_schedules(slugs: list[str], day: date, timeout: float = 10.0) -> dict[str, list[Programme]]:
    """Programme of the given broadcast day for channels identified by EPG slug (e.g. "ct1")."""
    body = {"query": SCHEDULE_QUERY, "variables": {"channels": slugs, "date": day.strftime("%m.%d.%Y")}}
    data = _request_json(GRAPHQL_URL, timeout, body) or {}
    plans = (data.get("data") or {}).get("TVProgramDailyChannelsPlanV2")
    if plans is None:
        raise StreamError(f"schedule query failed: {data.get('errors')}", "Program se nepodařilo načíst")
    result: dict[str, list[Programme]] = {}
    for plan in plans:
        programmes = []
        for item in plan.get("program") or []:
            try:
                programmes.append(Programme(
                    idec=str(item["idec"]),
                    title=item.get("title") or "",
                    start=_parse_time(item["start"]),
                    end=_parse_time(item["end"]),
                    playable=bool(item.get("isPlayableNow")),
                    live_only=bool(item.get("liveOnly")),
                    episode=item.get("episodeTitle") or "",
                    description=item.get("description") or "",
                    genre=item.get("genre") or "",
                    image=item.get("imageUrl") or "",
                ))
            except (KeyError, TypeError, ValueError, AttributeError):
                continue  # skip malformed entries, keep the rest
        result[plan.get("channel")] = programmes
    return result


class StreamResolver:
    """Resolves channel ids to stream URLs with a small TTL cache.

    Signed URLs live about an hour; a short TTL keeps API traffic low while
    always handing clients a URL with plenty of validity left.
    """

    def __init__(self, fetch=fetch_stream_url, ttl: float = 60.0, clock=time.monotonic):
        self._fetch = fetch
        self._ttl = ttl
        self._clock = clock
        self._cache: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def resolve(self, channel_id: str) -> str:
        with self._lock:
            hit = self._cache.get(channel_id)
            if hit and self._clock() - hit[0] < self._ttl:
                return hit[1]
        url = self._fetch(channel_id)
        with self._lock:
            self._cache[channel_id] = (self._clock(), url)
        return url
