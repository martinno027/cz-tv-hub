"""Profiles: the TV apps' settings kept on the Pi for a household member (spec/dvb-gateway.md "Profiles").

Only the settings document travels (channel order/hidden/sources, the thumbnails switch), never positions,
the gateway key or anything about viewing. Passwords are stored as PBKDF2-HMAC-SHA256 with a random salt,
session tokens only as their SHA-256; neither is ever logged. ``<state dir>/profiles.json`` is written
atomically with mode 0600. Failed logins are counted per name in memory (a restart forgets them).
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import unicodedata
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

FILE = "profiles.json"
ITERATIONS = 200_000
SALT_BYTES = 16
TOKEN_BYTES = 32
MAX_PROFILES = 20
MAX_SESSIONS = 10  # per profile; the one unused longest is dropped
MAX_NAME = 32
MIN_PASSWORD, MAX_PASSWORD = 4, 64
MAX_SETTINGS = 64 * 1024  # bytes of the settings document as stored (compact UTF-8 JSON)
MAX_LOGIN_BODY = 1024
MAX_BODY = MAX_SETTINGS + 1024  # create / PUT: the document plus name, password and the wrapper
MAX_TOKEN = 128  # longer header values are not even hashed
FAIL_LIMIT, FAIL_WINDOW, LOCK_SECONDS = 5, 600.0, 60
MAX_TRACKED_NAMES = 1000  # failed-login counters kept in memory (names need not exist)
TOUCH_SECONDS = 3600  # lastUsed is written to disk at most this often per session (SD card)
HASHING_SLOTS = 2  # PBKDF2 runs at once; more logins wait instead of starving the recorders
DEFAULT_SETTINGS: dict[str, Any] = {"v": 1, "channelPrefs": {"order": [], "hidden": [], "sourcesOff": []},
                                    "thumbnails": True}

WRONG_LOGIN = "Nesprávné jméno nebo heslo"
SIGNED_OUT = "Profil byl odhlášen"
SIGNED_OUT_CODE = "profileSignedOut"  # the apps sign out only on this; a 401 of the app key has no code
TOO_MANY = "Příliš mnoho pokusů, zkuste to za minutu"
EXISTS = "Profil s tímto jménem už existuje"


class ProfileError(Exception):
    def __init__(self, status: int, message: str, headers: dict[str, str] | None = None, code: str = ""):
        super().__init__(message)
        self.status, self.message, self.headers, self.code = status, message, headers or {}, code

    def payload(self) -> dict[str, str]:
        return {"error": self.message, **({"code": self.code} if self.code else {})}


def normalize_name(raw: Any) -> tuple[str, str] | None:
    """(name as stored, uniqueness key) or None: trimmed, NFC, 1-32 code points, no control characters;
    the key is lower case without diacritics ("Jana" = "jána")."""
    if not isinstance(raw, str):
        return None
    name = unicodedata.normalize("NFC", raw.strip())
    if not 0 < len(name) <= MAX_NAME or any(unicodedata.category(c) in ("Cc", "Cs") for c in name):
        return None
    bare = "".join(c for c in unicodedata.normalize("NFD", name) if unicodedata.category(c) != "Mn")
    return name, unicodedata.normalize("NFC", bare.lower())


def normalize_password(raw: Any) -> str | None:
    """4-64 code points, NFC (a TV keyboard may send "á" decomposed); no lone surrogates (not UTF-8)."""
    if not isinstance(raw, str):
        return None
    password = unicodedata.normalize("NFC", raw)
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD or any(unicodedata.category(c) == "Cs" for c in password):
        return None
    return password


def check_settings(value: Any) -> dict:
    """Only the shape: an object with an integer ``v`` >= 1, at most 64 KB; the rest is kept as it came."""
    if not isinstance(value, dict) or isinstance(value.get("v"), bool) or not isinstance(value.get("v"), int) \
            or value["v"] < 1:
        raise ProfileError(400, 'Nastavení musí být objekt s verzí "v"')
    try:
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (UnicodeEncodeError, RecursionError, ValueError):
        raise ProfileError(400, "Neplatné nastavení")
    if size > MAX_SETTINGS:
        raise ProfileError(413, "Nastavení je příliš velké (nejvýš 64 KB)")
    return value


def parse_body(body: bytes, limit: int) -> dict:
    """A JSON object of at most ``limit`` bytes (413 above); NaN/Infinity and lone surrogates are refused."""
    if len(body) > limit:
        raise ProfileError(413, "Příliš velký požadavek")
    try:
        payload = json.loads(body.decode("utf-8"), parse_constant=_no_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        payload = None
    if not isinstance(payload, dict):
        raise ProfileError(400, "Očekávám objekt JSON")
    return payload


def _no_constant(name: str) -> Any:
    raise ValueError(name)


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ProfileStore:
    def __init__(self, state_dir: Path, iterations: int = ITERATIONS, now: Callable[[], float] = time.time,
                 clock: Callable[[], float] = time.monotonic, log: Callable[[str], None] = print):
        self.path = Path(state_dir) / FILE
        self.iterations = iterations
        self.now, self.clock, self.log = now, clock, log
        self._lock = threading.Lock()
        self._turn = threading.Condition(self._lock)  # one login attempt per name at a time
        self._trying: set[str] = set()
        self._hashing = threading.BoundedSemaphore(HASHING_SLOTS)
        self._failures: dict[str, deque[float]] = {}
        self._locked: dict[str, float] = {}
        self._unwritable = ""  # set when the file exists but cannot be read: never overwrite it then
        self._profiles: list[dict] = self._load()
        self._dummy_salt = secrets.token_bytes(SALT_BYTES)

    # -- file ------------------------------------------------------------------------------
    def _load(self) -> list[dict]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except ValueError as exc:
            aside = self.path.with_name(FILE + ".broken")
            self.log(f"dvb: {self.path} is not valid JSON ({exc}); kept as {aside.name}, starting without profiles")
            try:
                os.replace(self.path, aside)
            except OSError as err:
                self._unwritable = str(err)
            return []
        except OSError as exc:
            self.log(f"dvb: cannot read {self.path}: {exc}; profiles stay read-only")
            self._unwritable = str(exc)
            return []
        items = raw.get("profiles") if isinstance(raw, dict) else None
        return [p for p in items or [] if _well_formed(p)]

    def _save(self, profiles: list[dict]) -> None:
        if self._unwritable:
            raise OSError(self._unwritable)
        payload = json.dumps({"v": 1, "profiles": profiles}, ensure_ascii=False, separators=(",", ":"))
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

    def _commit(self, change: Callable[[list[dict]], Any]) -> Any:
        """Apply ``change`` to a copy and save it; the copy becomes the state only once it is on disk."""
        profiles = copy.deepcopy(self._profiles)
        result = change(profiles)
        try:
            self._save(profiles)
        except OSError as exc:
            self.log(f"dvb: cannot save profiles: {exc}")
            raise ProfileError(503, "Profil se nepodařilo uložit")
        self._profiles = profiles
        return result

    # -- password hashing ------------------------------------------------------------------
    def _derive(self, password: str, salt: bytes, iterations: int) -> bytes:
        with self._hashing:
            return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)

    def _new_secret(self, password: str) -> dict:
        salt = secrets.token_bytes(SALT_BYTES)
        return {"salt": salt.hex(), "iterations": self.iterations,
                "hash": self._derive(password, salt, self.iterations).hex()}

    # -- app API ---------------------------------------------------------------------------
    def create(self, name: Any, password: Any, settings: Any) -> dict:
        normalized, plain = normalize_name(name), normalize_password(password)
        if normalized is None:
            raise ProfileError(400, f"Jméno musí mít 1 až {MAX_NAME} znaků")
        if plain is None:
            raise ProfileError(400, f"Heslo musí mít {MIN_PASSWORD} až {MAX_PASSWORD} znaků")
        settings = check_settings(settings)
        secret = self._new_secret(plain)  # before the lock: slow on purpose
        token = secrets.token_urlsafe(TOKEN_BYTES)
        stamp = iso(self.now())

        def change(profiles: list[dict]) -> dict:
            self._check_free(profiles, normalized[1])
            profile = {"id": secrets.token_hex(8), "name": normalized[0], **secret, "settings": settings,
                       "version": 1, "created": stamp, "updated": stamp,
                       "sessions": [{"hash": token_hash(token), "created": stamp, "lastUsed": stamp}]}
            profiles.append(profile)
            return profile

        with self._lock:
            profile = self._commit(change)
        self.log(f"dvb: profile {profile['name']}: created")
        return {"token": token, "name": profile["name"], "settings": profile["settings"], "version": 1}

    def login(self, name: Any, password: Any) -> dict:
        normalized, plain = normalize_name(name), normalize_password(password)
        key = normalized[1] if normalized else ""
        with self._lock:
            # Attempts for one name run one after another, so parallel requests cannot pass the limit together.
            while key in self._trying:
                self._turn.wait()
            self._check_locked(key)
            self._trying.add(key)
        try:
            return self._attempt(key, normalized, name, plain)
        finally:
            with self._lock:
                self._trying.discard(key)
                self._turn.notify_all()

    def _attempt(self, key: str, normalized: tuple[str, str] | None, name: Any, plain: str | None) -> dict:
        with self._lock:
            profile = self._by_key(key) if normalized else None
            snapshot = (profile["id"], profile["hash"]) if profile else None
            salt = bytes.fromhex(profile["salt"]) if profile else self._dummy_salt
            iterations = profile["iterations"] if profile else self.iterations
        # Unknown names cost the same work as wrong passwords, so the answer time tells nothing.
        derived = self._derive(plain or "", salt, iterations).hex()
        ok = snapshot is not None and plain is not None and hmac.compare_digest(derived, snapshot[1])
        with self._lock:
            profile = self._by_id(snapshot[0]) if ok and snapshot else None
            if profile is None or profile["hash"] != snapshot[1]:  # deleted or new password meanwhile
                self._fail(key, name if normalized is None else normalized[0])
                raise ProfileError(401, WRONG_LOGIN)
            self._failures.pop(key, None)
            token = secrets.token_urlsafe(TOKEN_BYTES)
            stamp = iso(self.now())

            def change(profiles: list[dict]) -> dict:
                target = next(p for p in profiles if p["id"] == profile["id"])
                sessions = target["sessions"]
                sessions.append({"hash": token_hash(token), "created": stamp, "lastUsed": stamp})
                while len(sessions) > MAX_SESSIONS:
                    sessions.remove(min(sessions, key=lambda s: (s["lastUsed"], s["created"])))
                return target

            target = self._commit(change)
            return {"token": token, "name": target["name"], "settings": target["settings"],
                    "version": target["version"]}

    def get(self, token: str) -> dict:
        with self._lock:
            profile = self._session(token)
            return {"name": profile["name"], "settings": profile["settings"], "version": profile["version"],
                    "updated": profile["updated"]}

    def put(self, token: str, settings: Any) -> dict:
        with self._lock:
            pid = self._session(token)["id"]  # a signed-out TV hears 401 first, whatever it sent
            settings = check_settings(settings)
            stamp = iso(self.now())

            def change(profiles: list[dict]) -> dict:
                target = next(p for p in profiles if p["id"] == pid)
                target.update(settings=settings, version=target["version"] + 1, updated=stamp)
                return target

            target = self._commit(change)  # last write wins (spec): no version check
            return {"version": target["version"], "updated": target["updated"]}

    def logout(self, token: str) -> None:
        with self._lock:
            pid = self._session(token)["id"]
            digest = token_hash(token)

            def change(profiles: list[dict]) -> None:
                target = next(p for p in profiles if p["id"] == pid)
                target["sessions"] = [s for s in target["sessions"] if not hmac.compare_digest(s["hash"], digest)]

            self._commit(change)

    # -- /admin ----------------------------------------------------------------------------
    def overview(self) -> list[dict]:
        with self._lock:
            return [{"id": p["id"], "name": p["name"], "created": p["created"], "updated": p["updated"],
                     "sessions": len(p["sessions"])} for p in self._profiles]

    def admin_create(self, name: Any, password: Any) -> str:
        """A profile with the default settings and no session (the TV signs in to it)."""
        normalized, plain = normalize_name(name), normalize_password(password)
        if normalized is None:
            raise ProfileError(400, f"Jméno musí mít 1 až {MAX_NAME} znaků")
        if plain is None:
            raise ProfileError(400, f"Heslo musí mít {MIN_PASSWORD} až {MAX_PASSWORD} znaků")
        secret = self._new_secret(plain)
        stamp = iso(self.now())

        def change(profiles: list[dict]) -> None:
            self._check_free(profiles, normalized[1])
            profiles.append({"id": secrets.token_hex(8), "name": normalized[0], **secret,
                             "settings": copy.deepcopy(DEFAULT_SETTINGS), "version": 1, "created": stamp,
                             "updated": stamp, "sessions": []})

        with self._lock:
            self._commit(change)
        return normalized[0]

    def set_password(self, pid: str, password: Any) -> str:
        """A new password from the admin; every session of the profile ends."""
        plain = normalize_password(password)
        if plain is None:
            raise ProfileError(400, f"Heslo musí mít {MIN_PASSWORD} až {MAX_PASSWORD} znaků")
        with self._lock:
            if self._by_id(pid) is None:
                raise ProfileError(404, "Profil nenalezen")
        secret = self._new_secret(plain)
        with self._lock:
            def change(profiles: list[dict]) -> str:
                target = next((p for p in profiles if p["id"] == pid), None)
                if target is None:
                    raise ProfileError(404, "Profil nenalezen")
                target.update(secret, sessions=[])
                return target["name"]

            name = self._commit(change)
            key = normalize_name(name)[1]  # type: ignore[index]
            self._failures.pop(key, None)  # the household member gets fresh attempts with the new password
            self._locked.pop(key, None)
            return name

    def delete(self, pid: str) -> str:
        with self._lock:
            def change(profiles: list[dict]) -> str:
                target = next((p for p in profiles if p["id"] == pid), None)
                if target is None:
                    raise ProfileError(404, "Profil nenalezen")
                profiles.remove(target)
                return target["name"]

            return self._commit(change)

    # -- helpers (under the lock) ----------------------------------------------------------
    def _by_key(self, key: str) -> dict | None:
        return next((p for p in self._profiles if normalize_name(p["name"])[1] == key), None)  # type: ignore[index]

    def _by_id(self, pid: str) -> dict | None:
        return next((p for p in self._profiles if p["id"] == pid), None)

    @staticmethod
    def _check_free(profiles: list[dict], key: str) -> None:
        if any(normalize_name(p["name"])[1] == key for p in profiles):  # type: ignore[index]
            raise ProfileError(409, EXISTS)
        if len(profiles) >= MAX_PROFILES:
            raise ProfileError(507, f"Na bráně je už {MAX_PROFILES} profilů, nejdřív některý smažte")

    def _session(self, token: str) -> dict:
        """The profile of a valid session token (401 otherwise); notes its use, on disk at most hourly."""
        if not token or len(token) > MAX_TOKEN:
            raise ProfileError(401, SIGNED_OUT, code=SIGNED_OUT_CODE)
        digest = token_hash(token)
        for profile in self._profiles:
            for session in profile["sessions"]:
                if hmac.compare_digest(session["hash"], digest):
                    now = self.now()
                    if now - _epoch(session["lastUsed"]) >= TOUCH_SECONDS:
                        self._touch(profile["id"], digest, iso(now))
                    return profile
        raise ProfileError(401, SIGNED_OUT, code=SIGNED_OUT_CODE)

    def _touch(self, pid: str, digest: str, stamp: str) -> None:
        def change(profiles: list[dict]) -> None:
            for profile in profiles:
                for session in profile["sessions"] if profile["id"] == pid else []:
                    if session["hash"] == digest:
                        session["lastUsed"] = stamp

        try:
            self._commit(change)
        except ProfileError:
            pass  # only bookkeeping; the request itself still works

    def _check_locked(self, key: str) -> None:
        until = self._locked.get(key)
        if until is not None and self.clock() < until:
            raise ProfileError(429, TOO_MANY, {"Retry-After": str(LOCK_SECONDS)})
        self._locked.pop(key, None)

    def _fail(self, key: str, shown: Any) -> None:
        now = self.clock()
        failures = self._failures.pop(key, None) or deque()
        failures.append(now)
        while failures and failures[0] <= now - FAIL_WINDOW:
            failures.popleft()
        self._failures[key] = failures  # re-inserted: the dict is ordered by the last failure
        while len(self._failures) > MAX_TRACKED_NAMES:
            del self._failures[next(iter(self._failures))]
        if len(failures) >= FAIL_LIMIT:
            self._locked[key] = now + LOCK_SECONDS
            while len(self._locked) > MAX_TRACKED_NAMES:
                del self._locked[next(iter(self._locked))]
            self.log(f"dvb: profile login for {str(shown)[:MAX_NAME]!r}: {len(failures)} failures, "
                     f"locked for {LOCK_SECONDS} s")


def _epoch(stamp: str) -> float:
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _well_formed(p: Any) -> bool:
    """A stored profile the code can use; anything else in the file is left out (not guessed at)."""
    try:
        return isinstance(p, dict) and isinstance(p["id"], str) and normalize_name(p["name"]) is not None \
            and bytes.fromhex(p["salt"]) and isinstance(p["iterations"], int) and p["iterations"] > 0 \
            and isinstance(p["hash"], str) and isinstance(p["settings"], dict) and isinstance(p["version"], int) \
            and isinstance(p["created"], str) and isinstance(p["updated"], str) \
            and isinstance(p["sessions"], list) \
            and all(isinstance(s, dict) and isinstance(s.get("hash"), str) and isinstance(s.get("created"), str)
                    and isinstance(s.get("lastUsed"), str) for s in p["sessions"])
    except (KeyError, TypeError, ValueError):
        return False
