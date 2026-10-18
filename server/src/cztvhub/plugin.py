"""Navigation and playback logic of the Kodi add-on, free of any Kodi imports.

The add-on runs on the viewer's own device, so every signed URL is requested
from the device's IP and works anywhere in the Czech Republic, not only on
the home LAN. `kodi-addon/plugin.video.cztvhub/main.py` turns the results of
`Plugin.route` into Kodi list items.

Routes (paths of plugin:// URLs):
  /                               root menu
  /live                           live channels
  /play/live/<CH>                 play a channel live
  /archive                        channels for catch-up
  /archive/<CH>                   recent days
  /archive/<CH>/<YYYY-MM-DD>      programmes of one day
  /play/vod/<IDEC>?ch=&start=     play an archive item
  /play/startover/<CH>/<epoch>    play from <epoch> using the ~3 h rewind buffer
  /play/catchup/<CH>/<start>/<end>?idec=   programme picked in Kodi's TV guide (IPTV Simple)
  /info?text=                     show a message (non-playable entries)
  /guide/refresh                  rewrite the TV guide files now (done by the Kodi adapter)
"""
from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from . import api
from .channels import BY_ID, CHANNELS, Channel

ARCHIVE_DAYS = 14
TIMESHIFT_WINDOW = timedelta(hours=3)  # length of CT's "Zpet v case" buffer
START_MARGIN = timedelta(seconds=30)  # start a bit early rather than miss the beginning
WEEKDAYS = ["po", "út", "st", "čt", "pá", "so", "ne"]
DRM_HINT = "Pořad můžete pustit v oficiální aplikaci iVysílání."


@dataclass(frozen=True)
class Item:
    label: str
    path: str
    folder: bool = False
    playable: bool = False
    plot: str = ""


@dataclass(frozen=True)
class Directory:
    title: str
    items: list[Item] = field(default_factory=list)


@dataclass(frozen=True)
class Play:
    url: str
    title: str = ""
    live: bool = False
    from_buffer_start: bool = False  # open the timeshift buffer at its oldest point...
    start_offset: float = 0.0  # ...then seek this many seconds forward


@dataclass(frozen=True)
class Message:
    text: str


