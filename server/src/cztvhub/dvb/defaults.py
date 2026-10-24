"""Household default channel list (spec/dvb-gateway.md "Household default channel list").

One ``channelPrefs`` document (spec/apps.md "Kanály") that TVs without a profile and without their own changes
use. Edited only on ``/admin``; "Použít jako výchozí" copies a profile's list once, nothing links the two.
``<state dir>/defaults.json`` is written atomically with mode 0600; ``version`` grows with every save, clear and
copy, to at least the current epoch seconds (a clear keeps the file with ``channelPrefs: null``), so it never goes
back and a TV applies only a newer default.
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable

from .profiles import ProfileError, iso

FILE = "defaults.json"
MAX_PREFS = 64 * 1024  # bytes of channelPrefs as stored (compact UTF-8 JSON)
MAX_BODY = MAX_PREFS + 1024  # the admin POST: the document plus its wrapper
MAX_KEYS = 500  # per list, as the apps store it
MAX_KEY = 128  # "dvb:<36-char uuid>" is the longest key in use
LISTS = ("order", "hidden", "sourcesOff")

# The Chamber of Deputies streams (spec/fixtures/psp_cases.json "streams", test-checked equal): only keys and
# names, for the /admin editor; the gateway never touches the streams themselves.
PSP_STREAMS = (
    ("psp:stream1", "Schůze PS"),
    ("psp:stream2", "Tiskové konference"),
    ("psp:stream3", "Výbory a semináře 1"),
    ("psp:stream4", "Výbory a semináře 2"),
    ("psp:stream5", "Výbory a semináře 3"),
)


def check_prefs(value: Any) -> dict:
    """``{"order": [keys], "hidden": [keys], "sourcesOff": [sources]}`` (``sourcesOff`` may be missing, as in the
    apps): lists of at most 500 short printable strings, at most 64 KB; other fields are kept as they came."""
    if not isinstance(value, dict) or not isinstance(value.get("order"), list) \
            or not isinstance(value.get("hidden"), list) or not isinstance(value.get("sourcesOff", []), list):
        raise ProfileError(400, 'Seznam kanálů musí být objekt {"order": […], "hidden": […], "sourcesOff": […]}')
    for name in LISTS:
        items = value.get(name, [])
        if len(items) > MAX_KEYS:
            raise ProfileError(400, f"Seznam {name} má víc než {MAX_KEYS} položek")
        if not all(_key(item) for item in items):
            raise ProfileError(400, f"Neplatná položka v seznamu {name}")
    try:
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (UnicodeEncodeError, RecursionError, ValueError):
        raise ProfileError(400, "Neplatný seznam kanálů")
    if size > MAX_PREFS:
        raise ProfileError(413, "Seznam kanálů je příliš velký (nejvýš 64 KB)")
    return value


def _key(item: Any) -> bool:
    return isinstance(item, str) and 0 < len(item) <= MAX_KEY \
        and not any(unicodedata.category(c) in ("Cc", "Cs") for c in item)


class DefaultsStore:
    def __init__(self, state_dir: Path, now: Callable[[], float] = time.time, log: Callable[[str], None] = print):
        self.path = Path(state_dir) / FILE
        self.now, self.log = now, log
        self._lock = threading.Lock()
        self._unwritable = ""  # set when the file exists but cannot be read: never overwrite it then
        self._doc = self._load()

    def _load(self) -> dict:
        empty = {"channelPrefs": None, "version": 0, "updated": None}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return empty
        except ValueError as exc:
            aside = self.path.with_name(FILE + ".broken")
            self.log(f"dvb: {self.path} is not valid JSON ({exc}); kept as {aside.name}, no household default")
            try:
                os.replace(self.path, aside)
            except OSError as err:
                self._unwritable = str(err)
            return empty
        except OSError as exc:
            self.log(f"dvb: cannot read {self.path}: {exc}; the household default stays read-only")
            self._unwritable = str(exc)
            return empty
        if not isinstance(raw, dict):
            return empty
        version = raw.get("version")
        version = version if isinstance(version, int) and not isinstance(version, bool) and version > 0 else 0
        try:
            prefs = check_prefs(raw.get("channelPrefs")) if raw.get("channelPrefs") is not None else None
        except ProfileError:
            prefs = None  # not guessed at; the next save replaces it with a higher version
        updated = raw.get("updated") if isinstance(raw.get("updated"), str) else None
        return {"channelPrefs": prefs, "version": version, "updated": updated}

    def _save(self, doc: dict) -> None:
        if self._unwritable:
            raise OSError(self._unwritable)
        payload = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(FILE + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)  # an older tmp file may have had other bits
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        os.replace(tmp, self.path)

    def get(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._doc)

    def set(self, prefs: Any) -> dict:
        """Save a new default (``None`` clears it); the document becomes the state only once it is on disk."""
        prefs = None if prefs is None else copy.deepcopy(check_prefs(prefs))
        with self._lock:
            now = self.now()
            # never backwards, also after a broken file was set aside (the apps apply only a newer version)
            doc = {"channelPrefs": prefs, "version": max(self._doc["version"] + 1, int(now)), "updated": iso(now)}
            try:
                self._save(doc)
            except OSError as exc:
                self.log(f"dvb: cannot save the household default: {exc}")
                raise ProfileError(503, "Výchozí seznam kanálů se nepodařilo uložit")
            self._doc = doc
            return copy.deepcopy(doc)
