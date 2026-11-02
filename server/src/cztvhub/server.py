"""Tiny HTTP server: playlist + per-channel redirect to a fresh signed stream URL.

With DVB enabled in the config it also serves the ``/dvb/*`` gateway (spec/dvb-gateway.md),
and with ``[admin] password`` the channel administration at ``/admin``. With the Tailscale certificate in
``/var/lib/cztvhub/tls/`` the same handler also answers HTTPS on port 8443 (tls.py, T47).
"""
from __future__ import annotations

import http.server
import re
import socketserver
import ssl
import sys
import traceback
import urllib.parse
from typing import TYPE_CHECKING

from . import logsafe, tls
from .api import StreamError, StreamResolver
from .channels import BY_ID
from .dvb.profiles import MAX_BODY  # the largest body: a profile's 64 KB settings plus its wrapper
from .m3u import build_playlist

if TYPE_CHECKING:
    from .dvb.admin import AdminSite
    from .dvb.gateway import DvbGateway
    from .dvb.web import WebSite

STREAM_RE = re.compile(r"^/ct/(CH_\d+)\.m3u8$")
DVB_DISABLED = '{"error": "DVB není zapnuté"}'.encode("utf-8")
TOO_LARGE = '{"error": "Příliš velký požadavek"}'.encode("utf-8")


def make_handler(resolver: StreamResolver, gateway: DvbGateway | None = None, admin: AdminSite | None = None,
                 web: WebSite | None = None):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status, body=b"", content_type="text/plain; charset=utf-8", headers=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if status != 304:  # a 304 has no body; its length would be the 200's
                self.send_header("Content-Length", str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD" and status != 304:
                self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path.startswith("/dvb/"):
                return self._dvb(path, query)
            if _is_web(path):
                return self._web(path)
            if path == "/admin" or path.startswith("/admin/"):
                return self._admin(path, query=query)
            if path == "/health":
                return self._send(200, b"ok\n")
            if path in ("/playlist.m3u", "/ct.m3u"):
                host = self.headers.get("Host") or "127.0.0.1"
                scheme = "https" if isinstance(self.connection, ssl.SSLSocket) else "http"
                body = build_playlist(f"{scheme}://{host}").encode("utf-8")
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
            if _is_web(path):
                return self._web(path)
            body = self._body(path.startswith("/dvb/"))
            if body is not None:
                self._dvb(path, query, "PUT", body)

        def do_POST(self):
            path, _, query = self.path.partition("?")
            if _is_web(path):
                return self._web(path)
            body = self._body(path.startswith("/dvb/") or path.startswith("/admin/"))
            if body is None:
                return
            if path.startswith("/dvb/"):
                return self._dvb(path, query, "POST", body)
            self._admin(path, "POST", body)

        def _other(self):
            """DELETE, PATCH, OPTIONS: 405 on /web (spec), as before (501) elsewhere."""
            if _is_web(self.path.partition("?")[0]):
                return self._web(self.path.partition("?")[0])
            self.close_connection = True
            self.send_error(501, "Unsupported method (%r)" % self.command)

        do_DELETE = do_PATCH = do_OPTIONS = _other

        def _web(self, path):
            """The web app's files (dvb/web.py); only with the gateway on."""
            if self.command not in ("GET", "HEAD"):
                self.close_connection = True  # a body, if any, is not read
            if web is None or not web.enabled:
                return self._send(404, b"web is off\n")
            resp = web.handle(self.command, path, self.headers)
            self._send(resp.status, resp.body, resp.content_type, resp.headers)

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
            resp = admin.handle(method, path, self.headers, body, dict(urllib.parse.parse_qsl(query)),
                                self.client_address[0])
            self._send(resp.status, resp.body, resp.content_type, resp.headers)

        def _dvb(self, path, query, method="GET", body=b""):
            if gateway is None:
                return self._send(404, DVB_DISABLED, "application/json; charset=utf-8")
            params = dict(urllib.parse.parse_qsl(query))
            resp = gateway.handle(path, params, self.headers, method, body, self.client_address[0])
            self._send(resp.status, resp.body, resp.content_type, resp.headers)

        def parse_request(self):
            self.path = ""  # a malformed request on a kept-alive connection must not log the previous path
            return super().parse_request()

        # No per-request log (spec "Sign-in log", O019): successful requests write nothing, errors only the
        # status, method and path without the query string; never the address, never a header.
        def log_request(self, code="-", size="-"):
            status = code.value if isinstance(code, http.HTTPStatus) else code
            try:
                failed = int(status) >= 400
            except (TypeError, ValueError):
                failed = True
            if failed:
                path = logsafe.route(getattr(self, "path", "") or "")  # no query, no channel/recording id
                print(f"http: {status} {self.command or '-'} {path or '-'}", flush=True)

        def log_error(self, fmt, *args):
            pass  # send_error's text can quote the raw request line (with its query); log_request reports the status

        def log_message(self, fmt, *args):
            pass  # BaseHTTPRequestHandler's own lines carry the address; nothing else calls it

    return Handler



def _is_web(path: str) -> bool:
    return path == "/web" or path.startswith("/web/")


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        """An exception while handling a request: the traceback, without the client's address."""
        print("http: exception while handling a request", file=sys.stderr, flush=True)
        traceback.print_exc()


def serve(host: str, port: int, resolver: StreamResolver | None = None, gateway: DvbGateway | None = None,
          admin: AdminSite | None = None, web: WebSite | None = None, https_port: int = tls.HTTPS_PORT,
          tls_dir=tls.TLS_DIR):
    handler = make_handler(resolver or StreamResolver(), gateway, admin, web)
    server = Server((host, port), handler)
    https = https_listener(host, https_port, tls_dir, handler)
    if gateway is not None:
        gateway.start()
    dvb = ", DVB gateway: /dvb/" if gateway is not None else ""
    dvb += ", admin: /admin" if admin is not None else ""
    dvb += ", web: /web/" if web is not None and web.enabled else ""
    print(f"cz-tv-hub listening on {host}:{port}  (playlist: /playlist.m3u{dvb})", flush=True)
    https.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        https.stop()
        if gateway is not None:
            gateway.stop()


def https_listener(host: str, port: int, tls_dir, handler, log=print, retry: float = tls.RETRY_SECONDS):
    """The HTTPS listener for ``handler`` (the plain port's handler class): off until the certificate is there."""
    server_class = tls.https_server_class(Server)

    def make_server(address, files):
        server = server_class(address, handler)
        server.tls = files
        return server

    return tls.HttpsListener(host, port, tls.TlsFiles(tls_dir, log=log), make_server, log=log, retry=retry)
