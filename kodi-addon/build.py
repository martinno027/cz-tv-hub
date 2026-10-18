"""Build the installable Kodi add-on ZIP: ``python3 kodi-addon/build.py [--out dist] [--serve]``.

``--serve`` then offers the output folder over HTTP on the home LAN, so devices
without a USB port (Apple TV, Android TV) can add it as a Kodi file source.

Bundles kodi-addon/plugin.video.cztvhub plus the client-side modules of the cztvhub
package (the server is not needed on Kodi devices).
"""
import argparse
import functools
import http.server
import pathlib
import re
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
ADDON_DIR = ROOT / "kodi-addon" / "plugin.video.cztvhub"
PACKAGE_DIR = ROOT / "server" / "src" / "cztvhub"
BUNDLED_MODULES = ["__init__.py", "api.py", "channels.py", "guide.py", "plugin.py"]


def addon_version() -> str:
    text = (ADDON_DIR / "addon.xml").read_text(encoding="utf-8")
    return re.search(r'<addon [^>]*version="([^"]+)"', text).group(1)


def build(out_dir: pathlib.Path) -> pathlib.Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{ADDON_DIR.name}-{addon_version()}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(ADDON_DIR.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                zf.write(path, f"{ADDON_DIR.name}/{path.relative_to(ADDON_DIR).as_posix()}")
        for name in BUNDLED_MODULES:
            zf.write(PACKAGE_DIR / name, f"{ADDON_DIR.name}/resources/lib/cztvhub/{name}")
    return target


def serve(directory: pathlib.Path, port: int) -> None:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    with http.server.ThreadingHTTPServer(("0.0.0.0", port), handler) as server:
        print(f"Serving {directory} on port {port} (home LAN only). In Kodi add the source "
              f"http://<this computer's LAN IP>:{port}/  Stop with Ctrl+C.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "dist"), help="output directory (default: dist/)")
    parser.add_argument("--serve", action="store_true", help="serve the output directory over HTTP afterwards")
    parser.add_argument("--port", type=int, default=8099, help="port for --serve (default: 8099)")
    args = parser.parse_args(argv)
    out = pathlib.Path(args.out)
    print(build(out))
    if args.serve:
        serve(out, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
