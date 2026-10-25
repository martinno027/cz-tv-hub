"""Web administration at ``/admin``: choose which antenna channels the household is offered.

A hidden channel is switched off in TVHeadend itself, so TVHeadend is the only state:
- the channel is disabled: apps no longer get it (``/dvb/channels``, EPG, live, recordings);
- its services are disabled, and a multiplex is disabled once none of its channels is offered,
  so the tuner does not tune to it at all (no reception, no EPG scan);
- its cztvhub autorec rule is disabled (``dvb-setup`` keeps it disabled), its scheduled and running
  recordings are cancelled and its finished recordings deleted.
Channels can be renamed (TVHeadend name, ``autoname`` off so a rescan keeps it; the service name stays the
original, shown less prominently on the page); ``short_name()`` suggests a TV-style name ("CT 1 PHA HD T2" -> "ČT1", matching the CT internet channels).
Showing the channel again reverses the switches (recordings start with the next programme); showing an
already offered channel repairs its reception (services / multiplex switched off elsewhere).

The tab "Výchozí kanály" edits the household default channel list (``defaults.py``); "Použít jako výchozí" on
the tab "Profily" copies a profile's list into it once. The tab "TMDb" shows the TMDb store (``tmdbstore.py``)
read-only and paginated, with refresh / delete per entry, delete all and the refresh period; every change is
logged (``admin: tmdb: …``). The store is never downloadable.

Login: HTTP Basic, user ``admin``, ``[admin] password``. State-changing requests must carry the
``X-Cztvhub-Admin`` header (a cross-site page cannot send it without a CORS preflight, which this
server never grants) and a same-origin ``Origin`` if the browser sends one.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import json
import re
import time
from typing import Any, Callable, Mapping

from .. import channels as ct_channels
from ..config import Config
from . import defaults as dflt
from . import details as tmdb
from . import epg
from . import tmdbstore
from .gateway import DvbGateway, Response, error, json_response
from .profiles import ProfileError, parse_body
from .setup import RULE_COMMENT, apply_setup, plan_setup, recording_set_up
from .tvheadend import MUX_DISABLE, MUX_ENABLE, Channel, TvheadendClient, TvhError, TvhNotFound

ADMIN_USER = "admin"
CHANNEL_RE = re.compile(r"^/admin/api/channels/([A-Za-z0-9_-]{1,64})$")
PROFILE_RE = re.compile(r"^/admin/api/profiles/([0-9a-f]{16})$")
TMDB_PATH = "/admin/api/tmdb"
TMDB_KEY_MAX = 600  # a store key: ["kind", year, "cleaned title"]
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_BULK = 500
MAX_NAME = 64
TECH_TOKENS = {"HD", "SD", "UHD", "4K", "T2", "HEVC", "H265", "H.265", "DVB-T2"}
CT_REGIONS = {"PHA", "PRAHA", "JM", "SM", "BRNO", "OSTRAVA"}  # regional variants of ČT1


def short_name(name: str) -> str:
    """TV-style channel name: no "HD"/"T2" suffixes, "CT 1 PHA" -> "ČT1" like the CT internet channels."""
    tokens = [t for t in name.split() if t.upper() not in TECH_TOKENS]
    if tokens and tokens[0].upper() in ("CT", "ČT"):
        rest = [t for t in tokens[1:] if t.upper() not in CT_REGIONS]
        if rest and rest[0].isdigit():
            tokens = ["ČT" + rest[0]] + rest[1:]
        else:
            tokens = ["ČT"] + rest
    return " ".join(tokens) or name


def clean_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())
    return name if 0 < len(name) <= MAX_NAME and name.isprintable() else None
CSRF_HEADER = "X-Cztvhub-Admin"
HTML = "text/html; charset=utf-8"
PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                               "connect-src 'self'; img-src 'self'; frame-ancestors 'none'; form-action 'none'",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


class ChannelAdmin:
    """Channel switches against TVHeadend; no own state."""

    def __init__(self, cfg: Config, client: TvheadendClient, log: Callable[[str], None] = print,
                 now: Callable[[], float] = time.time):
        self.cfg, self.client, self.log, self.now = cfg, client, log, now

    def overview(self) -> list[dict]:
        channels = self.client.channels(include_disabled=True)
        services = {s.uuid: s for s in self.client.services()}
        muxes = {m.uuid: m for m in self.client.muxes()}
        rules = [r for r in self.client.autorecs() if r.comment == RULE_COMMENT]
        recordings: dict[str, int] = {}
        for entry in self.client.dvr_entries("grid") + self.client.dvr_entries("grid_finished") + \
                self.client.dvr_entries("grid_failed"):
            recordings[entry.channel] = recordings.get(entry.channel, 0) + 1
        moment = int(self.now())
        airing = {e.channel: e for e in self.client.epg_events(moment, moment + 1)}
        rows = []
        for channel in channels:
            own = [services[s] for s in channel.services if s in services]
            mux_ids = sorted({s.mux for s in own if s.mux})
            rule = next((r for r in rules if r.channel == channel.uuid), None)
            event = airing.get(channel.uuid)
            # TVHeadend names a channel after its service; a different name was set on rename (autoname off)
            original = next((s.name for s in own if s.name), "")
            rows.append({
                "id": channel.uuid,
                "number": channel.number,
                "name": channel.name,
                "shortName": short_name(channel.name),
                "originalName": original if original and original != channel.name else None,
                "offered": channel.enabled,
                # every service scrambled (CA): cannot be recorded (free-to-air only, nothing is descrambled)
                "scrambled": bool(own) and all(s.encrypted for s in own),
                # tuned at all: an enabled service in an enabled multiplex
                "received": any(s.enabled and muxes.get(s.mux) is not None and muxes[s.mux].enabled == MUX_ENABLE
                                for s in own),
                "multiplexes": [muxes[m].name if m in muxes else next(s.mux_name for s in own if s.mux == m)
                                for m in mux_ids],
                "recordingRule": None if rule is None else rule.enabled,
                "recordings": recordings.get(channel.uuid, 0),
                # what is on right now (EPG); none for channels whose multiplex is not received
                "now": None if event is None else {"title": event.title, "subtitle": event.subtitle,
                                                   "genre": epg.genre_text(event.genre),
                                                   "start": epg.iso(event.start), "end": epg.iso(event.stop)},
            })
        # stable order like a TV's channel list (hidden channels stay in place, greyed out)
        rows.sort(key=lambda r: (r["number"] <= 0, r["number"], r["name"].casefold()))
        return rows

    def set_offered(self, channel_ids: str | list[str], offered: bool) -> list[str]:
        """Show or hide one or more channels at once; returns what changed, in Czech, for the page.

        Unknown ids fail before anything changes (``TvhNotFound``)."""
        ids = [channel_ids] if isinstance(channel_ids, str) else list(dict.fromkeys(channel_ids))
        channels = self.client.channels(include_disabled=True)
        by_id = {c.uuid: c for c in channels}
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise TvhNotFound(", ".join(missing))
        targets = [by_id[i] for i in ids]
        done: list[str] = []
        switch = [c for c in targets if c.enabled != offered]
        if switch:
            self.client.save_node([{"uuid": c.uuid, "enabled": offered} for c in switch])
            word = "zapnut" if offered else "vypnut"
            done.append(f"kanál {word}" if len(switch) == 1 else f"kanály {word}y: {len(switch)}")
        for channel in targets:
            channel.enabled = offered
        done += self._reception(targets, channels, offered)
        done += self._recording(targets, offered)
        names = ", ".join(c.name for c in targets)
        self.log(f"admin: {names} {'shown' if offered else 'hidden'}: {', '.join(done) or 'no change'}")
        return done

    def rename(self, names: Mapping[str, str]) -> list[str]:
        """Rename channels ({id: new name}); unknown ids fail before anything changes."""
        channels = {c.uuid: c for c in self.client.channels(include_disabled=True)}
        missing = [i for i in names if i not in channels]
        if missing:
            raise TvhNotFound(", ".join(missing))
        changed = [(channels[i], n) for i, n in names.items() if channels[i].name != n]
        if not changed:
            return []
        # autoname off first (property order), else TVHeadend renames it back from the service name
        self.client.save_node([{"uuid": c.uuid, "autoname": False, "name": n} for c, n in changed])
        done = [f"{c.name} → {n}" for c, n in changed]
        configured = {x.casefold() for x in (self.cfg.dvb.record_channels or []) + (self.cfg.ads.channels or [])}
        stale = [c.name for c, _ in changed if c.name.casefold() in configured]
        if stale:
            done.append("pozor, v konfiguraci (record_channels / [ads] channels) jsou staré názvy: " + ", ".join(stale))
        self.log(f"admin: renamed {', '.join(done)}")
        return done

    def _reception(self, targets: list[Channel], channels: list[Channel], offered: bool) -> list[str]:
        services = {s.uuid: s for s in self.client.services()}
        offered_services = {s for c in channels if c.enabled for s in c.services}
        own = list(dict.fromkeys(s for c in targets for s in c.services if s in services))
        # a service shared with another offered channel stays on
        switch = [services[s] for s in own if services[s].enabled != offered and (offered or s not in offered_services)]
        if switch:
            self.client.save_node([{"uuid": s.uuid, "enabled": offered} for s in switch])
        done = [f"služby {'zapnuty' if offered else 'vypnuty'}: {len(switch)}"] if switch else []

        muxes = {m.uuid: m for m in self.client.muxes()}
        changes = []
        for mux_id in sorted({services[s].mux for s in own if services[s].mux}):
            mux = muxes.get(mux_id)
            if mux is None:
                continue
            in_use = any(services[s].mux == mux_id for s in offered_services if s in services)
            if offered and mux.enabled == MUX_DISABLE:
                changes.append((mux, MUX_ENABLE))
            elif not offered and not in_use and mux.enabled == MUX_ENABLE:
                changes.append((mux, MUX_DISABLE))
        if changes:
            self.client.save_node([{"uuid": m.uuid, "enabled": value} for m, value in changes])
            done += [f"multiplex {m.name or m.uuid} {'zapnut' if v == MUX_ENABLE else 'vypnut'}" for m, v in changes]
        return done

    def _recording(self, targets: list[Channel], offered: bool) -> list[str]:
        done = []
        if recording_set_up(self.client):  # only where dvb-setup has switched recording on
            plan = plan_setup(self.client, self.cfg.dvb)
            apply_setup(self.client, plan)
            if plan.toggle or plan.create:
                done.append("nahrávání zapnuto" if offered else "nahrávání vypnuto")
        if not offered:
            ids = {c.uuid for c in targets}
            upcoming = [e.uuid for e in self.client.dvr_entries("grid_upcoming") if e.channel in ids]
            self.client.cancel_dvr_entries(upcoming)
            finished = [e.uuid for e in self.client.dvr_entries("grid_finished") +
                        self.client.dvr_entries("grid_failed") if e.channel in ids]
            self.client.remove_dvr_entries(finished)
            if upcoming:
                done.append(f"zrušeno plánovaných nahrávek: {len(upcoming)}")
            if finished:
                done.append(f"smazáno nahrávek: {len(finished)}")
        return done


class AdminSite:
    """``/admin`` (page) and ``/admin/api/*`` (JSON) behind HTTP Basic."""

    def __init__(self, cfg: Config, gateway: DvbGateway, admin: ChannelAdmin | None = None):
        self.password = cfg.admin.password
        self.gateway = gateway
        self.admin = admin or ChannelAdmin(cfg, gateway.client, gateway.log, gateway.now)

    def handle(self, method: str, path: str, headers: Mapping[str, str], body: bytes = b"",
               query: Mapping[str, str] | None = None) -> Response:
        if not self._authorized(headers.get("Authorization") or ""):
            return Response(401, "Přihlaste se jménem admin.\n".encode("utf-8"), "text/plain; charset=utf-8",
                            {"WWW-Authenticate": 'Basic realm="cz-tv-hub admin", charset="UTF-8"',
                             "Cache-Control": "no-store"})
        try:
            if method == "GET" and path in ("/admin", "/admin/"):
                return Response(200, PAGE.encode("utf-8"), HTML, PAGE_HEADERS)
            if method == "GET" and path == "/admin/api/channels":
                return self._overview()
            if method == "GET" and path == "/admin/api/storage":
                return self._storage()
            if method == "POST" and path == "/admin/api/settings":
                if not self._same_origin(headers):
                    return error(403, "Požadavek z cizí stránky")
                return self._settings(body)
            if method == "POST" and (path == "/admin/api/channels" or CHANNEL_RE.match(path)):
                if not self._same_origin(headers):
                    return error(403, "Požadavek z cizí stránky")
                m = CHANNEL_RE.match(path)
                return self._set(m.group(1) if m else None, body)
            if method == "GET" and path == "/admin/api/profiles":
                return json_response(200, {"profiles": self.gateway.profiles.overview()})
            if method == "POST" and (path == "/admin/api/profiles" or PROFILE_RE.match(path)):
                if not self._same_origin(headers):
                    return error(403, "Požadavek z cizí stránky")
                m = PROFILE_RE.match(path)
                return self._profile(m.group(1) if m else None, body)
            if method == "GET" and path == "/admin/api/defaults":
                return self._defaults_overview()
            if method == "POST" and path == "/admin/api/defaults":
                if not self._same_origin(headers):
                    return error(403, "Požadavek z cizí stránky")
                return self._defaults(body)
            if method == "GET" and path == TMDB_PATH:
                return self._tmdb_overview((query or {}).get("page"))
            if method == "POST" and path == TMDB_PATH:
                if not self._same_origin(headers):
                    return error(403, "Požadavek z cizí stránky")
                return self._tmdb(body)
            if path.startswith("/admin/api/channels") or path.startswith("/admin/api/profiles") \
                    or path in ("/admin/api/defaults", TMDB_PATH):
                return error(405, "Nepodporovaná metoda")
            return error(404, "Nenalezeno")
        except TvhNotFound:
            return error(404, "Kanál nenalezen")
        except TvhError as exc:
            self.gateway.log(f"admin: {path}: {exc}")
            return error(502, TvhError.user_message)

    def _authorized(self, header: str) -> bool:
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "basic":
            return False
        try:
            user, _, password = base64.b64decode(token.strip(), validate=True).decode("utf-8").partition(":")
        except (binascii.Error, UnicodeDecodeError):
            return False
        return user == ADMIN_USER and bool(self.password) and \
            hmac.compare_digest(password.encode("utf-8"), self.password.encode("utf-8"))

    @staticmethod
    def _same_origin(headers: Mapping[str, str]) -> bool:
        if headers.get(CSRF_HEADER) != "1":
            return False
        origin = headers.get("Origin")
        return origin is None or origin in (f"http://{headers.get('Host')}", f"https://{headers.get('Host')}")

    def _overview(self) -> Response:
        status = json.loads(self.gateway.status().body)
        rows = self.admin.overview()
        archive = self.gateway.archive
        recorded = archive.record_channels()
        for row in rows:
            row["record"] = row["id"] in recorded
            stats = archive.store.stats(row["id"])
            row["stored"] = {"hours": round(stats["seconds"] / 3600, 2), "bytes": stats["bytes"],
                             "oldest": epg.iso(int(stats["oldest"])) if stats["oldest"] else None}
            rec = archive.recorders.recorder(row["id"])
            row["recorderState"] = rec.state if rec is not None else None
        return json_response(200, {"channels": rows, "status": status,
                                   "recording": {"max": archive.max_recordings, "blocked": archive.can_record()},
                                   "settings": {"thumbnails": self.gateway.thumbs.enabled,
                                                "maxDays": archive.max_days()},
                                   "recordingSetUp": recording_set_up(self.gateway.client),
                                   "retentionDays": archive.max_days(),
                                   "recordingPaused": archive.recorders.paused})

    def _storage(self) -> Response:
        """"Přehled nahrávek": per recorded channel hours, how far back, size, measured bitrate, gaps."""
        rows = {r["id"]: r for r in self.admin.overview()}
        data = self.gateway.archive.overview({i: r["name"] for i, r in rows.items()})
        for channel in data["channels"]:
            channel["multiplexes"] = rows[channel["id"]]["multiplexes"] if channel["id"] in rows else []
        return json_response(200, data)

    def _record(self, ids: list[str], on: bool) -> Response:
        """Switch the continuous recording of channels (spec/dvb-gateway.md "Continuous recording")."""
        rows = {r["id"]: r for r in self.admin.overview()}
        missing = [i for i in ids if i not in rows]
        if missing:
            raise TvhNotFound(", ".join(missing))
        if on:
            blocked = [rows[i]["name"] for i in ids if not rows[i]["offered"] or rows[i]["scrambled"]]
            if blocked:
                return error(400, "Nahrávat lze jen zapnuté nekódované kanály: " + ", ".join(blocked))
            reason = self.gateway.archive.can_record()
            if reason:
                return error(400, reason)
            limit = self.gateway.archive.max_recordings
            total = len(self.gateway.archive.record_channels() | set(ids))
            if limit > 0 and total > limit:
                return error(400, f"Raspberry zvládne nahrávat nejvýš {limit} kanály najednou (každý stojí výkon "
                                  f"na převod zvuku); vybráno by bylo {total}. Limit je [dvb] max_recordings.")
        done = self.gateway.archive.set_record(ids, on)
        self.gateway.log(f"admin: recording {'on' if on else 'off'}: {', '.join(rows[i]['name'] for i in ids)}")
        return json_response(200, {"changes": done})

    def _set(self, channel_id: str | None, body: bytes) -> Response:
        """``POST /admin/api/channels/<id>`` {"offered"} | {"name"}, or for several channels
        ``POST /admin/api/channels`` {"ids": [...], "offered"} | {"names": {id: name}}."""
        try:
            payload: Any = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            payload = None
        if isinstance(payload, dict) and ("name" in payload or "names" in payload):
            return self._rename(channel_id, payload)
        if isinstance(payload, dict) and isinstance(payload.get("record"), bool):
            ids = payload.get("ids") if channel_id is None else [channel_id]
            if not isinstance(ids, list) or not 0 < len(ids) <= MAX_BULK or \
                    not all(isinstance(i, str) and ID_RE.match(i) for i in ids):
                return error(400, 'Očekávám {"ids": ["<id kanálu>", …], "record": true|false}')
            return self._record(list(dict.fromkeys(ids)), payload["record"])
        if not isinstance(payload, dict) or not isinstance(payload.get("offered"), bool):
            return error(400, 'Očekávám {"offered": true|false}')
        if channel_id is None:
            ids = payload.get("ids")
            if not isinstance(ids, list) or not 0 < len(ids) <= MAX_BULK or \
                    not all(isinstance(i, str) and ID_RE.match(i) for i in ids):
                return error(400, 'Očekávám {"ids": ["<id kanálu>", …], "offered": true|false}')
        else:
            ids = [channel_id]
        offered = payload["offered"]
        try:
            done = self.admin.set_offered(ids, offered)
        finally:
            if not offered:  # a hidden channel is not recorded any more, its recording is deleted
                done_rec = self.gateway.archive.set_record(ids, False)
            self.gateway.channels_changed(hidden=None if offered else ids)
        if not offered:
            done += done_rec
        return json_response(200, {"changes": done})

    def _settings(self, body: bytes) -> Response:
        """Household switches; for now "Náhledy pořadů" (spec/dvb-gateway.md "Thumbnails")."""
        try:
            payload: Any = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            payload = None
        days = payload.get("maxDays") if isinstance(payload, dict) else None
        if isinstance(days, int) and not isinstance(days, bool):
            if not 1 <= days <= 14:
                return error(400, "Uchovávat lze 1 až 14 dní")
            self.gateway.archive.set_max_days(days)
            self.gateway.log(f"admin: recordings kept {days} days")
        elif not isinstance(payload, dict) or not isinstance(payload.get("thumbnails"), bool):
            return error(400, 'Očekávám {"thumbnails": true|false} nebo {"maxDays": 1..14}')
        else:
            self.gateway.thumbs.set_enabled(payload["thumbnails"])
            self.gateway.log(f"admin: thumbnails {'on' if payload['thumbnails'] else 'off'}")
        return json_response(200, {"settings": {"thumbnails": self.gateway.thumbs.enabled,
                                                "maxDays": self.gateway.archive.max_days()}})

    def _profile(self, profile_id: str | None, body: bytes) -> Response:
        """Tab "Profily": ``POST /admin/api/profiles`` {"name", "password"} (default settings), or for one
        profile ``POST /admin/api/profiles/<id>`` {"password"} (ends its sessions) | {"delete": true} |
        {"makeDefault": true} (its channel list copied once into the household default).
        Logged with the name only, never the password."""
        store = self.gateway.profiles
        try:
            payload = parse_body(body, 1024)
            if profile_id is None:
                name = store.admin_create(payload.get("name"), payload.get("password"))
                self.gateway.log(f"admin: profile {name}: created")
                return json_response(201, {"name": name})
            if payload.get("delete") is True:
                name = store.delete(profile_id)
                self.gateway.log(f"admin: profile {name}: deleted")
                return json_response(200, {"name": name})
            if "password" in payload:
                name = store.set_password(profile_id, payload.get("password"))
                self.gateway.log(f"admin: profile {name}: new password")
                return json_response(200, {"name": name})
            if payload.get("makeDefault") is True:
                name, prefs = store.channel_prefs(profile_id)
                if prefs is None:
                    return error(400, f"Profil {name} nemá uložený seznam kanálů")
                doc = self.gateway.defaults.set(prefs)
                self.gateway.log(f"admin: defaults: copied from profile {name}")
                return json_response(200, {"name": name, **doc})
            return error(400, 'Očekávám {"password": "…"}, {"delete": true} nebo {"makeDefault": true}')
        except ProfileError as exc:
            return json_response(exc.status, exc.payload(), exc.headers)

    def _defaults_overview(self) -> Response:
        """Tab "Výchozí kanály": the default plus every channel a TV can list, in the apps' built-in order
        (ČT catalogue, the offered Anténa channels, Poslanecká sněmovna). Without TVHeadend the others still come."""
        known = [{"key": f"ct:{c.id}", "name": c.name, "source": "ct"} for c in ct_channels.CHANNELS]
        dvb_error = None
        try:
            known += [{"key": f"dvb:{c['id']}", "name": c["name"], "source": "dvb"}
                      for c in json.loads(self.gateway.channels().body)["channels"]]
        except TvhError:
            dvb_error = TvhError.user_message
        known += [{"key": key, "name": name, "source": "psp"} for key, name in dflt.PSP_STREAMS]
        return json_response(200, {**self.gateway.defaults.get(), "channels": known, "dvbError": dvb_error})

    def _defaults(self, body: bytes) -> Response:
        """``POST /admin/api/defaults`` {"channelPrefs": {...}} (Uložit) | {"clear": true} (Zrušit výchozí)."""
        try:
            payload = parse_body(body, dflt.MAX_BODY)
            if payload.get("clear") is True:
                doc = self.gateway.defaults.set(None)
                self.gateway.log("admin: defaults: cleared")
            elif "channelPrefs" in payload:
                doc = self.gateway.defaults.set(dflt.check_prefs(payload["channelPrefs"]))  # null: 400, not a clear
                self.gateway.log("admin: defaults: saved")
            else:
                return error(400, 'Očekávám {"channelPrefs": {…}} nebo {"clear": true}')
        except ProfileError as exc:
            return json_response(exc.status, exc.payload(), exc.headers)
        return json_response(200, doc)

    # -- tab "TMDb" (tmdbstore.py) ---------------------------------------------------------
    def _tmdb_overview(self, page_param: str | None) -> Response:
        """The store, read-only: one page of entries by title, the refresh period and its warning."""
        details = self.gateway.details
        months = self.gateway.tmdb_months()
        doc: dict[str, Any] = {"enabled": details is not None, "months": months,
                               "licenceMonths": tmdbstore.LICENCE_MONTHS,
                               "warning": tmdbstore.WARNING if months > tmdbstore.LICENCE_MONTHS else None}
        if details is None:
            return json_response(200, {**doc, "total": 0, "page": 1, "pages": 1, "entries": []})
        try:
            page = int(page_param or "1")
        except ValueError:
            page = 0
        if not 1 <= page <= 100000:
            return error(400, "Neplatná stránka")
        store = details.store
        entries = store.page(page)
        total = len(store)
        pages = max(1, -(-total // tmdbstore.PAGE_SIZE))
        return json_response(200, {**doc, "total": total, "page": page, "pages": pages,
                                   "entries": [self._tmdb_row(e, store) for e in entries]})

    @staticmethod
    def _tmdb_row(entry: tmdbstore.Entry, store: tmdbstore.TmdbStore) -> dict:
        return {"key": entry.key, "title": entry.title, "kind": entry.kind, "year": entry.year,
                "match": entry.matched, "matchKind": entry.match_kind, "tmdbId": entry.tmdb_id,
                "rating": entry.rating, "genres": list(entry.genres), "matchYear": entry.match_year,
                "fetchedAt": epg.iso(int(entry.fetched_at)),
                "deleteAt": epg.iso(int(min(entry.fetched_at + store.max_age, 253402300799)))}

    def _tmdb(self, body: bytes) -> Response:
        """``POST /admin/api/tmdb`` {"months": N} | {"refresh": key} | {"delete": key} | {"deleteAll": true}."""
        try:
            payload = parse_body(body, 2048)
        except ProfileError as exc:
            return json_response(exc.status, exc.payload(), exc.headers)
        details = self.gateway.details
        if "months" in payload:
            months = payload["months"]
            if not isinstance(months, int) or isinstance(months, bool) or not 1 <= months <= tmdbstore.MAX_MONTHS:
                return error(400, "Počet měsíců musí být kladné celé číslo")
            self.gateway.settings.update(**{tmdbstore.SETTING: months})
            removed = details.store.purge() if details is not None else 0
            self.gateway.log(f"admin: tmdb: refresh after {months} month(s)"
                             + (f", deleted {removed} older entr{'y' if removed == 1 else 'ies'}" if removed else ""))
            return self._tmdb_overview("1")
        if details is None:
            return error(404, "TMDb není zapnuté (chybí [tmdb] key)")
        store = details.store
        if payload.get("deleteAll") is True:
            removed = store.delete_all()
            self.gateway.log(f"admin: tmdb: delete all ({removed} entr{'y' if removed == 1 else 'ies'})")
            return json_response(200, {"deleted": removed})
        for action in ("refresh", "delete"):
            key = payload.get(action)
            if key is None:
                continue
            if not isinstance(key, str) or not 0 < len(key) <= TMDB_KEY_MAX:
                return error(400, "Neplatný záznam")
            if action == "delete":
                entry = store.delete(key)
                if entry is None:
                    return error(404, "Záznam nenalezen")
                self.gateway.log(f"admin: tmdb: delete {entry.title}")
                return json_response(200, {"deleted": 1})
            entry = store.get(key)
            if entry is None:
                return error(404, "Záznam nenalezen")
            try:  # the stored, cleaned title is all that goes to TMDb
                match = details.refresh(tmdb.Query(entry.title, entry.year, entry.kind))
            except tmdb.TmdbError as exc:
                self.gateway.log(f"admin: tmdb: refresh {entry.title}: failed ({exc})")
                return error(502, exc.user_message)
            self.gateway.log(f"admin: tmdb: refresh {entry.title}: {'match' if match else 'no match'}")
            refreshed = store.get(key)
            return json_response(200, {"entry": self._tmdb_row(refreshed, store) if refreshed else None})
        return error(400, 'Očekávám {"months": N}, {"refresh": "…"}, {"delete": "…"} nebo {"deleteAll": true}')

    def _rename(self, channel_id: str | None, payload: dict) -> Response:
        raw = {channel_id: payload.get("name")} if channel_id else payload.get("names")
        if not isinstance(raw, dict) or not 0 < len(raw) <= MAX_BULK or \
                not all(isinstance(i, str) and ID_RE.match(i) for i in raw):
            return error(400, 'Očekávám {"names": {"<id kanálu>": "název"}}')
        names = {i: clean_name(n) for i, n in raw.items()}
        if any(n is None for n in names.values()):
            return error(400, f"Název musí mít 1 až {MAX_NAME} znaků")
        try:
            done = self.admin.rename(names)  # type: ignore[arg-type]
        finally:
            self.gateway.channels_changed()
        return json_response(200, {"changes": done})


PAGE = r"""<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>cz-tv-hub – správa</title>
<style>
:root { color-scheme: light dark; --bg: #f6f6f4; --card: #fff; --fg: #1d1d1b; --muted: #6b6b66; --line: #e2e2dc;
  --accent: #1f6feb; --off: #b4b4ad; --warn: #b3261e; --track: #e8e8e3; --sel: #eef4ff; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #151514; --card: #1f1f1d; --fg: #ecece8; --muted: #9a9a93; --line: #33332f; --accent: #6ea8ff;
    --off: #55554f; --warn: #ff8a80; --track: #33332f; --sel: #1d2738; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.45 system-ui, -apple-system, sans-serif; }
main { max-width: 900px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 22px; margin: 0 0 4px; }
.sub { color: var(--muted); margin: 0 0 20px; }
.status { display: flex; flex-wrap: wrap; gap: 8px 20px; color: var(--muted); font-size: 13px; margin-bottom: 16px; }
.status b { color: var(--fg); font-weight: 600; }
.list { background: var(--card); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
.bar { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; padding: 10px 16px;
  border-bottom: 1px solid var(--line); font-size: 14px; position: sticky; top: 0; background: var(--card); z-index: 1; }
.bar .count { color: var(--muted); margin-right: auto; }
.bar .confirm { flex-basis: 100%; }
.row { display: grid; grid-template-columns: 22px 30px minmax(0, 1fr) auto; gap: 12px; align-items: center;
  padding: 12px 16px; border-top: 1px solid var(--line); }
.list .row:first-of-type { border-top: 0; }
.row.selected { background: var(--sel); }
.row.hidden .name, .row.hidden .now { color: var(--muted); }
input[type=checkbox].pick { width: 18px; height: 18px; margin: 0; accent-color: var(--accent); cursor: pointer; }
.num { color: var(--muted); font-variant-numeric: tabular-nums; text-align: right; }
.name { font-weight: 600; }
.orig { color: var(--muted); font-size: 12px; }
.filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 10px 16px;
  border-bottom: 1px solid var(--line); font-size: 14px; }
.filters input, .filters select { font: inherit; font-size: 14px; padding: 3px 8px; border-radius: 6px;
  border: 1px solid var(--line); background: var(--bg); color: var(--fg); max-width: 100%; }
.filters input { width: 200px; }
.filters .count { color: var(--muted); margin-left: auto; }
.now { margin-top: 2px; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.now .time { color: var(--muted); font-variant-numeric: tabular-nums; margin-right: 6px; }
.now .genre { color: var(--muted); }
.progress { height: 3px; border-radius: 2px; background: var(--track); margin: 5px 0 4px; max-width: 360px; }
.progress span { display: block; height: 100%; border-radius: 2px; background: var(--accent); }
.meta { color: var(--muted); font-size: 13px; }
.meta .no { color: var(--warn); }
.switch { position: relative; width: 46px; height: 26px; flex: none; }
.switch input { position: absolute; opacity: 0; width: 100%; height: 100%; margin: 0; cursor: pointer; }
.switch span { position: absolute; inset: 0; border-radius: 13px; background: var(--off); transition: .15s; }
.switch span::after { content: ""; position: absolute; top: 3px; left: 3px; width: 20px; height: 20px;
  border-radius: 50%; background: #fff; transition: .15s; }
.switch input:checked + span { background: var(--accent); }
.switch input:checked + span::after { transform: translateX(20px); }
.switch input:focus-visible + span { outline: 2px solid var(--accent); outline-offset: 2px; }
.switch input:disabled { cursor: wait; }
.confirm { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 14px; }
.row .confirm { grid-column: 3 / 5; }
button { font: inherit; font-size: 14px; border-radius: 6px; border: 1px solid var(--line); background: var(--card);
  color: var(--fg); padding: 4px 12px; cursor: pointer; }
button:disabled { opacity: .5; cursor: default; }
button.danger { background: var(--warn); border-color: var(--warn); color: #fff; }
.meta button { padding: 1px 8px; font-size: 13px; margin-left: 8px; }
.msg { margin: 12px 0; padding: 10px 14px; border-radius: 8px; border: 1px solid var(--line); background: var(--card);
  font-size: 14px; }
.msg.err { border-color: var(--warn); color: var(--warn); }
.help { color: var(--muted); font-size: 13px; margin-top: 20px; }
.setting { display: flex; gap: 8px; align-items: center; margin: 0 0 16px; font-size: 14px; }
.setting input { width: 18px; height: 18px; accent-color: var(--accent); }
.name button.link { border: 0; background: none; color: var(--accent); padding: 0 0 0 8px; font-size: 13px;
  font-weight: 400; }
.edit { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.edit input { font: inherit; font-weight: 600; padding: 3px 8px; border-radius: 6px; border: 1px solid var(--accent);
  background: var(--bg); color: var(--fg); min-width: 0; width: 220px; max-width: 100%; }
.confirm ul { margin: 4px 0; padding-left: 20px; flex-basis: 100%; }
.tabs { display: flex; gap: 4px; margin: 0 0 16px; }
.tabs button { padding: 6px 14px; }
.tabs button[aria-selected=true] { background: var(--accent); border-color: var(--accent); color: #fff; }
.rec { display: inline-flex; gap: 6px; align-items: center; margin-left: 10px; }
.rec input { width: 16px; height: 16px; margin: 0; accent-color: var(--accent); }
.mux { display: inline-block; font-size: 12px; font-weight: 500; color: var(--muted); border: 1px solid var(--line);
  border-radius: 10px; padding: 0 8px; margin-left: 8px; vertical-align: 1px; white-space: nowrap; }
.legal { font-size: 13px; color: var(--muted); border-left: 3px solid var(--accent); padding: 6px 12px; margin: 0 0 16px; }
table { width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--line);
  border-radius: 10px; overflow: hidden; font-size: 14px; }
th, td { text-align: left; padding: 8px 12px; border-top: 1px solid var(--line); vertical-align: top; }
th { font-weight: 600; color: var(--muted); border-top: 0; }
td.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.totals { display: flex; flex-wrap: wrap; gap: 8px 20px; margin: 0 0 12px; font-size: 14px; }
.totals b { font-weight: 600; }
.warn { color: var(--warn); }
.drow { display: grid; grid-template-columns: 70px minmax(0, 1fr) auto auto; gap: 12px; align-items: center;
  padding: 8px 16px; border-top: 1px solid var(--line); }
.list .drow:first-of-type { border-top: 0; }
.drow.hidden .name, .drow.off .name { color: var(--muted); }
.drow .src { color: var(--muted); font-size: 12px; }
.drow .arrows { display: flex; gap: 4px; }
.drow .arrows button { padding: 2px 10px; }
.drow label { display: inline-flex; gap: 6px; align-items: center; font-size: 13px; }
.drow input { width: 16px; height: 16px; margin: 0; accent-color: var(--accent); }
.dactions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 12px 0; font-size: 14px; }
</style>
</head>
<body>
<main>
<h1>Anténa – kanály</h1>
<p class="sub">Vypnutý kanál aplikace nenabízejí, nenahrává se a tuner ho neladí.</p>
<div class="status" id="status"></div>
<div class="tabs" role="tablist">
  <button role="tab" id="tab-channels" aria-selected="true">Kanály</button>
  <button role="tab" id="tab-storage" aria-selected="false">Přehled nahrávek</button>
  <button role="tab" id="tab-profiles" aria-selected="false">Profily</button>
  <button role="tab" id="tab-defaults" aria-selected="false">Výchozí kanály</button>
  <button role="tab" id="tab-tmdb" aria-selected="false">TMDb</button>
</div>
<p class="legal">Nahrávání je soukromá kopie jen pro tuto domácnost. Záznam zůstává na disku tohoto Raspberry, maže se
automaticky po nastaveném počtu dní (nejvýše 14) nebo dříve při nedostatku místa a nelze ho stáhnout ani sdílet.</p>
<div id="msg" role="status" aria-live="polite"></div>
<section id="storage" hidden>
  <div class="totals" id="totals"></div>
  <table><thead><tr><th>Kanál</th><th>Multiplex</th><th>Stav</th><th class="n">Uloženo</th><th>Zpět do</th><th class="n">Velikost</th>
    <th class="n">Datový tok</th><th class="n">Výpadky</th></tr></thead><tbody id="storage-rows"></tbody></table>
  <p class="help">Když místo dochází, maže se nejstarších 10 minut z kanálu, který má uloženo nejvíc hodin, takže
  každý nahrávaný kanál drží zhruba stejný počet posledních hodin. Na disku zůstává rezerva 10 % (nejméně 5 GB);
  když ji nejde udržet, nahrávání se pozastaví. Datový tok je změřený ze skutečných nahrávek.</p>
</section>
<section id="profiles" hidden>
  <table><thead><tr><th>Jméno</th><th>Vytvořen</th><th>Poslední změna</th><th class="n">Přihlášeno</th><th></th></tr></thead>
    <tbody id="profile-rows"></tbody></table>
  <form id="profile-new" class="edit" style="margin-top: 16px">
    <b>Nový profil</b>
    <input id="profile-name" maxlength="32" placeholder="Jméno" autocomplete="off" aria-label="Jméno">
    <input id="profile-password" type="password" maxlength="64" placeholder="Heslo (4 až 64 znaků)"
      autocomplete="new-password" aria-label="Heslo">
    <button type="submit">Vytvořit</button>
  </form>
  <p class="help">Profil drží nastavení televizí (pořadí a skrytí kanálů, zdroje, náhledy), nic o sledování. Nový
  profil má výchozí nastavení; přihlásí se k němu televize v Nastavení → Profil. Nové heslo odhlásí všechny televize
  profilu, Smazat ho odstraní i s jeho přihlášeními. Heslo se ukládá jen jako otisk (PBKDF2).</p>
</section>
<section id="defaults" hidden>
  <p class="sub" id="defaults-state"></p>
  <label class="setting"><input type="checkbox" id="d-src-ct"> Internetová TV (ČT)</label>
  <label class="setting"><input type="checkbox" id="d-src-dvb"> Anténa</label>
  <label class="setting"><input type="checkbox" id="d-src-psp"> Poslanecká sněmovna</label>
  <div class="list" id="defaults-rows"></div>
  <div class="dactions" id="defaults-actions">
    <button id="defaults-save">Uložit</button>
    <button id="defaults-clear">Zrušit výchozí</button>
    <span class="meta" id="defaults-dirty"></span>
  </div>
  <p class="help">Výchozí seznam kanálů platí pro televize bez profilu, na kterých nikdo seznam nezměnil (Nastavení →
  Kanály). Televize ho načte při spuštění a při otevření Nastavení. Jakmile na televizi někdo kanál skryje, přesune
  nebo vypne zdroj, má televize vlastní seznam; „Obnovit výchozí pořadí“ ji vrátí k tomuto. Profil se tímto nemění
  a výchozí seznam nesleduje změny profilu („Použít jako výchozí“ v záložce Profily ho jednou zkopíruje).
  Zrušit výchozí vrátí televize k vestavěnému pořadí.</p>
</section>
<section id="tmdb" hidden>
  <p class="sub" id="tmdb-state"></p>
  <form id="tmdb-months-form" class="setting">
    <label for="tmdb-months">Obnovit údaje z TMDb po</label>
    <input id="tmdb-months" type="number" min="1" step="1" inputmode="numeric" style="width: 7em">
    <span>měsících</span> <button type="submit">Uložit</button>
  </form>
  <p class="warn" id="tmdb-warning" hidden></p>
  <table><thead><tr><th>Název</th><th>Shoda</th><th class="n">Hodnocení</th><th>Žánry</th><th class="n">Rok</th>
    <th>Získáno</th><th>Smaže se</th><th></th></tr></thead><tbody id="tmdb-rows"></tbody></table>
  <div class="dactions">
    <button id="tmdb-prev">Předchozí</button> <span class="meta" id="tmdb-pages"></span> <button id="tmdb-next">Další</button>
    <span id="tmdb-all"></span>
  </div>
  <p class="help">Údaje z TMDb (shoda, hodnocení, žánry, rok, plakát) pro stránku pořadu a archiv. Raspberry se TMDb
  ptá jen na očištěný název pořadu, který archiv nebo otevřená stránka ukazuje, nikdy na nic, co kdo napíše do
  hledání. Záznam se obnoví, dokud ho archiv ukazuje, a smaže se po nastaveném počtu měsíců od získání. Seznam se
  nedá stáhnout ani exportovat. This application uses TMDB and the TMDB APIs but is not endorsed, certified, or
  otherwise approved by TMDB.</p>
</section>
<section id="channels">
<label class="setting"><input type="checkbox" id="thumbs"> Náhledy pořadů v aplikacích (snímek z nahrávky, jen v paměti)</label>
<label class="setting">Uchovávat nahrávky nejvýše <select id="maxdays"></select> (dříve se maže, když dochází místo)</label>
<div class="list">
  <div class="filters" role="search">
    <input type="search" id="f-text" placeholder="Hledat název" aria-label="Hledat v názvu i původním názvu">
    <select id="f-offered" aria-label="Zapnuté nebo vypnuté"><option value="">Zapnuté i vypnuté</option>
      <option value="on">Jen zapnuté</option><option value="off">Jen vypnuté</option></select>
    <select id="f-rec" aria-label="Nahrávání"><option value="">Nahrávané i nenahrávané</option>
      <option value="on">Jen nahrávané</option><option value="off">Jen nenahrávané</option></select>
    <select id="f-mux" aria-label="Multiplex"><option value="">Všechny multiplexy</option></select>
    <button id="f-reset" hidden>Zrušit filtr</button>
    <span class="count" id="f-count"></span>
  </div>
  <div class="bar" id="bar"></div>
  <div id="list"><div class="row"><span></span><span></span><span class="meta">Načítám…</span></div></div>
</div>
<p class="help">Vypnutím se zruší plánované nahrávky kanálu a smažou jeho nahrávky. Multiplex se vypne, až
v něm nezbude žádný zapnutý kanál. Po zapnutí se nahrává od dalšího pořadu; programový průvodce se doplní,
až tuner multiplex znovu naladí (může trvat desítky minut). Vypnuté kanály nemají program, protože se nepřijímají.
Nový název kanálu se uloží v TVHeadendu a aplikace ho ukážou po obnovení seznamu kanálů.
„Nahrávat“ znamená nepřetržitý záznam kanálu na disk tohoto Raspberry; vypnutím se záznam kanálu hned smaže.
Kódované kanály nahrávat nelze.</p>
</section>
</main>
<script>
"use strict";
var list = document.getElementById("list"), bar = document.getElementById("bar"), msg = document.getElementById("msg");
var rows = [], selected = {}, pending = null, busy = false, editing = null;

function el(tag, cls, text) {
  var e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function say(text, isError) {
  msg.textContent = "";
  if (text) msg.appendChild(el("div", "msg" + (isError ? " err" : ""), text));
}
function gb(n) { return n < 1e9 ? Math.round(n / 1e6) + " MB" : (n / 1e9).toFixed(n >= 1e11 ? 0 : 1) + " GB"; }
function plural(n, one, few, many) { return n === 1 ? one : (n > 1 && n < 5 ? few : many); }
function channels(n) { return n + " " + plural(n, "kanál", "kanály", "kanálů"); }
function hm(iso) { return new Date(iso).toLocaleTimeString("cs-CZ", {hour: "2-digit", minute: "2-digit"}); }

function api(method, path, body) {
  var opts = {method: method, credentials: "same-origin", headers: {"X-Cztvhub-Admin": "1"}};
  if (body) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  return fetch(path, opts).then(function (r) {
    return r.json().catch(function () { return {}; }).then(function (data) {
      if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
      return data;
    });
  });
}

var TABS = ["channels", "storage", "profiles", "defaults", "tmdb"];
function showTab(name) {
  TABS.forEach(function (t) {
    document.getElementById("tab-" + t).setAttribute("aria-selected", String(t === name));
    document.getElementById(t).hidden = t !== name;
  });
  if (name === "storage") loadStorage();
  if (name === "profiles") loadProfiles();
  if (name === "defaults" && !dfltDirty) loadDefaults();  // unsaved edits survive a look at another tab
  if (name === "tmdb") loadTmdb(tmdbPage);
}
TABS.forEach(function (t) {
  document.getElementById("tab-" + t).addEventListener("click", function () { showTab(t); });
});

var tmdbPage = 1, tmdbConfirm = null;
function months(n) { return n + " " + plural(n, "měsíc", "měsíce", "měsíců"); }
function entries(n) { return n + " " + plural(n, "záznam", "záznamy", "záznamů"); }
function loadTmdb(page) {
  return api("GET", "/admin/api/tmdb?page=" + page).then(function (data) {
    tmdbPage = data.page;
    document.getElementById("tmdb-state").textContent = data.enabled
      ? "Uloženo " + entries(data.total) + ", obnova po " + months(data.months) + "."
      : "TMDb je vypnuté (v konfiguraci chybí [tmdb] key), nic se neukládá.";
    var input = document.getElementById("tmdb-months");
    if (document.activeElement !== input) input.value = String(data.months);
    var warning = document.getElementById("tmdb-warning");
    warning.hidden = !data.warning;
    warning.textContent = data.warning || "";
    var body = document.getElementById("tmdb-rows");
    body.textContent = "";
    if (!data.entries.length) {
      var tr = el("tr"), td = el("td", null, "Žádné uložené údaje.");
      td.colSpan = 8; tr.appendChild(td); body.appendChild(tr);
    }
    data.entries.forEach(function (e) {
      var tr = el("tr");
      tr.appendChild(el("td", "name", e.title + (e.year ? " (" + e.year + ")" : "")));
      tr.appendChild(el("td", null, e.match ? (e.matchKind === "movie" ? "film" : "seriál") + " #" + e.tmdbId : "bez shody"));
      tr.appendChild(el("td", "n", e.rating ? String(e.rating.value).replace(".", ",") + " (" + e.rating.votes + ")" : "–"));
      tr.appendChild(el("td", null, e.genres.join(", ") || "–"));
      tr.appendChild(el("td", "n", e.matchYear ? String(e.matchYear) : "–"));
      tr.appendChild(el("td", null, when(e.fetchedAt)));
      tr.appendChild(el("td", null, when(e.deleteAt)));
      var cell = el("td", "confirm");
      if (tmdbConfirm === e.key) {
        var yes = el("button", "danger", "Smazat"), no = el("button", null, "Zpět");
        yes.addEventListener("click", function () { tmdbConfirm = null; tmdbAction({"delete": e.key}, "Smazáno: " + e.title); });
        no.addEventListener("click", function () { tmdbConfirm = null; loadTmdb(tmdbPage); });
        cell.appendChild(yes); cell.appendChild(no);
      } else {
        var refresh = el("button", null, "Obnovit"), del = el("button", null, "Smazat");
        refresh.addEventListener("click", function () { tmdbAction({refresh: e.key}, "Obnoveno: " + e.title); });
        del.addEventListener("click", function () { tmdbConfirm = e.key; loadTmdb(tmdbPage); });
        cell.appendChild(refresh); cell.appendChild(del);
      }
      tr.appendChild(cell);
      body.appendChild(tr);
    });
    document.getElementById("tmdb-pages").textContent = "strana " + data.page + " z " + data.pages;
    document.getElementById("tmdb-prev").disabled = data.page <= 1;
    document.getElementById("tmdb-next").disabled = data.page >= data.pages;
    var all = document.getElementById("tmdb-all");
    all.textContent = "";
    if (tmdbConfirm === "*") {
      all.appendChild(el("span", null, "Smazat všechny uložené údaje z TMDb? "));
      var yesAll = el("button", "danger", "Smazat vše"), noAll = el("button", null, "Zpět");
      yesAll.addEventListener("click", function () { tmdbConfirm = null; tmdbAction({deleteAll: true}, "Údaje z TMDb jsou smazané."); });
      noAll.addEventListener("click", function () { tmdbConfirm = null; loadTmdb(tmdbPage); });
      all.appendChild(yesAll); all.appendChild(noAll);
    } else if (data.enabled && data.total) {
      var delAll = el("button", null, "Smazat vše");
      delAll.addEventListener("click", function () { tmdbConfirm = "*"; loadTmdb(tmdbPage); });
      all.appendChild(delAll);
    }
  }, function (e) { say("Údaje z TMDb se nepodařilo načíst: " + e.message, true); });
}
function tmdbAction(body, done) {
  api("POST", "/admin/api/tmdb", body).then(function () { say(done); },
    function (e) { say(e.message, true); }).then(function () { loadTmdb(tmdbPage); });
}
document.getElementById("tmdb-prev").addEventListener("click", function () { loadTmdb(Math.max(1, tmdbPage - 1)); });
document.getElementById("tmdb-next").addEventListener("click", function () { loadTmdb(tmdbPage + 1); });
document.getElementById("tmdb-months-form").addEventListener("submit", function (ev) {
  ev.preventDefault();
  var n = Number(document.getElementById("tmdb-months").value);
  if (!(n >= 1) || Math.floor(n) !== n) { say("Zadejte kladné celé číslo měsíců.", true); return; }
  api("POST", "/admin/api/tmdb", {months: n}).then(function (data) {
    say("Údaje z TMDb se obnoví po " + months(data.months) + "." + (data.warning ? " " + data.warning : ""));
  }, function (e) { say(e.message, true); }).then(function () { loadTmdb(1); });
});

function tvs(n) { return n + " " + plural(n, "televize", "televize", "televizí"); }
var profileEditing = null;
function loadProfiles() {
  return api("GET", "/admin/api/profiles").then(function (data) {
    var body = document.getElementById("profile-rows");
    body.textContent = "";
    if (!data.profiles.length) {
      var tr = el("tr"), td = el("td", null, "Zatím žádný profil.");
      td.colSpan = 5; tr.appendChild(td); body.appendChild(tr);
    }
    data.profiles.forEach(function (p) {
      var tr = el("tr");
      tr.appendChild(el("td", "name", p.name));
      tr.appendChild(el("td", null, when(p.created)));
      tr.appendChild(el("td", null, when(p.updated)));
      tr.appendChild(el("td", "n", tvs(p.sessions)));
      var cell = el("td", "confirm");
      if (profileEditing && profileEditing.id === p.id && profileEditing.what === "password") {
        var input = el("input");
        input.type = "password"; input.maxLength = 64; input.autocomplete = "new-password";
        input.placeholder = "Nové heslo"; input.setAttribute("aria-label", "Nové heslo pro " + p.name);
        var save = el("button", null, "Uložit"), cancel = el("button", null, "Zrušit");
        save.addEventListener("click", function () {
          profileAction(p.id, {password: input.value}, "Nové heslo pro " + p.name + " je uložené, televize profilu jsou odhlášené.");
        });
        cancel.addEventListener("click", function () { profileEditing = null; loadProfiles(); });
        cell.appendChild(input); cell.appendChild(save); cell.appendChild(cancel);
      } else if (profileEditing && profileEditing.id === p.id && profileEditing.what === "delete") {
        var yes = el("button", "danger", "Smazat"), no = el("button", null, "Zrušit");
        cell.appendChild(el("span", null, "Smazat profil " + p.name + "?"));
        yes.addEventListener("click", function () { profileAction(p.id, {"delete": true}, "Profil " + p.name + " je smazaný."); });
        no.addEventListener("click", function () { profileEditing = null; loadProfiles(); });
        cell.appendChild(yes); cell.appendChild(no);
      } else if (profileEditing && profileEditing.id === p.id && profileEditing.what === "default") {
        var use = el("button", null, "Použít"), back = el("button", null, "Zrušit");
        cell.appendChild(el("span", null, "Nahradit výchozí seznam kanálů seznamem profilu " + p.name + "?"));
        use.addEventListener("click", function () {
          profileAction(p.id, {makeDefault: true}, "Výchozí seznam kanálů je zkopírovaný z profilu " + p.name +
            ". Další změny profilu ho neovlivní.");
        });
        back.addEventListener("click", function () { profileEditing = null; loadProfiles(); });
        cell.appendChild(use); cell.appendChild(back);
      } else {
        var pw = el("button", null, "Nové heslo"), del = el("button", null, "Smazat"),
          copy = el("button", null, "Použít jako výchozí");
        pw.addEventListener("click", function () { profileEditing = {id: p.id, what: "password"}; loadProfiles(); });
        del.addEventListener("click", function () { profileEditing = {id: p.id, what: "delete"}; loadProfiles(); });
        copy.addEventListener("click", function () { profileEditing = {id: p.id, what: "default"}; loadProfiles(); });
        cell.appendChild(pw); cell.appendChild(del); cell.appendChild(copy);
      }
      tr.appendChild(cell);
      body.appendChild(tr);
    });
  }, function (e) { say("Profily se nepodařilo načíst: " + e.message, true); });
}
function profileAction(id, body, done) {
  api("POST", "/admin/api/profiles/" + id, body).then(function () {
    profileEditing = null; say(done);
  }, function (e) { say(e.message, true); }).then(loadProfiles);
}
document.getElementById("profile-new").addEventListener("submit", function (ev) {
  ev.preventDefault();
  var name = document.getElementById("profile-name"), password = document.getElementById("profile-password");
  api("POST", "/admin/api/profiles", {name: name.value, password: password.value}).then(function (data) {
    name.value = ""; password.value = "";
    say("Profil " + data.name + " je vytvořený.");
  }, function (e) { say(e.message, true); }).then(loadProfiles);
});

// "Výchozí kanály": seq = the stored order incl. keys this page does not know (kept at their place), then the
// known channels missing from it; arrows swap a row with its neighbouring known row, like the apps' move().
var dflt = null, dfltDirty = false, dfltClearing = false;
var SOURCES = {ct: "Internet ČT", dvb: "Anténa", psp: "Sněmovna"};
function loadDefaults() {
  return api("GET", "/admin/api/defaults").then(function (data) {
    var prefs = data.channelPrefs, known = {}, seq = [], seen = {};
    data.channels.forEach(function (c) { known[c.key] = c; });
    ((prefs && prefs.order) || []).forEach(function (k) { if (!seen[k]) { seen[k] = true; seq.push(k); } });
    data.channels.forEach(function (c) { if (!seen[c.key]) { seen[c.key] = true; seq.push(c.key); } });
    dflt = {data: data, known: known, seq: seq, hidden: ((prefs && prefs.hidden) || []).slice(),
      sourcesOff: ((prefs && prefs.sourcesOff) || []).slice()};
    dfltDirty = false; dfltClearing = false;
    renderDefaults();
  }, function (e) { say("Výchozí kanály se nepodařilo načíst: " + e.message, true); });
}
function dfltMove(index, by) {
  var seq = dflt.seq, j = index + by;
  while (j >= 0 && j < seq.length && !dflt.known[seq[j]]) j += by;
  if (j < 0 || j >= seq.length) return;
  var t = seq[index]; seq[index] = seq[j]; seq[j] = t;
  dfltDirty = true; renderDefaults();
}
function dfltToggle(list, value, on) {
  var i = list.indexOf(value);
  if (on && i < 0) list.push(value);
  if (!on && i >= 0) list.splice(i, 1);
  dfltDirty = true; renderDefaults();
}
function renderDefaults() {
  var d = dflt.data, state = document.getElementById("defaults-state");
  state.textContent = d.channelPrefs
    ? "Výchozí seznam je nastavený (verze " + d.version + ", změněno " + when(d.updated) + ")."
    : "Výchozí seznam není nastavený: televize bez vlastního seznamu mají vestavěné pořadí.";
  if (d.dvbError) state.textContent += " Anténní kanály teď nejde načíst (" + d.dvbError + "), jejich místo v seznamu zůstává.";
  ["ct", "dvb", "psp"].forEach(function (s) {
    var box = document.getElementById("d-src-" + s);
    box.checked = dflt.sourcesOff.indexOf(s) < 0;
    box.onchange = function () { dfltToggle(dflt.sourcesOff, s, !box.checked); };
  });
  var box = document.getElementById("defaults-rows"), rowsShown = [];
  box.textContent = "";
  dflt.seq.forEach(function (k, i) { if (dflt.known[k]) rowsShown.push(i); });
  rowsShown.forEach(function (i, n) {
    var c = dflt.known[dflt.seq[i]], hidden = dflt.hidden.indexOf(c.key) >= 0,
      off = dflt.sourcesOff.indexOf(c.source) >= 0;
    var row = el("div", "drow" + (hidden ? " hidden" : "") + (off ? " off" : ""));
    row.appendChild(el("span", "src", SOURCES[c.source]));
    var name = el("span", "name", c.name);
    if (off) name.appendChild(el("span", "orig", " (zdroj vypnutý)"));
    row.appendChild(name);
    var label = el("label"), show = el("input");
    show.type = "checkbox"; show.checked = !hidden; show.setAttribute("aria-label", "Zobrazovat " + c.name);
    show.addEventListener("change", function () { dfltToggle(dflt.hidden, c.key, !show.checked); });
    label.appendChild(show); label.appendChild(document.createTextNode("zobrazovat"));
    row.appendChild(label);
    var arrows = el("span", "arrows"), up = el("button", null, "▲"), down = el("button", null, "▼");
    up.disabled = n === 0; down.disabled = n === rowsShown.length - 1;
    up.setAttribute("aria-label", "Výš: " + c.name); down.setAttribute("aria-label", "Níž: " + c.name);
    up.addEventListener("click", function () { dfltMove(i, -1); });
    down.addEventListener("click", function () { dfltMove(i, 1); });
    arrows.appendChild(up); arrows.appendChild(down);
    row.appendChild(arrows);
    box.appendChild(row);
  });
  document.getElementById("defaults-dirty").textContent = dfltDirty ? "Neuložené změny" : "";
  var clear = document.getElementById("defaults-clear");
  clear.textContent = dfltClearing ? "Opravdu zrušit výchozí" : "Zrušit výchozí";
  clear.className = dfltClearing ? "danger" : "";
  clear.disabled = !d.channelPrefs;
}
document.getElementById("defaults-save").addEventListener("click", function () {
  if (!dflt) return;
  var prefs = {}, old = dflt.data.channelPrefs || {};
  Object.keys(old).forEach(function (k) { prefs[k] = old[k]; });  // fields of a newer app stay as they came
  prefs.order = dflt.seq.slice(); prefs.hidden = dflt.hidden.slice(); prefs.sourcesOff = dflt.sourcesOff.slice();
  api("POST", "/admin/api/defaults", {channelPrefs: prefs}).then(function () {
    say("Výchozí seznam kanálů je uložený.");
  }, function (e) { say(e.message, true); }).then(loadDefaults);
});
document.getElementById("defaults-clear").addEventListener("click", function () {
  if (!dfltClearing) { dfltClearing = true; renderDefaults(); return; }
  api("POST", "/admin/api/defaults", {clear: true}).then(function () {
    say("Výchozí seznam je zrušený, televize bez vlastního seznamu mají vestavěné pořadí.");
  }, function (e) { say(e.message, true); }).then(loadDefaults);
});

function muxLabel(names) {
  // TVHeadend names multiplexes by frequency ("514MHz"); one tuner receives one multiplex at a time
  return (names || []).map(function (n) { return n.replace(/^(\d+(?:\.\d+)?)\s*MHz$/i, "$1 MHz"); }).join(", ");
}
function hours(h) {
  if (h == null) return "–";
  if (h < 1) return Math.round(h * 60) + " min";
  return (h < 10 ? h.toFixed(1) : String(Math.round(h))).replace(".", ",") + " h";
}
function when(iso) {
  if (!iso) return "–";
  var d = new Date(iso);
  return d.toLocaleDateString("cs-CZ", {day: "numeric", month: "numeric"}) + " " + hm(iso);
}
function loadStorage() {
  return api("GET", "/admin/api/storage").then(function (data) {
    var t = document.getElementById("totals");
    t.textContent = "";
    function tot(label, value, cls) { var p = el("span", cls || null, label + " "); p.appendChild(el("b", null, value)); t.appendChild(p); }
    tot("Disk:", gb(data.disk.freeBytes) + " volno z " + gb(data.disk.totalBytes));
    tot("Rezerva:", gb(data.disk.reserveBytes));
    tot("Nahrávky:", gb(data.usedBytes));
    tot("Nejvýše:", data.maxDays + plural(data.maxDays, " den", " dny", " dní"));
    if (data.hoursPerChannel != null) tot("Vejde se zhruba:", hours(data.hoursPerChannel) + " na kanál");
    if (data.paused) tot("", "Nahrávání pozastaveno – plný disk", "warn");
    var body = document.getElementById("storage-rows");
    body.textContent = "";
    if (!data.channels.length) {
      var tr = el("tr"), td = el("td", null, "Nic se nenahrává. Zapněte „Nahrávat“ u kanálů v záložce Kanály.");
      td.colSpan = 8; tr.appendChild(td); body.appendChild(tr);
    }
    data.channels.forEach(function (c) {
      var tr = el("tr");
      tr.appendChild(el("td", null, c.name));
      tr.appendChild(el("td", null, muxLabel(c.multiplexes) || "–"));
      tr.appendChild(el("td", c.recording ? null : "warn", c.recording ? c.state : "nenahrává se (záznam se smaže)"));
      tr.appendChild(el("td", "n", hours(c.hours)));
      tr.appendChild(el("td", null, when(c.oldest)));
      tr.appendChild(el("td", "n", gb(c.bytes)));
      tr.appendChild(el("td", "n", c.gbPerHour == null ? "měří se…" : String(c.gbPerHour).replace(".", ",") + " GB/h"));
      tr.appendChild(el("td", "n", c.gaps ? c.gaps + " (" + String(c.gapMinutes).replace(".", ",") + " min)" : "–"));
      body.appendChild(tr);
    });
  }, function (e) { say("Přehled se nepodařilo načíst: " + e.message, true); });
}

var maxdays = document.getElementById("maxdays");
for (var d = 1; d <= 14; d++) {
  var o = el("option", null, d + plural(d, " den", " dny", " dní"));
  o.value = String(d);
  maxdays.appendChild(o);
}
maxdays.addEventListener("change", function () {
  var days = parseInt(maxdays.value, 10);
  maxdays.disabled = true;
  api("POST", "/admin/api/settings", {maxDays: days}).then(function (data) {
    maxdays.value = String(data.settings.maxDays);
    say("Nahrávky se uchovávají nejvýše " + maxdays.options[maxdays.selectedIndex].text + ".");
  }, function (e) {
    say("Nastavení se nepodařilo uložit: " + e.message, true);
  }).then(function () { maxdays.disabled = false; return load(); });
});

var thumbs = document.getElementById("thumbs");
thumbs.addEventListener("change", function () {
  var on = thumbs.checked;
  thumbs.disabled = true;
  api("POST", "/admin/api/settings", {thumbnails: on}).then(function (data) {
    thumbs.checked = data.settings.thumbnails;
    say("Náhledy pořadů " + (data.settings.thumbnails ? "zapnuty." : "vypnuty."));
  }, function (e) {
    thumbs.checked = !on;
    say("Nastavení se nepodařilo uložit: " + e.message, true);
  }).then(function () { thumbs.disabled = false; });
});

function renderStatus(data) {
  if (data.settings) {
    thumbs.checked = !!data.settings.thumbnails;
    if (data.settings.maxDays) maxdays.value = String(data.settings.maxDays);
  }
  var s = data.status || {}, box = document.getElementById("status");
  box.textContent = "";
  function item(label, value) { var p = el("span", null, label + " "); p.appendChild(el("b", null, value)); box.appendChild(p); }
  item("TVHeadend:", s.tvheadendReachable ? "dostupný" : "nedostupný");
  if (s.tuners) item("Tunery:", s.tuners.filter(function (t) { return t.inUse; }).length + " z " + s.tuners.length + " v provozu");
  if (s.disk && s.disk.totalBytes) item("Disk:", gb(s.disk.freeBytes) + " volno z " + gb(s.disk.totalBytes));
  var d = data.retentionDays;
  var rec = rows.filter(function (c) { return c.record; }).length;
  item("Nahrávání:", (rec ? channels(rec) : "žádný kanál") + (data.recording && data.recording.max ? " z " + data.recording.max : "") +
    ", nejvýše " + d + plural(d, " den", " dny", " dní") + (data.recordingPaused ? " – POZASTAVENO, plný disk" : ""));
  if (data.recording && data.recording.blocked) item("Nahrávání nejde zapnout:", data.recording.blocked);
}

// filters (AND), client-side only; kept in the URL hash so a reload keeps them
var fText = document.getElementById("f-text"), fOffered = document.getElementById("f-offered");
var fRec = document.getElementById("f-rec"), fMux = document.getElementById("f-mux");
var fReset = document.getElementById("f-reset"), fCount = document.getElementById("f-count");
var filters = {q: "", offered: "", rec: "", mux: ""};
function fold(s) { return String(s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase(); }
// "nahrává se": continuous recording ("Nahrávat") or an enabled dvb-setup recording rule
function recorded(c) { return !!c.record || c.recordingRule === true; }
function visible(c) {
  if (filters.offered && c.offered !== (filters.offered === "on")) return false;
  if (filters.rec && recorded(c) !== (filters.rec === "on")) return false;
  if (filters.mux && c.multiplexes.indexOf(filters.mux) < 0) return false;
  var hay = fold(c.name + " " + (c.originalName || ""));
  return fold(filters.q).split(/\s+/).every(function (w) { return hay.indexOf(w) >= 0; });
}
function shown() { return rows.filter(visible); }
function filtering() { return !!(filters.q.trim() || filters.offered || filters.rec || filters.mux); }
function readHash() {
  var p = new URLSearchParams(location.hash.slice(1));
  Object.keys(filters).forEach(function (k) { filters[k] = p.get(k) || ""; });
  fText.value = filters.q; fOffered.value = filters.offered; fRec.value = filters.rec;
  filters.offered = fOffered.value; filters.rec = fRec.value;  // unknown values fall back to "all"
}
function applyFilters() {
  filters = {q: fText.value, offered: fOffered.value, rec: fRec.value, mux: fMux.value};
  var p = new URLSearchParams();
  Object.keys(filters).forEach(function (k) { if (filters[k]) p.set(k, filters[k]); });
  history.replaceState(null, "", p.toString() ? "#" + p.toString() : location.pathname + location.search);
  rows.forEach(function (c) { if (!visible(c)) delete selected[c.id]; });  // bulk actions only on what is visible
  pending = null;
  if (editing !== null && !rows.some(function (c) { return c.id === editing && visible(c); })) editing = null;
  render();
}
function renderMuxOptions() {
  var names = {};
  rows.forEach(function (c) { c.multiplexes.forEach(function (n) { names[n] = true; }); });
  var sorted = Object.keys(names).sort(function (a, b) { return (parseFloat(a) || 0) - (parseFloat(b) || 0) || (a < b ? -1 : a > b ? 1 : 0); });
  while (fMux.options.length > 1) fMux.remove(1);
  sorted.forEach(function (n) { var o = el("option", null, muxLabel([n])); o.value = n; fMux.appendChild(o); });
  fMux.value = names[filters.mux] ? filters.mux : "";
  filters.mux = fMux.value;
}
fText.addEventListener("input", applyFilters);
[fOffered, fRec, fMux].forEach(function (s) { s.addEventListener("change", applyFilters); });
fReset.addEventListener("click", function () {
  fText.value = ""; fOffered.value = ""; fRec.value = ""; fMux.value = "";
  applyFilters();
});
readHash();

function picked() { return rows.filter(function (c) { return selected[c.id] && visible(c); }); }

function renderBar() {
  bar.textContent = "";
  var chosen = picked(), vis = shown();
  var all = el("input", "pick");
  all.type = "checkbox";
  all.checked = vis.length > 0 && chosen.length === vis.length;
  all.indeterminate = chosen.length > 0 && chosen.length < vis.length;
  all.setAttribute("aria-label", filtering() ? "Vybrat všechny zobrazené kanály" : "Vybrat všechny kanály");
  all.disabled = busy || !vis.length;
  all.addEventListener("change", function () {
    selected = {};
    if (all.checked) vis.forEach(function (c) { selected[c.id] = true; });
    pending = null;
    render();
  });
  bar.appendChild(all);
  bar.appendChild(el("span", "count", chosen.length ? "Vybráno: " + channels(chosen.length) : "Vyberte kanály pro hromadnou změnu"));
  var off = el("button", null, "Vypnout vybrané");
  var on = el("button", null, "Zapnout vybrané");
  off.disabled = busy || !chosen.some(function (c) { return c.offered; });
  on.disabled = busy || !chosen.some(function (c) { return !c.offered || !c.received; });
  var shorten = (chosen.length ? chosen : vis).filter(function (c) { return c.shortName !== c.name; });
  var short = el("button", null, chosen.length ? "Zkrátit názvy vybraných" : "Zkrátit názvy");
  short.disabled = busy || !shorten.length;
  short.title = "Např. „CT 1 PHA HD T2“ → „ČT1“";
  off.addEventListener("click", function () { pending = "bulk"; render(); });
  on.addEventListener("click", function () { send(chosen, true); });
  short.addEventListener("click", function () { pending = "short"; render(); });
  var recOn = el("button", null, "Nahrávat vybrané");
  var recOff = el("button", null, "Nenahrávat vybrané");
  recOn.disabled = busy || !chosen.some(function (c) { return !c.record && c.offered && !c.scrambled; });
  recOff.disabled = busy || !chosen.some(function (c) { return c.record; });
  recOn.addEventListener("click", function () {
    record(chosen.filter(function (c) { return !c.record && c.offered && !c.scrambled; }), true);
  });
  recOff.addEventListener("click", function () { pending = "recoff"; render(); });
  bar.appendChild(off);
  bar.appendChild(on);
  bar.appendChild(short);
  bar.appendChild(recOn);
  bar.appendChild(recOff);
  if (pending === "recoff") {
    var stop = chosen.filter(function (c) { return c.record; });
    var h = stop.reduce(function (n, c) { return n + (c.stored ? c.stored.hours : 0); }, 0);
    bar.appendChild(confirmBox("Přestat nahrávat " + channels(stop.length) + "?" +
      (h ? " Smaže se " + hours(h) + " záznamu." : ""), function () { record(stop, false); }, "Nenahrávat"));
  }
  if (pending === "short") {
    var box = confirmBox("Přejmenovat " + channels(shorten.length) + "?", function () {
      var names = {};
      shorten.forEach(function (c) { names[c.id] = c.shortName; });
      rename(names, channels(shorten.length));
    }, "Přejmenovat", false);
    var ul = el("ul");
    shorten.forEach(function (c) { ul.appendChild(el("li", null, c.name + " → " + c.shortName)); });
    box.insertBefore(ul, box.children[1]);
    bar.appendChild(box);
  }
  if (pending === "bulk") {
    var victims = chosen.filter(function (c) { return c.offered; });
    var recs = victims.reduce(function (n, c) { return n + c.recordings; }, 0);
    var names = victims.slice(0, 4).map(function (c) { return c.name; }).join(", ") + (victims.length > 4 ? ", …" : "");
    bar.appendChild(confirmBox("Vypnout " + channels(victims.length) + " (" + names + ")?" +
      (recs ? " Smažou se i jejich nahrávky (" + recs + ")." : ""), function () { send(victims, false); }));
  }
}

function nowLine(c) {
  var box = el("div");
  if (!c.now) return box;
  var line = el("div", "now");
  line.appendChild(el("span", "time", hm(c.now.start) + "–" + hm(c.now.end)));
  line.appendChild(document.createTextNode(c.now.title || "Bez názvu"));
  if (c.now.genre) line.appendChild(el("span", "genre", " · " + c.now.genre));
  line.title = c.now.title + (c.now.subtitle ? " – " + c.now.subtitle : "");
  box.appendChild(line);
  var start = Date.parse(c.now.start), end = Date.parse(c.now.end);
  var done = Math.max(0, Math.min(1, (Date.now() - start) / Math.max(1, end - start)));
  var prog = el("div", "progress"), fill = el("span");
  fill.style.width = Math.round(done * 100) + "%";
  prog.appendChild(fill);
  box.appendChild(prog);
  return box;
}

function render() {
  renderBar();
  list.textContent = "";
  var vis = shown();
  fReset.hidden = !filtering();
  fCount.textContent = vis.length + " z " + rows.length + (rows.length === 1 ? " kanálu" : " kanálů");
  if (!rows.length) { list.appendChild(el("div", "row", "TVHeadend nemá žádné kanály.")); return; }
  if (!vis.length) { list.appendChild(el("div", "row", "Filtru neodpovídá žádný kanál.")); return; }
  vis.forEach(function (c) {
    var row = el("div", "row" + (c.offered ? "" : " hidden") + (selected[c.id] ? " selected" : ""));
    var pick = el("input", "pick");
    pick.type = "checkbox";
    pick.checked = !!selected[c.id];
    pick.disabled = busy;
    pick.setAttribute("aria-label", "Vybrat " + c.name);
    pick.addEventListener("change", function () {
      if (pick.checked) selected[c.id] = true; else delete selected[c.id];
      if (pending === "bulk") pending = null;
      render();
    });
    row.appendChild(pick);
    row.appendChild(el("span", "num", c.number > 0 ? String(c.number) : ""));
    var info = el("div");
    if (editing === c.id) {
      info.appendChild(editBox(c));
    } else {
      var name = el("div", "name", c.name);
      if (c.multiplexes.length) {
        var badge = el("span", "mux", "multiplex " + muxLabel(c.multiplexes));
        badge.title = "Jeden tuner přijímá najednou jeden multiplex se všemi jeho kanály";
        name.appendChild(badge);
      }
      var pen = el("button", "link", "Přejmenovat");
      pen.disabled = busy;
      pen.setAttribute("aria-label", "Přejmenovat " + c.name);
      pen.addEventListener("click", function () { editing = c.id; pending = null; render(); });
      name.appendChild(pen);
      info.appendChild(name);
    }
    if (c.originalName) {
      var orig = el("div", "orig", "původní název: " + c.originalName);
      orig.title = "Název z vysílání (služba v TVHeadendu)";
      info.appendChild(orig);
    }
    info.appendChild(nowLine(c));
    var meta = el("div", "meta");
    function flag(text, ok) {
      if (meta.textContent) meta.appendChild(document.createTextNode(" · "));
      meta.appendChild(el("span", ok ? null : "no", text));
    }
    if (c.offered) {
      flag(c.received ? "příjem zapnutý" : "příjem vypnutý", c.received);
      if (c.recordingRule !== null) flag(c.recordingRule ? "nahrává se" : "nenahrává se", c.recordingRule);
    } else {
      flag(c.received ? "služba se stále přijímá" : "nepřijímá se", !c.received);
    }
    if (c.recordings) flag("nahrávek: " + c.recordings, true);
    if (c.stored && c.stored.hours) flag("uloženo " + hours(c.stored.hours) + " (od " + when(c.stored.oldest) + "), " +
      gb(c.stored.bytes), true);
    if (c.record && c.recorderState) flag(c.recorderState, c.recorderState === "nahrává");
    var recBox = el("label", "rec");
    var recInput = el("input");
    recInput.type = "checkbox";
    recInput.checked = !!c.record;
    recInput.disabled = busy || (!c.record && (!c.offered || c.scrambled));
    recBox.title = c.scrambled ? "Kódovaný kanál nelze nahrávat" : (!c.offered ? "Nejdřív kanál zapněte" : "");
    recInput.addEventListener("change", function () {
      if (recInput.checked) { record([c], true); return; }
      pending = "rec:" + c.id;
      render();
    });
    recBox.appendChild(recInput);
    recBox.appendChild(document.createTextNode("Nahrávat"));
    meta.appendChild(recBox);
    if (c.offered && !c.received && c.multiplexes.length) {
      var fix = el("button", null, "Zapnout příjem");
      fix.disabled = busy;
      fix.addEventListener("click", function () { send([c], true); });
      meta.appendChild(fix);
    }
    info.appendChild(meta);
    row.appendChild(info);
    var sw = el("label", "switch");
    var input = el("input");
    input.type = "checkbox";
    input.checked = c.offered;
    input.setAttribute("aria-label", "Nabízet kanál " + c.name);
    input.disabled = busy;
    input.addEventListener("change", function () {
      if (input.checked) { send([c], true); return; }
      pending = c.id;
      render();
    });
    sw.appendChild(input);
    sw.appendChild(el("span"));
    row.appendChild(sw);
    if (pending === "rec:" + c.id) {
      row.appendChild(confirmBox("Přestat nahrávat " + c.name + "?" + (c.stored && c.stored.hours
        ? " Smaže se " + hours(c.stored.hours) + " záznamu." : ""), function () { record([c], false); }, "Nenahrávat"));
    }
    if (pending === c.id) {
      row.appendChild(confirmBox(c.recordings
        ? "Vypnout " + c.name + "? Smažou se i jeho nahrávky (" + c.recordings + ")."
        : "Vypnout " + c.name + "?", function () { send([c], false); }));
    }
    list.appendChild(row);
  });
}

function editBox(c) {
  var box = el("div", "edit");
  var input = el("input");
  input.value = c.name;
  input.maxLength = 64;
  input.setAttribute("aria-label", "Nový název kanálu " + c.name);
  var save = el("button", null, "Uložit"), cancel = el("button", null, "Zrušit");
  var hint = c.shortName !== c.name ? el("button", "link", "Návrh: " + c.shortName) : null;
  function done() { editing = null; render(); }
  function submit() {
    var value = input.value.replace(/\s+/g, " ").trim();
    if (!value || value === c.name) return done();
    editing = null;
    var names = {};
    names[c.id] = value;
    rename(names, c.name);
  }
  save.addEventListener("click", submit);
  cancel.addEventListener("click", done);
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") submit();
    if (e.key === "Escape") done();
  });
  box.appendChild(input);
  box.appendChild(save);
  box.appendChild(cancel);
  if (hint) {
    hint.addEventListener("click", function () { input.value = c.shortName; input.focus(); });
    box.appendChild(hint);
  }
  setTimeout(function () { input.focus(); input.select(); }, 0);
  return box;
}

function rename(names, label) {
  busy = true;
  pending = null;
  render();
  say("Přejmenovávám " + label + "…");
  api("POST", "/admin/api/channels", {names: names}).then(function (data) {
    say(data.changes.length ? "Přejmenováno: " + data.changes.join(", ") + "." : "Beze změny.");
    selected = {};
  }, function (e) {
    say(label + ": " + e.message, true);
  }).then(function () { busy = false; return load(); });
}

function confirmBox(text, action, yesLabel, danger) {
  var box = el("div", "confirm");
  box.appendChild(el("span", null, text));
  var yes = el("button", danger === false ? null : "danger", yesLabel || "Vypnout");
  var no = el("button", null, "Zpět");
  yes.addEventListener("click", function () { pending = null; action(); });
  no.addEventListener("click", function () { pending = null; render(); });
  box.appendChild(yes);
  box.appendChild(no);
  setTimeout(function () { yes.focus(); }, 0);
  return box;
}

function record(targets, on) {
  if (!targets.length) return;
  var label = targets.length === 1 ? targets[0].name : channels(targets.length);
  busy = true;
  pending = null;
  render();
  say((on ? "Zapínám nahrávání: " : "Vypínám nahrávání: ") + label + "…");
  var request = targets.length === 1
    ? api("POST", "/admin/api/channels/" + encodeURIComponent(targets[0].id), {record: on})
    : api("POST", "/admin/api/channels", {ids: targets.map(function (c) { return c.id; }), record: on});
  request.then(function (data) {
    say(label + ": " + (data.changes.length ? data.changes.join(", ") : "beze změny") + ".");
    selected = {};
  }, function (e) {
    say(label + ": " + e.message, true);
  }).then(function () { busy = false; return load(); });
}

function send(targets, offered) {
  if (!targets.length) return;
  var label = targets.length === 1 ? targets[0].name : channels(targets.length);
  busy = true;
  pending = null;
  render();
  say((offered ? "Zapínám " : "Vypínám ") + label + "…");
  var request = targets.length === 1
    ? api("POST", "/admin/api/channels/" + encodeURIComponent(targets[0].id), {offered: offered})
    : api("POST", "/admin/api/channels", {ids: targets.map(function (c) { return c.id; }), offered: offered});
  request.then(function (data) {
    say(label + ": " + (data.changes.length ? data.changes.join(", ") : "beze změny") + ".");
    selected = {};
  }, function (e) {
    say(label + ": " + e.message, true);
  }).then(function () { busy = false; return load(); });
}

function load() {
  return api("GET", "/admin/api/channels").then(function (data) {
    rows = data.channels;
    var known = {};
    rows.forEach(function (c) { if (selected[c.id]) known[c.id] = true; });
    selected = known;
    renderMuxOptions();
    renderStatus(data);
    render();
  }, function (e) { say("Nepodařilo se načíst kanály: " + e.message, true); });
}
load();
// keep "now playing" current, but never while the user is in the middle of something
setInterval(function () {
  if (busy || pending !== null || editing !== null) return;
  if (!document.getElementById("channels").hidden) load();
  else if (!document.getElementById("storage").hidden) loadStorage();
}, 60000);
</script>
</body>
</html>
"""
