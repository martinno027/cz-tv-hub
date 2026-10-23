"""Web administration at ``/admin``: choose which antenna channels the household is offered.

A hidden channel is switched off in TVHeadend itself, so TVHeadend is the only state:
- the channel is disabled: apps no longer get it (``/dvb/channels``, EPG, live, recordings);
- its services are disabled, and a multiplex is disabled once none of its channels is offered,
  so the tuner does not tune to it at all (no reception, no EPG scan);
- its cztvhub autorec rule is disabled (``dvb-setup`` keeps it disabled), its scheduled and running
  recordings are cancelled and its finished recordings deleted.
Showing the channel again reverses the switches (recordings start with the next programme); showing an
already offered channel repairs its reception (services / multiplex switched off elsewhere).

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

from ..config import Config
from . import epg
from .gateway import DvbGateway, Response, error, json_response
from .setup import RULE_COMMENT, apply_setup, plan_setup, recording_set_up
from .tvheadend import MUX_DISABLE, MUX_ENABLE, Channel, TvheadendClient, TvhError, TvhNotFound

ADMIN_USER = "admin"
CHANNEL_RE = re.compile(r"^/admin/api/channels/([A-Za-z0-9_-]{1,64})$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_BULK = 500
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
            rows.append({
                "id": channel.uuid,
                "number": channel.number,
                "name": channel.name,
                "offered": channel.enabled,
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

    def handle(self, method: str, path: str, headers: Mapping[str, str], body: bytes = b"") -> Response:
        if not self._authorized(headers.get("Authorization") or ""):
            return Response(401, "Přihlaste se jménem admin.\n".encode("utf-8"), "text/plain; charset=utf-8",
                            {"WWW-Authenticate": 'Basic realm="cz-tv-hub admin", charset="UTF-8"',
                             "Cache-Control": "no-store"})
        try:
            if method == "GET" and path in ("/admin", "/admin/"):
                return Response(200, PAGE.encode("utf-8"), HTML, PAGE_HEADERS)
            if method == "GET" and path == "/admin/api/channels":
                return self._overview()
            if method == "POST" and (path == "/admin/api/channels" or CHANNEL_RE.match(path)):
                if not self._same_origin(headers):
                    return error(403, "Požadavek z cizí stránky")
                m = CHANNEL_RE.match(path)
                return self._set(m.group(1) if m else None, body)
            if path.startswith("/admin/api/channels"):
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
        return json_response(200, {"channels": self.admin.overview(), "status": status,
                                   "recordingSetUp": recording_set_up(self.gateway.client),
                                   "retentionDays": self.gateway.dvb.retention_days})

    def _set(self, channel_id: str | None, body: bytes) -> Response:
        """``POST /admin/api/channels/<id>`` {"offered"} or ``POST /admin/api/channels`` {"ids": [...], "offered"}."""
        try:
            payload: Any = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            payload = None
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
            self.gateway.channels_changed(hidden=None if offered else ids)
        return json_response(200, {"changes": done})


PAGE = """<!doctype html>
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
</style>
</head>
<body>
<main>
<h1>Anténa – kanály</h1>
<p class="sub">Vypnutý kanál aplikace nenabízejí, nenahrává se a tuner ho neladí.</p>
<div class="status" id="status"></div>
<div id="msg" role="status" aria-live="polite"></div>
<div class="list">
  <div class="bar" id="bar"></div>
  <div id="list"><div class="row"><span></span><span></span><span class="meta">Načítám…</span></div></div>
</div>
<p class="help">Vypnutím se zruší plánované nahrávky kanálu a smažou jeho nahrávky. Multiplex se vypne, až
v něm nezbude žádný zapnutý kanál. Po zapnutí se nahrává od dalšího pořadu; programový průvodce se doplní,
až tuner multiplex znovu naladí (může trvat desítky minut). Vypnuté kanály nemají program, protože se nepřijímají.</p>
</main>
<script>
"use strict";
var list = document.getElementById("list"), bar = document.getElementById("bar"), msg = document.getElementById("msg");
var rows = [], selected = {}, pending = null, busy = false;

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
function gb(n) { return (n / 1e9).toFixed(n >= 1e11 ? 0 : 1) + " GB"; }
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

