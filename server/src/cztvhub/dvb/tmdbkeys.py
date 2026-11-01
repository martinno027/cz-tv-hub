"""TMDb keys entered on ``/admin`` (spec/dvb-gateway.md "Programme details", T39).

The household's own TMDb credentials: a v3 "API Key" and/or a v4 "API Read Access Token". ``[tmdb] key`` in the
config wins; without it the token is used, else the API key; none = TMDb off. ``<state dir>/tmdb-keys.json`` is
written atomically with mode 0600 and read only by the service. A value never leaves this module except to
``TmdbClient``: ``/admin`` sees only ``hint()`` (the last four characters) and log lines name only which key changed.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

FILE = "tmdb-keys.json"
API_KEY = "apiKey"
TOKEN = "token"
NAMES = {API_KEY: "API Key", TOKEN: "API Read Access Token"}
API_KEY_RE = re.compile(r"^[0-9a-f]{32}$")  # TMDb v3 keys: 32 lowercase hex characters
TOKEN_RE = re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")  # a JWT
TOKEN_MAX = 1024


class KeyFormatError(ValueError):
    """A value that is not a TMDb key of that kind (the message is Czech and never contains the value)."""


def check(kind: str, value: str) -> str:
    """The trimmed value, or ``KeyFormatError`` when it cannot be that kind of TMDb key."""
    value = value.strip()
    if kind == API_KEY:
        if not API_KEY_RE.match(value):
            raise KeyFormatError("API Key má 32 znaků 0–9 a a–f (TMDb → Nastavení → API).")
    elif kind == TOKEN:
        if len(value) > TOKEN_MAX or not TOKEN_RE.match(value):
            raise KeyFormatError("API Read Access Token je dlouhý text začínající „eyJ“ (TMDb → Nastavení → API).")
    else:
        raise KeyFormatError("Neznámý druh klíče")
    return value


def hint(value: str) -> str:
    """What ``/admin`` may show of a stored value."""
    return "…" + value[-4:] if len(value) >= 12 else "…"


class KeyFile:
    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / FILE
        self._lock = threading.Lock()

    def load(self) -> dict[str, str]:
        """{"apiKey": …, "token": …} with only valid, present values (a damaged file counts as empty)."""
        with self._lock:
            return self._read()

    def _read(self) -> dict[str, str]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        keys: dict[str, str] = {}
        if isinstance(raw, dict):
            for kind in NAMES:
                value = raw.get(kind)
                if isinstance(value, str):
                    try:
                        keys[kind] = check(kind, value)
                    except KeyFormatError:
                        continue
        return keys

    def set(self, kind: str, value: str | None) -> dict[str, str]:
        """Store (value) or remove (None) one key; returns what is stored afterwards. The file goes when empty."""
        if kind not in NAMES:
            raise KeyFormatError("Neznámý druh klíče")
        with self._lock:
            keys = self._read()
            if value is None:
                keys.pop(kind, None)
            else:
                keys[kind] = check(kind, value)
            if keys:
                self._write(keys)
            else:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
            return keys

    def _write(self, keys: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(FILE + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)  # an older tmp file may have had other bits
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(keys, separators=(",", ":")))
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        os.replace(tmp, self.path)


def effective(config_key: str, stored: dict[str, str]) -> tuple[str, str | None]:
    """(key to use, source): the config's ``[tmdb] key`` first, then the token, then the API key; ("", None) = off."""
    if config_key:
        return config_key, "config"
    if stored.get(TOKEN):
        return stored[TOKEN], TOKEN
    if stored.get(API_KEY):
        return stored[API_KEY], API_KEY
    return "", None
