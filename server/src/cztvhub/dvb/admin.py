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
from typing import Any, Callable, Mapping

from ..config import Config
from .gateway import DvbGateway, Response, error, json_response
from .setup import RULE_COMMENT, apply_setup, plan_setup, recording_set_up
from .tvheadend import MUX_DISABLE, MUX_ENABLE, Channel, TvheadendClient, TvhError, TvhNotFound

ADMIN_USER = "admin"
CHANNEL_RE = re.compile(r"^/admin/api/channels/([A-Za-z0-9_-]{1,64})$")
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

    def __init__(self, cfg: Config, client: TvheadendClient, log: Callable[[str], None] = print):
        self.cfg, self.client, self.log = cfg, client, log

    def overview(self) -> list[dict]:
        channels = self.client.channels(include_disabled=True)
        services = {s.uuid: s for s in self.client.services()}
        muxes = {m.uuid: m for m in self.client.muxes()}
        rules = [r for r in self.client.autorecs() if r.comment == RULE_COMMENT]
        recordings: dict[str, int] = {}
        for entry in self.client.dvr_entries("grid") + self.client.dvr_entries("grid_finished") + \
                self.client.dvr_entries("grid_failed"):
            recordings[entry.channel] = recordings.get(entry.channel, 0) + 1
        rows = []
        for channel in channels:
            own = [services[s] for s in channel.services if s in services]
            mux_ids = sorted({s.mux for s in own if s.mux})
            rule = next((r for r in rules if r.channel == channel.uuid), None)
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
            })
        rows.sort(key=lambda r: (not r["offered"], r["number"] <= 0, r["number"], r["name"].casefold()))
        return rows

    def set_offered(self, channel_id: str, offered: bool) -> list[str]:
        """Show or hide one channel; returns what changed, in Czech, for the page."""
        channels = self.client.channels(include_disabled=True)
        channel = next((c for c in channels if c.uuid == channel_id), None)
        if channel is None:
            raise TvhNotFound(channel_id)
        done: list[str] = []
        if channel.enabled != offered:
            self.client.save_node([{"uuid": channel.uuid, "enabled": offered}])
            done.append("kanál zapnut" if offered else "kanál vypnut")
        channel.enabled = offered
        done += self._reception(channel, channels)
        done += self._recording(channel)
        self.log(f"admin: {channel.name} {'shown' if offered else 'hidden'}: {', '.join(done) or 'no change'}")
        return done

    def _reception(self, channel: Channel, channels: list[Channel]) -> list[str]:
        services = {s.uuid: s for s in self.client.services()}
        offered_services = {s for c in channels if c.enabled for s in c.services}
        # a service shared with another offered channel stays on
        switch = [services[s] for s in channel.services if s in services
                  and services[s].enabled != channel.enabled and (channel.enabled or s not in offered_services)]
        if switch:
            self.client.save_node([{"uuid": s.uuid, "enabled": channel.enabled} for s in switch])
        done = [f"služby {'zapnuty' if channel.enabled else 'vypnuty'}: {len(switch)}"] if switch else []

        muxes = {m.uuid: m for m in self.client.muxes()}
        own = {services[s].mux for s in channel.services if s in services and services[s].mux}
        changes = []
        for mux_id in sorted(own):
            mux = muxes.get(mux_id)
            if mux is None:
                continue
            in_use = any(services[s].mux == mux_id for s in offered_services if s in services)
            if channel.enabled and mux.enabled == MUX_DISABLE:
                changes.append((mux, MUX_ENABLE))
            elif not channel.enabled and not in_use and mux.enabled == MUX_ENABLE:
                changes.append((mux, MUX_DISABLE))
        if changes:
            self.client.save_node([{"uuid": m.uuid, "enabled": value} for m, value in changes])
            done += [f"multiplex {m.name or m.uuid} {'zapnut' if v == MUX_ENABLE else 'vypnut'}" for m, v in changes]
        return done

    def _recording(self, channel: Channel) -> list[str]:
        done = []
        if recording_set_up(self.client):  # only where dvb-setup has switched recording on
            plan = plan_setup(self.client, self.cfg.dvb)
            apply_setup(self.client, plan)
            if plan.toggle or plan.create:
                done.append("nahrávání zapnuto" if channel.enabled else "nahrávání vypnuto")
        if not channel.enabled:
            upcoming = [e.uuid for e in self.client.dvr_entries("grid_upcoming") if e.channel == channel.uuid]
            self.client.cancel_dvr_entries(upcoming)
            finished = [e.uuid for e in self.client.dvr_entries("grid_finished") +
                        self.client.dvr_entries("grid_failed") if e.channel == channel.uuid]
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
        self.admin = admin or ChannelAdmin(cfg, gateway.client, gateway.log)

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
            if method == "POST" and (m := CHANNEL_RE.match(path)):
                if not self._same_origin(headers):
                    return error(403, "Požadavek z cizí stránky")
                return self._set(m.group(1), body)
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

    def _set(self, channel_id: str, body: bytes) -> Response:
        try:
            payload: Any = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            payload = None
        if not isinstance(payload, dict) or not isinstance(payload.get("offered"), bool):
            return error(400, 'Očekávám {"offered": true|false}')
        offered = payload["offered"]
        try:
            done = self.admin.set_offered(channel_id, offered)
        finally:
            self.gateway.channels_changed(hidden=None if offered else channel_id)
        return json_response(200, {"changes": done})


