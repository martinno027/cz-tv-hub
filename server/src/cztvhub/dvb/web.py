"""The household web app's files at ``/web/`` (spec/web.md, spec/dvb-gateway.md "Web").

Only files that exist under ``[dvb] web_dir`` (copied there by ``install.sh``), no key needed: the page carries no
data and has to load before it can ask for the key. Everything else is 404: paths outside the directory (also through
``..``, encoded dots or slashes, or a symlink leaving it), dot files, directories, unknown types. The web is off when
``web_dir`` overlaps a directory holding recordings or state, so a config mistake cannot publish them without a key.
"""
from __future__ import annotations

import hashlib
import urllib.parse
from pathlib import Path
from typing import Callable, Iterable, Mapping

from ..config import Config
from .gateway import Response

PREFIX = "/web/"

# Hosts a page may reach besides itself (CSP). Only what ČT's playback needs, measured on 2026-11-01 from ČT's
# own answers (live CH_1, CH_24 main + timeshift, one archive item):
# - the signed playlist URL ČT hands out is on ivys-nw-cdn.o2tv.cz (answered there, no redirect);
# - its variant playlists and segments are on numbered edges ivys-stNN-N.o2-tv.cz (another domain: o2-tv.cz);
# - programme images (guide, archive cards) and subtitle files are on ctfs.ceskatelevize.cz; the app loads images
#   as blobs (XHR) and falls back to an <img>, so it is in connect-src and img-src, never media-src.
# ČT's API (api.ceskatelevize.cz) is not here: the page asks the gateway (/dvb/ct/*).
# The Chamber of Deputies' live streams (approved for the web by the owner on 2026-11-01, T28) come straight from
# its CDN: the playlist URL of psp.js; it answers any origin (Access-Control-Allow-Origin: *, checked 2026-11-01
# outside a broadcast). Variant playlists and segments are taken to be on the same host; check during a broadcast.
CT_STREAM_HOSTS = ("https://ivys-nw-cdn.o2tv.cz", "https://*.o2-tv.cz")
CT_IMAGE_HOSTS = ("https://ctfs.ceskatelevize.cz",)
CHAMBER_STREAM_HOSTS = ("https://pspcr-ott-live.ssl.cdn.cra.cz",)


def _csp() -> str:
    streams = " ".join(CT_STREAM_HOSTS + CHAMBER_STREAM_HOSTS)
    images = " ".join(CT_IMAGE_HOSTS)
    return "; ".join([
        "default-src 'self'",
        "script-src 'self'",  # no inline scripts, no eval
        "style-src 'self'",
        f"img-src 'self' blob: {images}",
        f"media-src 'self' blob: {streams}",
        f"connect-src 'self' {streams} {images}",
        "worker-src 'self' blob:",  # hls.js runs its demuxer in a worker made from a blob URL
        "manifest-src 'self'",  # manifest.webmanifest: own window (T35); default-src covers it, explicit on purpose
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ])


CSP = _csp()
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff2": "font/woff2",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",  # the web app manifest (spec/web.md T35)
}
TEXT = "text/plain; charset=utf-8"


def overlaps(a: Path, b: Path) -> bool:
    """``a`` is, contains or sits inside ``b`` (both resolved, so symlinks count where they point)."""
    a, b = a.resolve(), b.resolve()
    return a == b or a in b.parents or b in a.parents


def protected_dirs(cfg: Config, config_path: str | Path | None = None) -> dict[str, Path]:
    """Directories the web must never touch: recordings, sessions, state, ad breaks and the config's own."""
    dirs = {"hls_dir": cfg.dvb.hls_dir, "buffer_dir": cfg.dvb.buffer_dir, "state_dir": cfg.dvb.state_dir,
            "breaks_dir": cfg.ads.breaks_dir}
    if config_path is not None:
        dirs["config"] = Path(config_path).parent
    return dirs


def start_problem(web_dir: Path, protected: Mapping[str, Path]) -> str | None:
    """Why the web stays off, or None."""
    for name, path in protected.items():
        if overlaps(web_dir, path):
            return f"web_dir {web_dir} overlaps {name} {path}"
    if not (web_dir / "index.html").is_file():
        return f"no index.html in {web_dir} (install.sh copies the web app there)"
    return None


class WebSite:
    def __init__(self, web_dir: Path, protected: Mapping[str, Path], log: Callable[[str], None] = print):
        problem = start_problem(web_dir, protected)
        self.root: Path | None = None if problem else web_dir.resolve()
        if problem:
            log(f"web: off ({problem})")
        else:
            log(f"web: /web/ from {self.root}")

    @classmethod
    def from_config(cls, cfg: Config, config_path: str | Path | None = None,
                    log: Callable[[str], None] = print) -> "WebSite":
        return cls(cfg.dvb.web_dir, protected_dirs(cfg, config_path), log)

    @property
    def enabled(self) -> bool:
        return self.root is not None

    def handle(self, method: str, path: str, headers: Mapping[str, str]) -> Response:
        """``path`` as the request line had it (still percent-encoded, without the query)."""
        if method not in ("GET", "HEAD"):
            return _answer(405, b"method not allowed\n", TEXT, {"Allow": "GET, HEAD"})
        if path == "/web":
            return _answer(301, b"", TEXT, {"Location": PREFIX})
        file = self._file(path)
        if file is None:
            return _answer(404, b"not found\n", TEXT)
        data = file.read_bytes()
        etag = '"' + hashlib.sha256(data).hexdigest()[:32] + '"'
        extra = {"ETag": etag, "Cache-Control": "no-cache"}
        if _matches(headers.get("If-None-Match"), etag):
            return _answer(304, b"", CONTENT_TYPES[file.suffix.lower()], extra)
        return _answer(200, data, CONTENT_TYPES[file.suffix.lower()], extra)

    def _file(self, path: str) -> Path | None:
        if self.root is None or not path.startswith(PREFIX):
            return None
        raw = path[len(PREFIX):] or "index.html"
        if "\\" in raw or "%2f" in raw.lower() or "%5c" in raw.lower():
            return None  # an encoded or Windows separator is never part of a file name here
        try:
            rest = urllib.parse.unquote(raw, errors="strict")
        except UnicodeDecodeError:
            return None
        parts = rest.split("/")
        if any(not p or p.startswith(".") or "\0" in p for p in parts):
            return None  # empty segments (also a trailing slash = a directory), ".", "..", dot files
        candidate = self.root.joinpath(*parts)
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if self.root not in resolved.parents or not resolved.is_file():
            return None  # outside through a symlink, or a directory
        if resolved.suffix.lower() not in CONTENT_TYPES or candidate.suffix.lower() not in CONTENT_TYPES:
            return None
        return resolved


def _matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    tags: Iterable[str] = (t.strip() for t in header.split(","))
    return any(t == "*" or t.removeprefix("W/") == etag for t in tags)


def _answer(status: int, body: bytes, content_type: str, headers: dict[str, str] | None = None) -> Response:
    return Response(status, body, content_type, {**SECURITY_HEADERS, **(headers or {})})
