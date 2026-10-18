"""Build the Samsung TV (Tizen) app: ``python3 apps/tizen/build.py [--gateway ADDR --key-file FILE] [--install TV_IP]``.

Copies ``apps/tizen/app`` into ``dist/tizen/Televize`` and, when the Tizen Studio CLI (``tizen``, ``sdb``)
is available, packages and signs ``dist/Televize.wgt`` with the active certificate profile (Samsung
certificate created in Tizen Studio's Certificate Manager) and optionally installs it on a TV in
Developer Mode. Without the CLI it stops after the copy and prints what to do.

``--gateway``/``--key-file`` preset the "Anténa" gateway in the build only (``js/local-config.js``),
so the 43-character key does not have to be typed with the remote. The preset never enters git.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
APP_DIR = ROOT / "apps" / "tizen" / "app"
WGT_NAME = "Televize.wgt"
MIN_KEY_LENGTH = 32
SDB_PORT = 26101


def find_cli(name: str) -> str | None:
    """`tizen` / `sdb` from PATH or a default Tizen Studio install (``TIZEN_STUDIO`` overrides it)."""
    found = shutil.which(name)
    if found:
        return found
    studio = pathlib.Path(os.environ.get("TIZEN_STUDIO", pathlib.Path.home() / "tizen-studio"))
    for candidate in (studio / "tools" / "ide" / "bin" / name, studio / "tools" / name):
        if candidate.exists():
            return str(candidate)
    return None


def local_config(gateway: str, key: str) -> str:
    if len(key) < MIN_KEY_LENGTH:
        raise ValueError(f"the gateway key has at least {MIN_KEY_LENGTH} characters")
    preset = {"gatewayAddress": gateway.strip(), "gatewayKey": key}
    return "window.CT_LOCAL_CONFIG = " + json.dumps(preset) + ";\n"


def stage(out_dir: pathlib.Path, gateway: str | None = None, key: str | None = None) -> pathlib.Path:
    """Copy the app into ``out_dir/Televize`` (fresh), with the optional gateway preset."""
    target = out_dir / "Televize"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(APP_DIR, target, ignore=shutil.ignore_patterns(".*", "local-config.js"))
    if gateway and key:
        (target / "js" / "local-config.js").write_text(local_config(gateway, key), encoding="utf-8")
    return target


def package(tizen: str, project: pathlib.Path, out_dir: pathlib.Path, profile: str | None) -> pathlib.Path:
    build_dir = project / ".buildResult"
    subprocess.run([tizen, "build-web", "-out", ".buildResult", "--", str(project)], check=True)
    command = [tizen, "package", "-t", "wgt"]
    if profile:
        command += ["-s", profile]
    subprocess.run(command + ["--", str(build_dir)], check=True)
    built = sorted(build_dir.glob("*.wgt"))
    if not built:
        raise RuntimeError("tizen package produced no .wgt")
    wgt = out_dir / WGT_NAME
    shutil.copyfile(built[-1], wgt)
    return wgt


def install(tizen: str, sdb: str, wgt: pathlib.Path, tv: str) -> None:
    serial = tv if ":" in tv else f"{tv}:{SDB_PORT}"
    subprocess.run([sdb, "connect", serial], check=True)
    subprocess.run([tizen, "install", "-s", serial, "-n", wgt.name, "--", str(wgt.parent)], check=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(ROOT / "dist" / "tizen"), help="output directory (default: dist/tizen)")
    parser.add_argument("--gateway", help="preset Anténa gateway address (LAN / tailnet)")
    parser.add_argument("--key-file", help="file with the gateway key ([dvb] key of cztvhub.conf)")
    parser.add_argument("--profile", help="Tizen Studio certificate profile (default: the active one)")
    parser.add_argument("--install", metavar="TV_IP", help="install on this TV (Developer Mode, same LAN)")
    args = parser.parse_args(argv)
    if bool(args.gateway) != bool(args.key_file):
        parser.error("--gateway and --key-file go together")
    key = pathlib.Path(args.key_file).read_text(encoding="utf-8").strip() if args.key_file else None

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    project = stage(out, args.gateway, key)
    print(f"app copied to {project}")

    tizen = find_cli("tizen")
    if not tizen:
        print("Tizen Studio CLI not found (PATH or ~/tizen-studio; set TIZEN_STUDIO). Install Tizen Studio with the "
              "TV extension and a Samsung certificate, then run this again (see apps/tizen/README.md).")
        return 1
    wgt = package(tizen, project, out, args.profile)
    print(wgt)
    if args.install:
        sdb = find_cli("sdb")
        if not sdb:
            print("sdb not found next to the tizen CLI")
            return 1
        install(tizen, sdb, wgt, args.install)
    return 0


if __name__ == "__main__":
    sys.exit(main())
