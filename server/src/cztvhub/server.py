"""Tiny HTTP server: playlist + per-channel redirect to a fresh signed stream URL.

With DVB enabled in the config it also serves the ``/dvb/*`` gateway (spec/dvb-gateway.md),
and with ``[admin] password`` the channel administration at ``/admin``.
"""
from __future__ import annotations

import http.server
import re
import socketserver
import urllib.parse
from typing import TYPE_CHECKING

from .api import StreamError, StreamResolver
from .channels import BY_ID
from .dvb.profiles import MAX_BODY  # the largest body: a profile's 64 KB settings plus its wrapper
from .m3u import build_playlist

if TYPE_CHECKING:
    from .dvb.admin import AdminSite
    from .dvb.gateway import DvbGateway

STREAM_RE = re.compile(r"^/ct/(CH_\d+)\.m3u8$")
DVB_DISABLED = '{"error": "DVB není zapnuté"}'.encode("utf-8")
TOO_LARGE = '{"error": "Příliš velký požadavek"}'.encode("utf-8")


def make_handler(resolver: StreamResolver, gateway: DvbGateway | None = None, admin: AdminSite | None = None):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status, body=b"", content_type="text/plain; charset=utf-8", headers=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path.startswith("/dvb/"):
                return self._dvb(path, query)
            if path == "/admin" or path.startswith("/admin/"):
                return self._admin(path, query=query)
            if path == "/health":
                return self._send(200, b"ok\n")
            if path in ("/playlist.m3u", "/ct.m3u"):
                host = self.headers.get("Host") or "127.0.0.1"
                body = build_playlist(f"http://{host}").encode("utf-8")
                return self._send(200, body, "audio/x-mpegurl; charset=utf-8")
            match = STREAM_RE.match(path)
            if match and match.group(1) in BY_ID:
                try:
                    url = resolver.resolve(match.group(1))
                except StreamError as exc:
                    return self._send(502, f"{exc}\n".encode("utf-8"))
                return self._send(302, b"", headers={"Location": url, "Cache-Control": "no-store"})
            self._send(404, b"not found\n")

        def do_PUT(self):
            path, _, query = self.path.partition("?")
            body = self._body(path.startswith("/dvb/"))
            if body is not None:
                self._dvb(path, query, "PUT", body)

        def do_POST(self):
            path, _, query = self.path.partition("?")
            body = self._body(path.startswith("/dvb/") or path.startswith("/admin/"))
            if body is None:
                return
            if path.startswith("/dvb/"):
                return self._dvb(path, query, "POST", body)
            self._admin(path, "POST", body)

        def _body(self, routed: bool):
            """The request body, or None after answering: 400 elsewhere or without a length, 413 too large."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not routed or length < 0:
                self.close_connection = True
                return self._send(400, b"bad request\n")
            if length > MAX_BODY:
                self.close_connection = True  # the body is not read
                return self._send(413, TOO_LARGE, "application/json; charset=utf-8")
            return self.rfile.read(length)

        def _admin(self, path, method="GET", body=b"", query=""):
            if admin is None:
                return self._send(404, b"admin is off ([admin] password in the config)\n")
            resp = admin.handle(method, path, self.headers, body, dict(urllib.parse.parse_qsl(query)))
            self._send(resp.status, resp.body, resp.content_type, resp.headers)

        def _dvb(self, path, query, method="GET", body=b""):
            if gateway is None:
                return self._send(404, DVB_DISABLED, "application/json; charset=utf-8")
            params = dict(urllib.parse.parse_qsl(query))
            resp = gateway.handle(path, params, self.headers, method, body, self.client_address[0])
            self._send(resp.status, resp.body, resp.content_type, resp.headers)

        def log_message(self, fmt, *args):
            print(self.address_string(), fmt % args, flush=True)

    return Handler


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(host: str, port: int, resolver: StreamResolver | None = None, gateway: DvbGateway | None = None,
          admin: AdminSite | None = None):
    server = Server((host, port), make_handler(resolver or StreamResolver(), gateway, admin))
    if gateway is not None:
        gateway.start()
    dvb = ", DVB gateway: /dvb/" if gateway is not None else ""
    dvb += ", admin: /admin" if admin is not None else ""
    print(f"cz-tv-hub listening on {host}:{port}  (playlist: /playlist.m3u{dvb})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if gateway is not None:
            gateway.stop()