@dataclass(frozen=True)
class RefreshGuide:
    """Ask the Kodi adapter to regenerate the TV guide files."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _local(moment: datetime) -> datetime:
    return moment.astimezone()  # device's time zone (Prague for our viewers)


def _url(path: str, **params) -> str:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return f"{path}?{query}" if query else path


def day_label(day: date, today: date) -> str:
    if day == today:
        return "Dnes"
    if day == today - timedelta(days=1):
        return "Včera"
    return f"{WEEKDAYS[day.weekday()]} {day.day}. {day.month}."


class Plugin:
    def __init__(
        self,
        fetch_live=api.fetch_live_streams,
        fetch_vod=api.fetch_vod_url,
        fetch_schedules=api.fetch_schedules,
        now=_utc_now,
    ):
        self._fetch_live = fetch_live
        self._fetch_vod = fetch_vod
        self._fetch_schedules = fetch_schedules
        self._now = now

    def route(self, path: str, params: dict[str, str] | None = None):
        params = params or {}
        parts = [p for p in path.split("/") if p]
        try:
            if not parts:
                return self.root()
            if parts == ["live"]:
                return self.live_channels()
            if parts == ["archive"]:
                return self.archive_channels()
            if parts[0] == "archive" and len(parts) == 2:
                return self.archive_days(self._channel(parts[1]))
            if parts[0] == "archive" and len(parts) == 3:
                return self.archive_day(self._channel(parts[1]), date.fromisoformat(parts[2]))
            if parts[:2] == ["play", "live"] and len(parts) == 3:
                return self.play_live(self._channel(parts[2]))
            if parts[:2] == ["play", "vod"] and len(parts) == 3:
                return self.play_vod(parts[2], params.get("ch"), params.get("start"))
            if parts[:2] == ["play", "startover"] and len(parts) == 4:
                return self.play_startover(self._channel(parts[2]), int(parts[3]))
            if parts[:2] == ["play", "catchup"] and len(parts) == 5:
                return self.play_catchup(self._channel(parts[2]), int(parts[3]), int(parts[4]), params.get("idec"))
            if parts == ["guide", "refresh"]:
                return RefreshGuide()
            if parts == ["info"]:
                return Message(params.get("text") or "")
        except api.StreamError as exc:
            if exc.status == "UNSUPPORTED_DRM":
                return Message(f"{exc.user_message}. {DRM_HINT}")
            return Message(exc.user_message)
        except (KeyError, ValueError):
            pass
        return Message("Neznámá položka")

    @staticmethod
    def _channel(channel_id: str) -> Channel:
        return BY_ID[channel_id]

    # --- menus -------------------------------------------------------------

    def root(self) -> Directory:
        return Directory("ČT", [
            Item("Živě", "/live", folder=True),
            Item("Zpětně (archiv iVysílání)", "/archive", folder=True),
            Item("Aktualizovat TV průvodce", "/guide/refresh",
                 plot="Znovu stáhne program pro TV průvodce Kodi (PVR IPTV Simple Client). "
                      "Jinak se aktualizuje sám každých 6 hodin."),
        ])

    def live_channels(self) -> Directory:
        now = self._now()
        try:
            schedules = self._fetch_schedules([c.slug for c in CHANNELS], _local(now).date())
        except api.StreamError:
            schedules = {}  # channel names alone are still useful
        items = []
        for ch in CHANNELS:
            label, plot = ch.name, ""
            current = next((p for p in schedules.get(ch.slug, []) if p.start <= now < p.end), None)
            if current:
                label = f"{ch.name}  ·  {current.title}"
                plot = f"{_local(current.start):%H:%M}–{_local(current.end):%H:%M}  {current.title}"
            items.append(Item(label, f"/play/live/{ch.id}", playable=True, plot=plot))
        return Directory("Živě", items)

    def archive_channels(self) -> Directory:
        return Directory("Zpětně", [Item(c.name, f"/archive/{c.id}", folder=True) for c in CHANNELS])

    def archive_days(self, channel: Channel) -> Directory:
        today = _local(self._now()).date()
        items = []
        for back in range(ARCHIVE_DAYS):
            day = today - timedelta(days=back)
            items.append(Item(day_label(day, today), f"/archive/{channel.id}/{day.isoformat()}", folder=True))
        return Directory(channel.name, items)

    def archive_day(self, channel: Channel, day: date) -> Directory:
        now = self._now()
        programmes = self._fetch_schedules([channel.slug], day).get(channel.slug, [])
        items = [self._programme_item(channel, p, now) for p in programmes]
        return Directory(f"{channel.name} – {day_label(day, _local(now).date())}", items)

    def _programme_item(self, channel: Channel, p: api.Programme, now: datetime) -> Item:
        start = int(p.start.timestamp())
        label = f"{_local(p.start):%H:%M}  {p.title}"
        plot = f"{_local(p.start):%H:%M}–{_local(p.end):%H:%M}  {p.title}"
        if now < p.start:
            return Item(f"{label}  (zatím nevysíláno)", _url("/info", text="Pořad ještě nezačal"), plot=plot)
        if now < p.end:
            return Item(f"{label}  (právě běží)", f"/play/startover/{channel.id}/{start}", playable=True, plot=plot)
        if now - p.start < TIMESHIFT_WINDOW:
            # Began less than 3 h ago: always play it from CT's "Zpet v case" buffer (decided by time only).
            return Item(label, f"/play/startover/{channel.id}/{start}", playable=True, plot=plot)
        if p.playable:
            return Item(label, _url(f"/play/vod/{p.idec}", ch=channel.id, start=start), playable=True, plot=plot)
        # Not playable: still route to the API so the viewer gets CT's reason.
        return Item(f"{label}  (nedostupné)", f"/play/vod/{p.idec}", playable=True, plot=plot)

    # --- playback ----------------------------------------------------------

    def play_live(self, channel: Channel) -> Play:
        return Play(self._fetch_live(channel.id).main, channel.name, live=True)

    def play_vod(self, idec: str, channel_id: str | None = None, start: str | None = None):
        try:
            return Play(self._fetch_vod(idec))
        except api.StillLive as exc:
            channel = BY_ID.get(channel_id or exc.channel_id)
            if channel is None:
                raise
            if start:
                return self.play_startover(channel, int(start))
            return self.play_live(channel)

    def play_catchup(self, channel: Channel, start_epoch: int, end_epoch: int, idec: str | None):
        now = self._now()
        start = datetime.fromtimestamp(start_epoch, timezone.utc)
        # Airing, or began less than 3 h ago: "Zpet v case" buffer (decided by time only).
        if now.timestamp() < end_epoch or now - start < TIMESHIFT_WINDOW:
            return self.play_startover(channel, start_epoch)
        if idec and not idec.startswith("{"):  # IPTV Simple leaves the placeholder if unknown
            return self.play_vod(idec, channel.id, str(start_epoch))
        return Message("Pořad není v archivu")

    def play_startover(self, channel: Channel, start_epoch: int):
        streams = self._fetch_live(channel.id)
        if not streams.timeshift:
            return Message("Zpět v čase není pro tento kanál k dispozici")
        start = datetime.fromtimestamp(start_epoch, timezone.utc)
        buffer_start = self._now() - TIMESHIFT_WINDOW
        offset = max(0.0, (start - START_MARGIN - buffer_start).total_seconds())
        return Play(streams.timeshift, channel.name, live=True, from_buffer_start=True, start_offset=offset)
