"""Weather for Domů: ``GET /dvb/weather`` (spec/dvb-gateway.md "Weather", T59, approved by the user 2026-11-14).

Only the gateway talks to Open-Meteo (forecast, air quality and pollen), from the Pi's address, fixed hosts, no
redirects, size caps. A TV may send its location rounded on the device; the gateway accepts only numbers within
Czechia and surroundings, rounds them again to 0.1° and sends Open-Meteo nothing but those rounded coordinates, at
most ``NEW_PER_HOUR`` places per hour and one request at a time. Without a device location it uses the household
place typed on ``/admin`` (``HomePlace``, the only coordinates on disk, 0600); with neither, weather is off.
Answers are cached in memory per rounded place for 30 min. **Coordinates never appear in a log line, an error text
or an exception**: log lines name only what failed (``forecast``, ``air quality``) and how (an HTTP status or an
exception's type).

Texts, icons, AQI words and pollen levels follow ``spec/fixtures/weather_cases.json`` (a test compares the tables);
the apps show them as they come. ČHMÚ warnings come from ``warnings.py``, matched to the place on the Pi.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Callable, Mapping

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
USER_AGENT = "cz-tv-hub (household gateway; https://github.com/martinno027/cz-tv-hub)"
TIMEZONE = "Europe/Prague"
FORECAST_DAYS = 7
CURRENT = "temperature_2m,weather_code,wind_speed_10m"
DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset"
POLLEN = ("alder", "birch", "grass", "mugwort", "ragweed")
AIR_CURRENT = "european_aqi," + ",".join(f"{s}_pollen" for s in POLLEN)
MAX_BYTES = 512 * 1024
TIMEOUT = 10.0
CACHE_SECONDS = 1800.0  # a place's answer is fresh this long
STALE_SECONDS = 3 * 3600.0  # an older answer is still served when Open-Meteo fails or the limit is reached
MAX_PLACES = 50
NEW_PER_HOUR = 20  # distinct places sent to Open-Meteo in any hour
WAIT_SECONDS = 25.0  # for the one request at a time
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = 48.0, 51.5, 11.5, 19.5
NUMBER_RE = re.compile(r"^[+-]?[0-9]{1,3}(?:\.[0-9]{1,20})?$")
FILE = "home-place.json"

OFF = "Počasí není zapnuté"
UNAVAILABLE = "Počasí teď není k dispozici"
OUTSIDE = "Poloha mimo Česko"
ATTRIBUTION = "Počasí: Open-Meteo.com (CC BY 4.0), CAMS · Výstrahy: ČHMÚ (CC BY 4.0)"

# WMO weather code -> (Czech text, icon key); an unknown code: no text, icon "cloud" (weather_cases.json "wmo")
WMO: dict[int, tuple[str, str]] = {
    0: ("Jasno", "sun"), 1: ("Skoro jasno", "sun"), 2: ("Polojasno", "partly"), 3: ("Zataženo", "cloud"),
    45: ("Mlha", "fog"), 48: ("Mlha s námrazou", "fog"),
    51: ("Slabé mrholení", "drizzle"), 53: ("Mrholení", "drizzle"), 55: ("Silné mrholení", "drizzle"),
    56: ("Mrznoucí mrholení", "drizzle"), 57: ("Silné mrznoucí mrholení", "drizzle"),
    61: ("Slabý déšť", "rain"), 63: ("Déšť", "rain"), 65: ("Silný déšť", "rain"),
    66: ("Mrznoucí déšť", "rain"), 67: ("Silný mrznoucí déšť", "rain"),
    71: ("Slabé sněžení", "snow"), 73: ("Sněžení", "snow"), 75: ("Silné sněžení", "snow"), 77: ("Sněhová zrna", "snow"),
    80: ("Slabé přeháňky", "rain"), 81: ("Přeháňky", "rain"), 82: ("Silné přeháňky", "rain"),
    85: ("Sněhové přeháňky", "snow"), 86: ("Silné sněhové přeháňky", "snow"),
    95: ("Bouřka", "storm"), 96: ("Bouřka s kroupami", "storm"), 99: ("Silná bouřka s kroupami", "storm"),
}
UNKNOWN_ICON = "cloud"
# European AQI from which a word is shown, highest first (weather_cases.json "aqi")
AQI_TEXTS = ((100, "mimořádně špatné"), (80, "velmi špatné"), (60, "špatné"), (40, "zhoršené"))
POLLEN_NAMES = {"alder": "olše", "birch": "bříza", "grass": "tráva", "mugwort": "pelyněk", "ragweed": "ambrozie"}
# grains/m³: exclusive upper bounds of žádná / nízká / střední; at or above the last: vysoká
POLLEN_LEVELS = {"alder": (1, 10, 100), "birch": (1, 10, 100), "grass": (1, 5, 30), "mugwort": (1, 5, 30),
                 "ragweed": (1, 5, 30)}
LEVEL_NAMES = ("nízká", "střední", "vysoká")


class WeatherError(Exception):
    """Open-Meteo failed or answered something unusable. The text never carries the URL or a coordinate."""


# -- rules (weather_cases.json) ------------------------------------------------------------------
def round_place(lat: Any, lon: Any) -> tuple[float, float] | None:
    """Both coordinates rounded to 0.1° (half away from zero), or None when either is not a finite number or lies
    outside lat 48.0–51.5 / lon 11.5–19.5. A string must be a plain decimal ("50.08"), as from a query."""
    a, b = _coordinate(lat), _coordinate(lon)
    if a is None or b is None or not (LAT_MIN <= a <= LAT_MAX and LON_MIN <= b <= LON_MAX):
        return None
    return _round(a), _round(b)


def _coordinate(value: Any) -> float | None:
    if isinstance(value, str):
        if not NUMBER_RE.match(value.strip()):
            return None
        value = float(value.strip())
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _round(value: float) -> float:
    # repr gives the shortest decimal that is this float: 49.95 is "49.95", rounded up as written, not as stored
    return float(Decimal(repr(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def wmo(code: Any) -> tuple[str | None, str]:
    """(text, icon) for a WMO weather code."""
    found = WMO.get(code) if isinstance(code, int) and not isinstance(code, bool) else None
    return found if found is not None else (None, UNKNOWN_ICON)


def aqi_text(value: Any) -> str | None:
    number = _number(value)
    if number is None:
        return None
    return next((text for bound, text in AQI_TEXTS if number >= bound), None)


def pollen_level(species: str, value: Any) -> str | None:
    """"nízká" | "střední" | "vysoká", or None for "žádná" (and for a missing value or an unknown species)."""
    bounds, number = POLLEN_LEVELS.get(species), _number(value)
    if bounds is None or number is None or number < bounds[0]:
        return None
    return next((name for bound, name in zip(bounds[1:], LEVEL_NAMES) if number < bound), LEVEL_NAMES[-1])


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


# -- answers from Open-Meteo ---------------------------------------------------------------------
def forecast_part(data: Any) -> dict:
    """``now``, ``sun`` and ``days`` from Open-Meteo's forecast answer; WeatherError when it lacks them."""
    current = data.get("current") if isinstance(data, dict) else None
    daily = data.get("daily") if isinstance(data, dict) else None
    if not isinstance(current, dict) or not isinstance(daily, dict) or not isinstance(daily.get("time"), list):
        raise WeatherError("forecast: unexpected answer")
    code = current.get("weather_code")
    text, icon = wmo(code)
    now = {"temperature": _number(current.get("temperature_2m")), "code": code if _int(code) else None,
           "text": text, "icon": icon, "wind": _number(current.get("wind_speed_10m"))}

    def column(name: str) -> list:
        values = daily.get(name)
        return values if isinstance(values, list) else []

    dates, codes = column("time"), column("weather_code")
    highs, lows, rain = column("temperature_2m_max"), column("temperature_2m_min"), \
        column("precipitation_probability_max")
    days = []
    for i, day in enumerate(dates[:FORECAST_DAYS]):
        if not isinstance(day, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
            continue
        code = codes[i] if i < len(codes) and _int(codes[i]) else None
        text, icon = wmo(code)
        days.append({"date": day, "code": code, "text": text, "icon": icon,
                     "max": _number(highs[i]) if i < len(highs) else None,
                     "min": _number(lows[i]) if i < len(lows) else None,
                     "rainChance": _number(rain[i]) if i < len(rain) else None})
    if not days:
        raise WeatherError("forecast: no days")
    sun = {"rise": _clock(column("sunrise")), "set": _clock(column("sunset"))}
    return {"now": now, "sun": sun, "days": days}


def air_part(data: Any) -> dict:
    """``air`` from Open-Meteo's air-quality answer: AQI, its word, pollen above "žádná"."""
    current = data.get("current") if isinstance(data, dict) else None
    if not isinstance(current, dict):
        raise WeatherError("air quality: unexpected answer")
    aqi = _number(current.get("european_aqi"))
    pollen = []
    for species in POLLEN:
        level = pollen_level(species, current.get(f"{species}_pollen"))
        if level is not None:
            pollen.append({"name": POLLEN_NAMES[species], "level": level})
    return {"aqi": round(aqi) if aqi is not None else None, "aqiText": aqi_text(aqi), "pollen": pollen}


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _clock(values: list) -> str | None:
    """"HH:MM" of today's (the first) local ISO time."""
    m = re.match(r"^\d{4}-\d{2}-\d{2}T(\d{2}:\d{2})", values[0]) if values and isinstance(values[0], str) else None
    return m.group(1) if m else None


# -- the household place (/admin) -----------------------------------------------------------------
class HomePlace:
    """``<state dir>/home-place.json`` ``{"lat": 50.1, "lon": 14.4}``, rounded, 0600, written atomically. The only
    coordinates the gateway keeps on disk; never logged."""

    def __init__(self, state_dir: Path, log: Callable[[str], None] = print):
        self.path = Path(state_dir) / FILE
        self.log = log
        self._lock = threading.Lock()
        self._place = self._load()

    def _load(self) -> tuple[float, float] | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            self.log(f"dvb: {self.path.name} unreadable ({type(exc).__name__}); no household place")
            return None
        place = round_place(raw.get("lat"), raw.get("lon")) if isinstance(raw, dict) else None
        if place is None:
            self.log(f"dvb: {self.path.name} has no usable place; no household place")
        return place

    def get(self) -> tuple[float, float] | None:
        with self._lock:
            return self._place

    def set(self, lat: Any, lon: Any) -> tuple[float, float] | None:
        """Round and save; None (nothing saved) when the place is not valid. OSError when the disk refuses."""
        place = round_place(lat, lon)
        if place is None:
            return None
        payload = json.dumps({"lat": place[0], "lon": place[1]})
        with self._lock:
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
            self._place = place
        return place

    def remove(self) -> bool:
        """Delete the place; whether there was one."""
        with self._lock:
            had = self._place is not None or self.path.exists()
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self._place = None
            return had


# -- the route --------------------------------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Fixed hosts: a redirect elsewhere is an error, not followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


class Weather:
    """``/dvb/weather``: validation, the household fallback, the cache, the limit and the answer."""

    def __init__(self, home: HomePlace, warnings: Callable[[float, float], list[dict]] | None = None,
                 urlopen: Callable[..., Any] | None = None, clock: Callable[[], float] = time.monotonic,
                 log: Callable[[str], None] = print, on_use: Callable[[], None] | None = None):
        self.home = home
        self.warnings = warnings or (lambda lat, lon: [])
        self._urlopen = urlopen or _OPENER.open
        self.clock, self.log = clock, log
        self.on_use = on_use or (lambda: None)  # the warnings loop: weather is in use
        self._cache: OrderedDict[tuple[float, float], tuple[float, dict]] = OrderedDict()
        self._sent: dict[tuple[float, float], float] = {}  # place -> when last sent to Open-Meteo
        self._cache_lock = threading.Lock()
        self._fetch_lock = threading.Lock()  # one request at a time

    def answer(self, query: Mapping[str, str]) -> tuple[int, dict]:
        """(status, body). Only ``lat`` and ``lon`` of the query are read."""
        if "lat" in query or "lon" in query:
            place = round_place(query.get("lat"), query.get("lon"))
            if place is None:
                return 400, {"error": OUTSIDE}
            source = "device"
        else:
            place = self.home.get()
            if place is None:
                return 404, {"error": OFF}
            source = "household"
        self.on_use()
        head = {"place": {"lat": place[0], "lon": place[1], "source": source}}
        part = self._part(place)
        if part is None:
            return 200, {**head, "error": UNAVAILABLE}
        return 200, {**head, **part, "warnings": self.warnings(*place), "attribution": ATTRIBUTION}

    def _part(self, place: tuple[float, float]) -> dict | None:
        """The cached or fetched weather of a place (fresh, else stale), or None."""
        cached = self._cached(place, CACHE_SECONDS)
        if cached is not None:
            return cached
        if not self._fetch_lock.acquire(timeout=WAIT_SECONDS):
            return self._cached(place, STALE_SECONDS)
        try:
            cached = self._cached(place, CACHE_SECONDS)  # fetched by another request meanwhile
            if cached is not None:
                return cached
            if not self._may_send(place):
                return self._cached(place, STALE_SECONDS)
            try:
                part = self._fetch(place)
            except WeatherError as exc:
                self.log(f"weather: {exc}")
                return self._cached(place, STALE_SECONDS)
            with self._cache_lock:
                self._cache[place] = (self.clock(), part)
                self._cache.move_to_end(place)
                while len(self._cache) > MAX_PLACES:
                    self._cache.popitem(last=False)
            return part
        finally:
            self._fetch_lock.release()

    def _cached(self, place: tuple[float, float], max_age: float) -> dict | None:
        with self._cache_lock:
            found = self._cache.get(place)
            if found is None or self.clock() - found[0] >= max_age:
                return None
            self._cache.move_to_end(place)
            return found[1]

    def _may_send(self, place: tuple[float, float]) -> bool:
        """At most NEW_PER_HOUR distinct places sent in the last hour; a place sent within it costs nothing new.
        Counts the place as sent when allowed (also when the request then fails: the coordinates went out)."""
        now = self.clock()
        with self._cache_lock:
            for key in [k for k, at in self._sent.items() if now - at >= 3600.0]:
                del self._sent[key]
            if place not in self._sent and len(self._sent) >= NEW_PER_HOUR:
                self.log("weather: limit of new places per hour reached")
                return False
            self._sent[place] = now
            return True

    def _fetch(self, place: tuple[float, float]) -> dict:
        lat, lon = place
        common = {"latitude": f"{lat:.1f}", "longitude": f"{lon:.1f}", "timezone": TIMEZONE}
        part = forecast_part(self._get(FORECAST_URL, {**common, "forecast_days": str(FORECAST_DAYS),
                                                      "current": CURRENT, "daily": DAILY}, "forecast"))
        try:
            part["air"] = air_part(self._get(AIR_URL, {**common, "current": AIR_CURRENT}, "air quality"))
        except WeatherError as exc:
            self.log(f"weather: {exc}")
            part["air"] = None
        return part

    def _get(self, base: str, params: dict[str, str], what: str) -> Any:
        request = urllib.request.Request(base + "?" + urllib.parse.urlencode(params),
                                         headers={"Accept": "application/json", "User-Agent": USER_AGENT})
        try:
            with self._urlopen(request, timeout=TIMEOUT) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise WeatherError(f"{what}: HTTP {status}")
                body = response.read(MAX_BYTES + 1)
        except WeatherError:
            raise
        except urllib.error.HTTPError as exc:
            exc.close()
            raise WeatherError(f"{what}: HTTP {exc.code}") from None
        except urllib.error.URLError as exc:  # the reason's type only: its text could quote the request
            raise WeatherError(f"{what}: {_reason(exc.reason)}") from None
        except (OSError, ValueError) as exc:
            raise WeatherError(f"{what}: {type(exc).__name__}") from None
        if len(body) > MAX_BYTES:
            raise WeatherError(f"{what}: larger than {MAX_BYTES} bytes")
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise WeatherError(f"{what}: not JSON") from None


def _reason(reason: object) -> str:
    """urllib's reason without its text (it can quote the request): the exception's type, or "unreachable"."""
    return type(reason).__name__ if isinstance(reason, BaseException) else "unreachable"
