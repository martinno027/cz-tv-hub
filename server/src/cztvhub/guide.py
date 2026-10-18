"""TV guide files for Kodi's PVR IPTV Simple Client, generated on the viewer's device.

The add-on's background service writes an M3U and an XMLTV file into its
profile folder; IPTV Simple reads them as local files. Every stream and
catch-up entry is a plugin:// URL, so playback still resolves on the device
(CT URLs are IP-bound) and the guide works outside the home network.
"""
from __future__ import annotations

import os
import tempfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

from . import api
from .channels import CHANNELS, Channel

ADDON_ID = "plugin.video.cztvhub"
M3U_NAME = "cztvhub.m3u"
EPG_NAME = "cztvhub-epg.xml"
DAYS_BACK = 7  # catch-up range shown in the guide
DAYS_AHEAD = 2
GROUP = "Ceska televize"


def plugin_url(path: str) -> str:
    return f"plugin://{ADDON_ID}{path}"


def build_m3u(channels: list[Channel] = CHANNELS) -> str:
    """M3U whose live and catch-up URLs call back into the add-on."""
    lines = ["#EXTM3U"]
    for ch in channels:
        catchup = plugin_url(f"/play/catchup/{ch.id}/{{utc}}/{{utcend}}?idec={{catchup-id}}")
        lines.append(
            f'#EXTINF:-1 tvg-id="{ch.tvg_id}" tvg-name="{ch.name}" tvg-chno="{ch.number}" '
            f'group-title="{GROUP}" catchup="default" catchup-days="{DAYS_BACK}" '
            f'catchup-source="{catchup}",{ch.name}'
        )
        lines.append(plugin_url(f"/play/live/{ch.id}"))
    return "\n".join(lines) + "\n"


def _xmltv_time(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def build_xmltv(schedules: dict[str, list[api.Programme]], channels: list[Channel] = CHANNELS) -> str:
    root = ET.Element("tv", {"generator-info-name": "cz-tv-hub"})
    for ch in channels:
        node = ET.SubElement(root, "channel", {"id": ch.tvg_id})
        ET.SubElement(node, "display-name", {"lang": "cs"}).text = ch.name
    for ch in channels:
        for p in schedules.get(ch.slug, []):
            node = ET.SubElement(root, "programme", {
                "start": _xmltv_time(p.start),
                "stop": _xmltv_time(p.end),
                "channel": ch.tvg_id,
                "catchup-id": p.idec,
            })
            ET.SubElement(node, "title", {"lang": "cs"}).text = p.title
            if p.episode:
                ET.SubElement(node, "sub-title", {"lang": "cs"}).text = p.episode
            if p.description:
                ET.SubElement(node, "desc", {"lang": "cs"}).text = p.description
            if p.genre:
                ET.SubElement(node, "category", {"lang": "cs"}).text = p.genre
            if p.image:
                ET.SubElement(node, "icon", {"src": p.image})
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def collect_schedules(fetch_schedules, today: date, channels: list[Channel] = CHANNELS) -> dict[str, list[api.Programme]]:
    """Programmes from DAYS_BACK days ago to DAYS_AHEAD days ahead; one request per day.

    A failing day is skipped (the future days are often not published yet);
    raises StreamError only when nothing at all could be loaded.
    """
    slugs = [c.slug for c in channels]
    merged: dict[str, dict[tuple[str, datetime], api.Programme]] = {s: {} for s in slugs}
    loaded, last_error = 0, None
    for offset in range(-DAYS_BACK, DAYS_AHEAD + 1):
        try:
            day_plan = fetch_schedules(slugs, today + timedelta(days=offset))
        except api.StreamError as exc:
            last_error = exc
            continue
        loaded += 1
        for slug, programmes in day_plan.items():
            for p in programmes:
                merged.setdefault(slug, {})[(p.idec, p.start)] = p
    if not loaded:
        raise last_error or api.StreamError("no schedule loaded")
    return {slug: sorted(items.values(), key=lambda p: p.start) for slug, items in merged.items()}


def _write_atomic(path: str, text: str) -> None:
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def write_guide(directory: str, today: date, fetch_schedules=api.fetch_schedules) -> int:
    """Write M3U_NAME and EPG_NAME into `directory`; return the number of programmes."""
    os.makedirs(directory, exist_ok=True)
    schedules = collect_schedules(fetch_schedules, today)
    _write_atomic(os.path.join(directory, EPG_NAME), build_xmltv(schedules))
    _write_atomic(os.path.join(directory, M3U_NAME), build_m3u())
    return sum(len(v) for v in schedules.values())