function renderStatus(data) {
  var s = data.status || {}, box = document.getElementById("status");
  box.textContent = "";
  function item(label, value) { var p = el("span", null, label + " "); p.appendChild(el("b", null, value)); box.appendChild(p); }
  item("TVHeadend:", s.tvheadendReachable ? "dostupný" : "nedostupný");
  if (s.tuners) item("Tunery:", s.tuners.filter(function (t) { return t.inUse; }).length + " z " + s.tuners.length + " v provozu");
  if (s.disk && s.disk.totalBytes) item("Disk:", gb(s.disk.freeBytes) + " volno z " + gb(s.disk.totalBytes));
  var d = data.retentionDays;
  item("Nahrávání:", data.recordingSetUp ? "zapnuté, " + d + plural(d, " den", " dny", " dní") : "není nastavené (dvb-setup)");
}

function picked() { return rows.filter(function (c) { return selected[c.id]; }); }

function renderBar() {
  bar.textContent = "";
  var chosen = picked();
  var all = el("input", "pick");
  all.type = "checkbox";
  all.checked = rows.length > 0 && chosen.length === rows.length;
  all.indeterminate = chosen.length > 0 && chosen.length < rows.length;
  all.setAttribute("aria-label", "Vybrat všechny kanály");
  all.disabled = busy;
  all.addEventListener("change", function () {
    selected = {};
    if (all.checked) rows.forEach(function (c) { selected[c.id] = true; });
    pending = null;
    render();
  });
  bar.appendChild(all);
  bar.appendChild(el("span", "count", chosen.length ? "Vybráno: " + channels(chosen.length) : "Vyberte kanály pro hromadnou změnu"));
  var off = el("button", null, "Vypnout vybrané");
  var on = el("button", null, "Zapnout vybrané");
  off.disabled = busy || !chosen.some(function (c) { return c.offered; });
  on.disabled = busy || !chosen.some(function (c) { return !c.offered || !c.received; });
  off.addEventListener("click", function () { pending = "bulk"; render(); });
  on.addEventListener("click", function () { send(chosen, true); });
  bar.appendChild(off);
  bar.appendChild(on);
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
  if (!rows.length) { list.appendChild(el("div", "row", "TVHeadend nemá žádné kanály.")); return; }
  rows.forEach(function (c) {
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
    info.appendChild(el("div", "name", c.name));
    info.appendChild(nowLine(c));
    var meta = el("div", "meta");
    function flag(text, ok) {
      if (meta.textContent) meta.appendChild(document.createTextNode(" · "));
      meta.appendChild(el("span", ok ? null : "no", text));
    }
    if (c.multiplexes.length) flag("multiplex " + c.multiplexes.join(", "), true);
    if (c.offered) {
      flag(c.received ? "příjem zapnutý" : "příjem vypnutý", c.received);
      if (c.recordingRule !== null) flag(c.recordingRule ? "nahrává se" : "nenahrává se", c.recordingRule);
    } else {
      flag(c.received ? "služba se stále přijímá" : "nepřijímá se", !c.received);
    }
    if (c.recordings) flag("nahrávek: " + c.recordings, true);
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
    if (pending === c.id) {
      row.appendChild(confirmBox(c.recordings
        ? "Vypnout " + c.name + "? Smažou se i jeho nahrávky (" + c.recordings + ")."
        : "Vypnout " + c.name + "?", function () { send([c], false); }));
    }
    list.appendChild(row);
  });
}

function confirmBox(text, action) {
  var box = el("div", "confirm");
  box.appendChild(el("span", null, text));
  var yes = el("button", "danger", "Vypnout");
  var no = el("button", null, "Zpět");
  yes.addEventListener("click", function () { pending = null; action(); });
  no.addEventListener("click", function () { pending = null; render(); });
  box.appendChild(yes);
  box.appendChild(no);
  setTimeout(function () { yes.focus(); }, 0);
  return box;
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
    renderStatus(data);
    render();
  }, function (e) { say("Nepodařilo se načíst kanály: " + e.message, true); });
}
load();
// keep "now playing" current, but never while the user is in the middle of something
setInterval(function () { if (!busy && pending === null) load(); }, 60000);
</script>
</body>
</html>
"""
