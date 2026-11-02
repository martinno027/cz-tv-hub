"""HTTPS for the installed web app (spec/dvb-gateway.md "HTTPS for the installed web app", T47).

Browsers offer "Install" (an own window without any browser bar) only on a secure origin. Next to the plain HTTP
port the service can serve the **same handler** over TLS on port 8443 with the Pi's Tailscale certificate for its
MagicDNS name. Nothing to configure: the certificate files in ``/var/lib/cztvhub/tls/`` (written by the root job
``deploy/cert.sh``) switch it on; without them it is off and the plain port works as before. The files are checked
for every new connection and loaded again when they change (renewal), without a restart; a pair that does not load
(half-written, mismatched) leaves the previous one in use.
"""
from __future__ import annotations

import os
import socket
import ssl
import threading
from pathlib import Path
from typing import Callable

TLS_DIR = Path("/var/lib/cztvhub/tls")  # deploy/cert.sh writes here (owner cztvhub, key 0600)
CERT_FILE = "cert.pem"
KEY_FILE = "key.pem"
HTTPS_PORT = 8443
HANDSHAKE_SECONDS = 15.0  # a peer that never finishes the handshake does not hold a thread for ever
RETRY_SECONDS = 60.0  # no certificate yet, or the port is taken: try again (the cert job may run any time)


class TlsFiles:
    """The certificate pair of ``directory`` as an ``ssl.SSLContext``, loaded again when a file changes."""

    def __init__(self, directory: Path, log: Callable[[str], None] = print):
        self.cert = Path(directory) / CERT_FILE
        self.key = Path(directory) / KEY_FILE
        self.log = log
        self._lock = threading.Lock()
        self._context: ssl.SSLContext | None = None
        self._loaded: tuple | None = None  # signature of the files behind _context
        self._failed: tuple | None = None  # signature that last failed (logged once)

    def _signature(self) -> tuple | None:
        try:
            parts = []
            for path in (self.cert, self.key):
                st = os.stat(path)
                parts.append((st.st_ino, st.st_mtime_ns, st.st_size))
            return tuple(parts)
        except OSError:
            return None

    def present(self) -> bool:
        return self._signature() is not None

    def context(self) -> ssl.SSLContext | None:
        """The current context; a changed pair is loaded first. None only when nothing ever loaded."""
        signature = self._signature()
        with self._lock:
            if signature is None or signature in (self._loaded, self._failed):
                return self._context
            try:
                context = make_context(self.cert, self.key)
            except (OSError, ssl.SSLError, ValueError) as exc:
                self._failed = signature
                kept = "the previous one stays in use" if self._context is not None else "https stays off"
                self.log(f"https: certificate in {self.cert.parent} does not load ({_reason(exc)}); {kept}")
                return self._context
            if self._context is not None:
                self.log("https: certificate reloaded")
            self._context, self._loaded, self._failed = context, signature, None
            return context


def make_context(cert: Path, key: Path) -> ssl.SSLContext:
    """A server context: TLS 1.2 or newer, HTTP/1.1 only."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(cert), str(key))
    context.set_alpn_protocols(["http/1.1"])
    return context


def _reason(exc: BaseException) -> str:
    if isinstance(exc, ssl.SSLError):
        return exc.reason or type(exc).__name__
    if isinstance(exc, OSError):
        return exc.strerror or type(exc).__name__
    return type(exc).__name__


def https_server_class(base: type) -> type:
    """``base`` (server.Server) with TLS per connection: the handshake runs in the request's own thread, with the
    context of that moment, so a renewed certificate is used from the next connection on."""

    class HttpsServer(base):
        tls: TlsFiles

        def finish_request(self, request, client_address):
            context = self.tls.context()
            if context is None:
                return
            request.settimeout(HANDSHAKE_SECONDS)
            try:
                conn = context.wrap_socket(request, server_side=True)
            except (ssl.SSLError, OSError):
                return  # plain HTTP on this port, an untrusted certificate, a peer that left: nothing to answer
            try:
                conn.settimeout(None)  # as the plain port: keep-alive connections wait for the next request
                self.RequestHandlerClass(conn, client_address, self)
            except (ssl.SSLError, ConnectionError):
                pass  # the browser dropped the connection (TLS close or reset): nobody left to answer
            finally:
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                conn.close()

    return HttpsServer


class HttpsListener:
    """Starts the HTTPS server once the certificate is there (checked every ``retry`` seconds) and the port is
    free; logs each reason for being off once. ``make_server(address, tls)`` builds the server (tests: any port)."""

    def __init__(self, host: str, port: int, tls: TlsFiles, make_server: Callable, log: Callable[[str], None] = print,
                 retry: float = RETRY_SECONDS):
        self.host, self.port, self.tls = host, port, tls
        self.make_server = make_server
        self.log = log
        self.retry = retry
        self.server = None
        self._stop = threading.Event()
        self._started = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="https", daemon=True)
        self._thread.start()

    def wait_started(self, timeout: float) -> bool:
        return self._started.wait(timeout)

    def _run(self) -> None:
        said = None
        while not self._stop.is_set():
            reason = None
            if not self.tls.present():
                reason = f"no certificate in {self.tls.cert.parent} (cztvhub-cert.service makes it)"
            elif self.tls.context() is None:
                reason = None  # TlsFiles logged why the pair does not load
                said = "load"
            else:
                try:
                    self.server = self.make_server((self.host, self.port), self.tls)
                except OSError as exc:
                    reason = f"port {self.port}: {exc.strerror or type(exc).__name__}"
                else:
                    self.log(f"https: listening on {self.host}:{self.server.server_address[1]} (same routes as http)")
                    self._started.set()
                    self.server.serve_forever()
                    return
            if reason and reason != said:
                self.log(f"https: off ({reason})")
                said = reason
            self._stop.wait(self.retry)

    def stop(self) -> None:
        self._stop.set()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