PAGE = """<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>cz-tv-hub – správa</title>
<style>
:root { color-scheme: light dark; --bg: #f6f6f4; --card: #fff; --fg: #1d1d1b; --muted: #6b6b66; --line: #e2e2dc;
  --accent: #1f6feb; --off: #b4b4ad; --warn: #b3261e; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #151514; --card: #1f1f1d; --fg: #ecece8; --muted: #9a9a93; --line: #33332f; --accent: #6ea8ff;
    --off: #55554f; --warn: #ff8a80; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.45 system-ui, -apple-system, sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 22px; margin: 0 0 4px; }
.sub { color: var(--muted); margin: 0 0 20px; }
.status { display: flex; flex-wrap: wrap; gap: 8px 20px; color: var(--muted); font-size: 13px; margin-bottom: 16px; }
.status b { color: var(--fg); font-weight: 600; }
.list { background: var(--card); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
.row { display: grid; grid-template-columns: 44px 1fr auto; gap: 12px; align-items: center; padding: 12px 16px;
  border-top: 1px solid var(--line); }
.row:first-child { border-top: 0; }
.row.hidden .name { color: var(--muted); }
.num { color: var(--muted); font-variant-numeric: tabular-nums; text-align: right; }
.name { font-weight: 600; }
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
.confirm { grid-column: 2 / 4; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 14px; }
button { font: inherit; border-radius: 6px; border: 1px solid var(--line); background: var(--card); color: var(--fg);
  padding: 5px 12px; cursor: pointer; }
button.danger { background: var(--warn); border-color: var(--warn); color: #fff; }
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
<div class="list" id="list"><div class="row"><span></span><span class="meta">Načítám…</span></div></div>
<p class="help">Vypnutím se zruší plánované nahrávky kanálu a smažou jeho nahrávky. Multiplex se vypne, až
v něm nezbude žádný zapnutý kanál. Po zapnutí se nahrává od dalšího pořadu; programový průvodce se doplní,
až tuner multiplex znovu naladí (může trvat desítky minut).</p>
</main>
<script>
"use strict";
var list = document.getElementById("list"), msg = document.getElementById("msg");
var rows = [], pending = null;

function el(tag, cls, text) {
  var e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function say(text, isError) {
  msg.textContent = "";
  if (!text) return;
  var box = el("div", "msg" + (isError ? " err" : ""), text);
  msg.appendChild(box);
}
function gb(n) { return (n / 1e9).toFixed(n >= 1e11 ? 0 : 1) + " GB"; }

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
  item("Nahrávání:", data.recordingSetUp ? "zapnuté, " + data.retentionDays + (data.retentionDays === 1 ? " den" : data.retentionDays < 5 ? " dny" : " dní") : "není nastavené (dvb-setup)");
}

function render() {
  list.textContent = "";
  if (!rows.length) { list.appendChild(el("div", "row", "TVHeadend nemá žádné kanály.")); return; }
  rows.forEach(function (c) {
    var row = el("div", "row" + (c.offered ? "" : " hidden"));
    row.appendChild(el("span", "num", c.number > 0 ? String(c.number) : ""));
    var info = el("div");
    info.appendChild(el("div", "name", c.name));
    var meta = el("div", "meta");
    var parts = [];
    if (c.multiplexes.length) parts.push("multiplex " + c.multiplexes.join(", "));
    meta.appendChild(document.createTextNode(parts.join(" · ")));
    function flag(text, ok) { if (meta.childNodes.length && meta.textContent) meta.appendChild(document.createTextNode(" · ")); meta.appendChild(el("span", ok ? null : "no", text)); }
    if (c.offered) {
      flag(c.received ? "příjem zapnutý" : "příjem vypnutý", c.received);
      if (!c.received && c.multiplexes.length) {
        var fix = el("button", null, "Zapnout příjem");
        fix.disabled = pending !== null;
        fix.style.marginLeft = "8px";
        fix.addEventListener("click", function () { send(c, true); });
        meta.appendChild(fix);
      }
      if (c.recordingRule !== null) flag(c.recordingRule ? "nahrává se" : "nenahrává se", c.recordingRule);
    } else {
      flag(c.received ? "služba se stále přijímá" : "nepřijímá se", !c.received);
    }
    if (c.recordings) flag("nahrávek: " + c.recordings, true);
    info.appendChild(meta);
    row.appendChild(info);
    var sw = el("label", "switch");
    var input = el("input");
    input.type = "checkbox";
    input.checked = c.offered;
    input.setAttribute("aria-label", "Nabízet kanál " + c.name);
    input.disabled = pending !== null;
    input.addEventListener("change", function () { toggle(c, input); });
    sw.appendChild(input);
    sw.appendChild(el("span"));
    row.appendChild(sw);
    if (pending === c.id) row.appendChild(confirmBox(c));
    list.appendChild(row);
  });
}

function confirmBox(c) {
  var box = el("div", "confirm");
  box.appendChild(el("span", null, c.recordings
    ? "Vypnout " + c.name + "? Smažou se i jeho nahrávky (" + c.recordings + ")."
    : "Vypnout " + c.name + "?"));
  var yes = el("button", "danger", "Vypnout");
  var no = el("button", null, "Zpět");
  yes.addEventListener("click", function () { pending = null; send(c, false); });
  no.addEventListener("click", function () { pending = null; render(); });
  box.appendChild(yes);
  box.appendChild(no);
  setTimeout(function () { yes.focus(); }, 0);
  return box;
}

function toggle(c, input) {
  if (!input.checked) { pending = c.id; render(); return; }
  send(c, true);
}

function send(c, offered) {
  pending = "busy";
  render();
  say(offered ? "Zapínám " + c.name + "…" : "Vypínám " + c.name + "…");
  api("POST", "/admin/api/channels/" + encodeURIComponent(c.id), {offered: offered}).then(function (data) {
    say(c.name + ": " + (data.changes.length ? data.changes.join(", ") : "beze změny") + ".");
  }, function (e) {
    say(c.name + ": " + e.message, true);
  }).then(function () { pending = null; return load(); });
}

function load() {
  return api("GET", "/admin/api/channels").then(function (data) {
    rows = data.channels;
    renderStatus(data);
    render();
  }, function (e) { say("Nepodařilo se načíst kanály: " + e.message, true); });
}
load();
</script>
</body>
</html>
"""
