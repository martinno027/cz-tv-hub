"""Updating the Pi from ``/admin`` (spec/dvb-gateway.md "Updating the Pi from /admin", T33).

The gateway never runs the update itself (it is unprivileged, ``NoNewPrivileges``). It only
- writes the request file ``<state dir>/update-request`` (content ignored), which the systemd path unit
  ``cztvhub-update.path`` watches; the oneshot ``cztvhub-update.service`` (root) then runs the fixed script
  ``server/deploy/update.sh`` of the clone ``install.sh`` was run from;
- reads ``<state dir>/update-status.json``, which that script writes (as the service user) at start and end;
- reads ``/opt/cztvhub/install.json`` (``install.sh`` writes it once the restarted service answers: the commit of
  the last install that worked) for the version line.

Nothing from the browser reaches the script: not a branch, a command, an argument or a path.
Without the path unit (an older install, a dev machine without systemd) the update is "not installed": no
request is written. What the files hold is shown in a neutral shape (unknown values stay unknown).
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REQUEST = "update-request"
STATUS = "update-status.json"
PATH_UNIT = Path("/etc/systemd/system/cztvhub-update.path")
INSTALL_INFO = Path("/opt/cztvhub/install.json")
STATES = ("running", "ok", "failed", "up-to-date")
STEPS = ("check", "pull", "install", "timeout")
MESSAGE_MAX = 2000
STATUS_MAX_BYTES = 16384
# cztvhub-update.service has TimeoutStartSec=15min (the script's own limit is 10 min): a "running" status older
# than this was killed before it could write its end, so it no longer blocks a new request.
STALE_SECONDS = 20 * 60
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")


def _time(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _epoch(value: str | None) -> float | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _commit(value: Any) -> str | None:
    return value if isinstance(value, str) and COMMIT_RE.match(value) else None


def _read_json(path: Path, limit: int) -> Any:
    """The parsed file, ``None`` when it is missing, ``...`` when it is unreadable or not JSON."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(limit + 1)
    except FileNotFoundError:
        return None
    except OSError:
        return ...
    if len(raw) > limit:
        return ...
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return ...


class Updater:
    """Request file, status file and the installed version; no own state."""

    def __init__(self, state_dir: Path, path_unit: Path = PATH_UNIT, install_info: Path = INSTALL_INFO,
                 now: Callable[[], float] = time.time):
        self.request_path = Path(state_dir) / REQUEST
        self.status_path = Path(state_dir) / STATUS
        self.path_unit, self.install_info, self.now = Path(path_unit), Path(install_info), now

    def version(self) -> dict | None:
        data = _read_json(self.install_info, 4096)
        if not isinstance(data, dict) or _commit(data.get("commit")) is None:
            return None
        return {"commit": data["commit"], "installed": _time(data.get("installed"))}

    def installed(self) -> bool:
        """The path unit is there and watches exactly our request file (a moved ``state_dir`` would not)."""
        try:
            text = self.path_unit.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        watched = [line.split("=", 1)[1].strip() for line in text.splitlines()
                   if line.strip().startswith("PathExists=")]
        return watched == [str(self.request_path)]

    def status(self) -> dict | None:
        """The last run as the script wrote it, in a neutral shape; ``None`` = never ran."""
        data = _read_json(self.status_path, STATUS_MAX_BYTES)
        if data is None:
            return None
        if not isinstance(data, dict):
            data = {}
        state = data.get("state") if data.get("state") in STATES else "unknown"
        step = data.get("step") if data.get("step") in STEPS else None
        message = data.get("message") if isinstance(data.get("message"), str) else None
        started = _time(data.get("started"))
        out = {"state": state, "started": started, "finished": _time(data.get("finished")),
               "before": _commit(data.get("before")), "after": _commit(data.get("after")), "step": step,
               "message": message[-MESSAGE_MAX:] if message is not None else None, "stale": False}
        if state == "running":
            began = _epoch(started)
            out["stale"] = began is None or self.now() - began > STALE_SECONDS
        return out

    def running(self) -> bool:
        current = self.status()
        return current is not None and current["state"] == "running" and not current["stale"]

    def overview(self) -> dict:
        return {"installed": self.installed(), "version": self.version(), "status": self.status(),
                "requested": self.request_path.exists()}

    def request(self) -> str | None:
        """Write the request file; returns the reason when it was not written."""
        if not self.installed():
            return "Aktualizace z prohlížeče není nainstalovaná – spusťte jednou bash server/deploy/install.sh."
        if self.running() or self.request_path.exists():
            return "Aktualizace už probíhá."
        stamp = datetime.fromtimestamp(self.now(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        tmp = self.request_path.with_name(REQUEST + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(f"requested {stamp}\n")
        os.replace(tmp, self.request_path)  # the path unit sees the finished file appear
        return None
