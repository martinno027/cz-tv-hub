"""Journal lines that do not say what was watched (spec/dvb-gateway.md "Sign-in log", "No per-request log").

Playback paths and TVHeadend paths carry channel, recording, programme and session ids; a log line keeps only the
route's fixed words (``/dvb/live/*.m3u8``) and the kind of session ("a live session"), never the id.
"""
from __future__ import annotations

import re

# The fixed words of the gateway's routes (gateway.py, ctweb.py, server.py); every other segment is an id.
WORDS = frozenset({
    "dvb", "ct", "live", "recording", "hls", "v", "m", "timeline", "details", "image", "w342", "vod", "schedule",
    "archive", "show", "category", "recordings", "icon", "profile", "profiles", "login", "logout", "status",
    "channels", "epg", "defaults", "ads", "jobs", "breaks", "thumb",
})
EXTENSIONS = frozenset({".m3u8", ".ts", ".m4s", ".mp4", ".jpg", ".png"})
ID_PREFIXES = ("/dvb/", "/ct/")
HEX_ID = re.compile(r"[0-9a-fA-F]{16,}")
TVH_PATH = re.compile(r"/(stream/\w+|dvrfile|imagecache)/[^\s:?]+(\?[^\s:]*)?")
LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_-]*\d[A-Za-z0-9_-]*-\d{6,}[A-Za-z0-9_-]*")  # recording ids "<ch>-<start>-<end>"
MAX_PATH = 200


def route(path: str) -> str:
    """The path without the query and with every id segment under ``/dvb/`` and ``/ct/`` as ``*`` (its media
    extension kept): ``/dvb/live/<uuid>.m3u8`` → ``/dvb/live/*.m3u8``. Other paths stay (printable, ≤ 200)."""
    path = printable(path.partition("?")[0])
    if not path.startswith(ID_PREFIXES):
        return path
    out = []
    for segment in path.split("/"):
        stem, dot, ext = segment.rpartition(".") if "." in segment else (segment, "", "")
        if segment == "" or segment in WORDS:
            out.append(segment)
        elif dot and stem in WORDS and "." + ext in EXTENSIONS:
            out.append(segment)
        elif dot and "." + ext in EXTENSIONS:
            out.append("*." + ext)
        else:
            out.append("*")
    return "/".join(out)


def scrub(text: str) -> str:
    """An error text without TVHeadend stream/recording paths and long ids (``/stream/channel/…``)."""
    text = TVH_PATH.sub(lambda m: "/" + m.group(1) + "/…", str(text))
    text = LONG_TOKEN.sub("…", text)
    return HEX_ID.sub("…", text)


def session_kind(key: str) -> str:
    """"a live session" | "a recording session" | "a session" for an HLS session key (``live:<id>``, ``rec:<id>``)."""
    if key.startswith("live:"):
        return "a live session"
    if key.startswith("rec:"):
        return "a recording session"
    return "a session"


def printable(text: str) -> str:
    """At most 200 printable ASCII characters (a path comes from the client)."""
    return "".join(c if " " < c < "\x7f" else "?" for c in text[:MAX_PATH])
