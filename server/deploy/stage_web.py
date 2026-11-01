"""Stage the household web app for ``[dvb] web_dir`` (spec/web.md): ``python3 deploy/stage_web.py TARGET``.

Called by ``install.sh`` as the normal user (git works there), which then copies TARGET to ``/opt/cztvhub/web``.

Mapping (the web page references the shared files as relative URLs ``app/js/core/x.js``):
- ``apps/web/``                → ``TARGET/``  (``index.html``, ``manifest.webmanifest``, ``icons/``, ``js/``, ``css/``,
                                  ``vendor/`` with hls.js + LICENSE; not ``tools/``: only served types are copied)
- ``apps/tizen/app/index.html`` → ``TARGET/app/index.html`` (the Samsung markup; ``apps/web/js/boot.js`` reads it
                                  and loads its scripts in order)
- ``apps/tizen/app/js|css|img`` → ``TARGET/app/js|css|img`` (the Samsung app's code, shared 1:1)
- build info                    → ``TARGET/js/build-info.js`` (``window.CT_BUILD_INFO``, the Samsung build's shape,
                                  where ``boot.js`` looks for it) for the version line "Verze … (web)"

Only regular files of the served types (plus LICENSE/NOTICE files) are copied; never symlinks, dot files, a
``local-config.js`` (a Samsung build's gateway preset can hold the real key), a leftover ``build-info.js``, test or
fixture directories, or the Samsung manifest ``config.xml``. Without ``apps/web/index.html`` nothing is staged and
the exit code is 3 (install.sh then leaves the web off).
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SERVED = {".html", ".js", ".css", ".svg", ".png", ".woff2", ".json", ".webmanifest"}  # = dvb/web.py CONTENT_TYPES
LEGAL = ("LICENSE", "NOTICE")
SKIP_DIRS = {"test", "tests", "fixtures", "node_modules", "dist"}
SKIP_FILES = {"local-config.js", "build-info.js"}
SHARED = ("js", "css", "img")
SHARED_FILES = ("index.html",)
NO_WEB = 3


def wanted(path: pathlib.Path, base: pathlib.Path) -> bool:
    rel = path.relative_to(base)
    if any(p.startswith(".") for p in rel.parts) or any(p in SKIP_DIRS for p in rel.parts[:-1]):
        return False
    if path.is_symlink() or not path.is_file() or path.name in SKIP_FILES:
        return False
    return path.suffix.lower() in SERVED or path.name.upper().startswith(LEGAL)


def copy_tree(src: pathlib.Path, dst: pathlib.Path) -> int:
    count = 0
    found = []
    for folder, dirs, names in os.walk(src, followlinks=False):  # never into a symlinked directory
        found += [pathlib.Path(folder) / n for n in names]
    for path in sorted(found):
        if wanted(path, src):
            target = dst / path.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            count += 1
    return count


def build_info(root: pathlib.Path) -> dict:
    """The Samsung build's info (version from config.xml, build number, commit, time): one implementation."""
    spec = importlib.util.spec_from_file_location("tizen_build", root / "apps" / "tizen" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_info()


def stage(target: pathlib.Path, root: pathlib.Path = ROOT, info: dict | None = None) -> int:
    """Fill ``target`` afresh; returns the number of files copied (0 = no web app in the tree)."""
    web, shared = root / "apps" / "web", root / "apps" / "tizen" / "app"
    if target.exists():
        shutil.rmtree(target)
    if not (web / "index.html").is_file():
        return 0
    target.mkdir(parents=True)
    count = copy_tree(web, target)
    for name in SHARED:
        if (shared / name).is_dir():
            count += copy_tree(shared / name, target / "app" / name)
    for name in SHARED_FILES:
        if wanted(shared / name, shared):
            (target / "app").mkdir(exist_ok=True)
            shutil.copyfile(shared / name, target / "app" / name)
            count += 1
    info = build_info(root) if info is None else info
    (target / "js").mkdir(exist_ok=True)
    (target / "js" / "build-info.js").write_text(
        "window.CT_BUILD_INFO = " + json.dumps(info, ensure_ascii=False) + ";\n", encoding="utf-8")
    return count + 1


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: stage_web.py TARGET", file=sys.stderr)
        return 2
    count = stage(pathlib.Path(args[0]))
    if count == 0:
        print(f"web: no apps/web/index.html in {ROOT}: the web app is not installed", file=sys.stderr)
        return NO_WEB
    print(f"web: {count} files staged in {args[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
