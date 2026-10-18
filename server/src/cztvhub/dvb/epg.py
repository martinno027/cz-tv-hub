"""Prague broadcast days and the gateway's EPG JSON (events merged with DVR entries)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .tvheadend import DvrEntry, EpgEvent

PRAGUE = ZoneInfo("Europe/Prague")
DAY_START = time(5, 0)

# ETSI EN 300 468 content_nibble_level_1
GENRES = {
    0x1: "Film / drama",
    0x2: "Zprávy",
    0x3: "Zábava",
    0x4: "Sport",
    0x5: "Pro děti",
    0x6: "Hudba",
    0x7: "Kultura",
    0x8: "Společnost",
    0x9: "Vzdělávání / věda",
    0xA: "Volný čas",
}


def broadcast_day(moment: datetime) -> date:
    """The Prague broadcast day (05:00 to 05:00 local) a moment belongs to."""
    local = moment.astimezone(PRAGUE)
    return (local - timedelta(hours=DAY_START.hour, minutes=DAY_START.minute)).date()


def day_bounds(day: date) -> tuple[int, int]:
    """Epoch seconds [start, end) of a broadcast day; DST-aware (23 or 25 h days)."""
    start = datetime.combine(day, DAY_START, PRAGUE)
    end = datetime.combine(day + timedelta(days=1), DAY_START, PRAGUE)
    return int(start.timestamp()), int(end.timestamp())


def allowed_days(today: date, retention_days: int) -> tuple[date, date]:
    return today - timedelta(days=retention_days), today + timedelta(days=2)


def recording_state(sched_status: str) -> str:
    """Map TVHeadend's dvr_entry_schedstatus() strings to the gateway's four states."""
    s = (sched_status or "").strip()
    if s.startswith("recording"):
        return "recording"
    if s.startswith("scheduled"):
        return "scheduled"
    if s == "completed":
        return "completed"
    # completedError, completedRerecord, and completedWarning (TVHeadend uses it only for
    # missed recordings whose service was disabled), unknown, ...
    return "failed"


def genre_text(genres: list[int]) -> str:
    for code in genres:
        text = GENRES.get((code >> 4) & 0xF)
        if text:
            return text
    return ""


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_epg(events: list[EpgEvent], entries: list[DvrEntry], start: int, end: int) -> list[dict]:
    """Events overlapping [start, end). TVHeadend forgets EPG events once they end, so past
    programmes come from DVR entries (id ``dvr:<uuid>`` when the EPG event is gone)."""
    by_uuid = {e.uuid: e for e in entries}
    by_broadcast = {e.broadcast: e for e in entries if e.broadcast}
    out: list[dict] = []
    seen: set[str] = set()

    for ev in events:
        if not (ev.stop > start and ev.start < end):
            continue
        entry = by_uuid.get(ev.dvr_uuid) or by_broadcast.get(ev.event_id)
        recording = None
        if entry is not None:
            seen.add(entry.uuid)
            recording = {"id": entry.uuid, "state": recording_state(entry.sched_status or ev.dvr_state)}
        elif ev.dvr_uuid:
            seen.add(ev.dvr_uuid)
            recording = {"id": ev.dvr_uuid, "state": recording_state(ev.dvr_state)}
        out.append(_event_json(ev.event_id, ev.channel, ev.title, ev.subtitle, ev.description, ev.genre,
                               ev.start, ev.stop, recording))

    for entry in entries:
        if entry.uuid in seen or not entry.channel or not (entry.stop > start and entry.start < end):
            continue
        out.append(_event_json(entry.broadcast or "dvr:" + entry.uuid, entry.channel, entry.title, entry.subtitle,
                               entry.description, entry.genre, entry.start, entry.stop,
                               {"id": entry.uuid, "state": recording_state(entry.sched_status)}))

    out.sort(key=lambda e: (e["channel"], e["start"]))
    return out


def _event_json(event_id: str, channel: str, title: str, subtitle: str, description: str, genre: list[int],
                start: int, stop: int, recording: dict | None) -> dict:
    return {
        "id": event_id,
        "channel": channel,
        "title": title,
        "subtitle": subtitle,
        "description": description,
        "genre": genre_text(genre),
        "start": iso(start),
        "end": iso(stop),
        "recording": recording,
    }
