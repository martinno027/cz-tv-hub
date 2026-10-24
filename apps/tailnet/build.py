#!/usr/bin/env python3
"""Builds the Tailscale bridge (package tailnet) for the TV apps (spec/apps.md "Tailscale v aplikaci").

    python3 apps/tailnet/build.py android   # dist/android/tailnet.aar (gomobile bind)
    python3 apps/tailnet/build.py tvos      # dist/tvos/{appletvos,appletvsimulator}/libtailnet.a + include/
    python3 apps/tailnet/build.py all

Called by the apps' builds (Gradle task, Xcode build phase); does nothing when the output is newer than every source.
Needs Go (the version in go.mod is fetched by Go itself); android also the NDK and a JDK, tvos Xcode.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"
BIN = HERE / ".bin"
TAGS = "ts_omit_logtail"
TVOS_MIN = "17.0"
ANDROID_API = "26"
MODULEMAP = 'module Tailnet {\n    header "libtailnet.h"\n    link "tailnet"\n    export *\n}\n'


def fail(msg: str) -> None:
    sys.exit(f"tailnet build: {msg}")


def go() -> str:
    for cand in (shutil.which("go"), "/opt/homebrew/bin/go", "/usr/local/go/bin/go", "/usr/local/bin/go"):
        if cand and Path(cand).exists():
            return cand
    fail("Go not found (install it: https://go.dev/dl/ or `brew install go`)")
    return ""


def sources() -> list[Path]:
    return [p for p in HERE.rglob("*") if p.suffix == ".go" or p.name in ("go.mod", "go.sum", "build.py")
            if ".bin" not in p.parts and "dist" not in p.parts]


def up_to_date(outputs: list[Path]) -> bool:
    if not all(o.exists() for o in outputs):
        return False
    newest = max(p.stat().st_mtime for p in sources())
    return min(o.stat().st_mtime for o in outputs) > newest


def run(cmd: list[str], env: dict[str, str], outputs: tuple[Path, ...] = ()) -> None:
    print("+", " ".join(cmd), flush=True)
    res = subprocess.run(cmd, cwd=HERE, env=env)
    if res.returncode != 0:
        for out in outputs:  # a half-written output would look up to date next time
            out.unlink(missing_ok=True)
        fail(f"{Path(cmd[0]).name} {cmd[1]} failed with exit {res.returncode} (output above)")


def base_env() -> dict[str, str]:
    env = dict(os.environ)
    gobin = str(Path(go()).parent)
    env["PATH"] = os.pathsep.join([str(BIN), gobin, env.get("PATH", "")])
    return env


def ndk() -> str:
    for var in ("ANDROID_NDK_HOME", "ANDROID_NDK_LATEST_HOME", "ANDROID_NDK_ROOT"):
        if os.environ.get(var) and Path(os.environ[var]).is_dir():
            return os.environ[var]
    sdks = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
            str(Path.home() / "Library/Android/sdk"), str(Path.home() / "Android/Sdk")]
    for sdk in filter(None, sdks):
        versions = sorted((Path(sdk) / "ndk").glob("*"), key=lambda p: [int(x) for x in p.name.split(".") if x.isdigit()])
        if versions:
            return str(versions[-1])
    fail("Android NDK not found (Android Studio > SDK Manager > NDK, or set ANDROID_NDK_HOME)")
    return ""


def java_home() -> str | None:
    """gomobile runs javac; outside Gradle there may be no JAVA_HOME, then use Android Studio's JDK."""
    if os.environ.get("JAVA_HOME"):
        return os.environ["JAVA_HOME"]
    studio = Path("/Applications/Android Studio.app/Contents/jbr/Contents/Home")
    return str(studio) if studio.is_dir() else None


def android() -> None:
    aar = DIST / "android" / "tailnet.aar"
    if up_to_date([aar]):
        print(f"tailnet: {aar.relative_to(HERE)} is up to date")
        return
    env = base_env()
    env["ANDROID_NDK_HOME"] = ndk()
    env["GOBIN"] = str(BIN)
    jdk = java_home()
    if jdk:
        env["JAVA_HOME"] = jdk
        env["PATH"] = os.pathsep.join([str(Path(jdk) / "bin"), env["PATH"]])
    # gomobile and gobind at the version pinned in go.mod ("tool" lines).
    run([go(), "install", "golang.org/x/mobile/cmd/gomobile", "golang.org/x/mobile/cmd/gobind"], env)
    aar.parent.mkdir(parents=True, exist_ok=True)
    run([str(BIN / "gomobile"), "bind", "-target=android/arm64,android/arm", f"-androidapi={ANDROID_API}",
         "-javapkg=cz.cztvhub.bridge", f"-tags={TAGS}", "-trimpath", "-ldflags=-s -w", "-o", str(aar), "."], env, (aar,))
    for extra in aar.parent.glob("tailnet-sources.jar"):
        extra.unlink()


def tvos() -> None:
    sdks = {"appletvos": f"arm64-apple-tvos{TVOS_MIN}", "appletvsimulator": f"arm64-apple-tvos{TVOS_MIN}-simulator"}
    libs = [DIST / "tvos" / sdk / "libtailnet.a" for sdk in sdks]
    include = DIST / "tvos" / "include"
    if up_to_date(libs + [include / "libtailnet.h", include / "module.modulemap"]):
        print("tailnet: dist/tvos is up to date")
        return
    if not shutil.which("xcrun"):
        fail("xcrun not found: the tvOS library builds on a Mac with Xcode")
    for sdk, target in sdks.items():
        sdk_path = subprocess.run(["xcrun", "--sdk", sdk, "--show-sdk-path"], capture_output=True, text=True).stdout.strip()
        clang = subprocess.run(["xcrun", "--sdk", sdk, "-f", "clang"], capture_output=True, text=True).stdout.strip()
        if not sdk_path or not clang:
            fail(f"Xcode has no {sdk} SDK (Xcode > Settings > Components > tvOS)")
        env = base_env()
        flags = f"-isysroot {sdk_path} -target {target}"
        # Xcode exports its own settings (SDKROOT, deployment targets) to build phases; Go must not pick them up.
        for var in ("SDKROOT", "IPHONEOS_DEPLOYMENT_TARGET", "TVOS_DEPLOYMENT_TARGET", "MACOSX_DEPLOYMENT_TARGET"):
            env.pop(var, None)
        env.update(GOOS="ios", GOARCH="arm64", CGO_ENABLED="1", CC=f"{clang} {flags}", CGO_CFLAGS=flags,
                   CGO_LDFLAGS=flags)
        out = DIST / "tvos" / sdk / "libtailnet.a"
        out.parent.mkdir(parents=True, exist_ok=True)
        run([go(), "build", "-buildmode=c-archive", f"-tags=ios,{TAGS}", "-trimpath", "-ldflags=-s -w",
             "-o", str(out), "./cshim"], env, (out,))
    include.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DIST / "tvos" / "appletvos" / "libtailnet.h", include / "libtailnet.h")
    (include / "module.modulemap").write_text(MODULEMAP)


def main(argv: list[str]) -> int:
    what = argv[1] if len(argv) > 1 else ""
    if what not in ("android", "tvos", "all"):
        print(__doc__)
        return 2
    if what in ("android", "all"):
        android()
    if what in ("tvos", "all"):
        tvos()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
