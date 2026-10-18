"""Guard for the public repo: fails if private or forbidden files/content would be published.

Usage:
  python3 tools/check_public.py            # check all tracked files (CI)
  python3 tools/check_public.py --staged   # check files staged for commit (pre-commit hook)

Extra patterns (e.g. personal names, home IPs) can be listed one regex per line in the file named by
the CZTVHUB_PRIVATE_PATTERNS environment variable; that file lives outside this repo.
"""
import argparse
import os
import pathlib
import re
import subprocess
import sys

# Files that must never be in the public repo.
FORBIDDEN_PATHS = [
    re.compile(r"(^|/)CLAUDE(\.local)?\.md$"),
    re.compile(r"(^|/)\.claude(/|$)"),
    re.compile(r"^notes/"),
    re.compile(r"\.(zip|apk|aab|ipa)$"),
    re.compile(r"(^|/)\.env$"),
    re.compile(r"(^|/)local\.properties$"),
]

# Content that must never be published (checked in text files).
FORBIDDEN_CONTENT = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"(?i)\b(api|bearer)[_-]?key\s*=\s*(?!dev-key-|<|@KEY@|\.\.\.)[A-Za-z0-9_\-]{24,}"), "secret key"),
]

# Shared test data must stay synthetic: no real CT hosts.
FIXTURE_DIR = "spec/fixtures/"
FIXTURE_FORBIDDEN = re.compile(r"ceskatelevize\.cz|o2tv\.cz|o2-tv\.cz")


def tracked_files(staged: bool) -> list[str]:
    if staged:
        cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    else:
        cmd = ["git", "ls-files"]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line]


def read_text(path: str, staged: bool) -> str | None:
    try:
        if staged:
            data = subprocess.run(["git", "show", f":{path}"], check=True, capture_output=True).stdout
        else:
            data = pathlib.Path(path).read_bytes()
    except (OSError, subprocess.CalledProcessError):
        return None
    if b"\0" in data[:4096]:
        return None  # binary
    return data.decode("utf-8", "replace")


def private_patterns() -> list[re.Pattern]:
    path = os.environ.get("CZTVHUB_PRIVATE_PATTERNS")
    if not path or not os.path.exists(path):
        return []
    lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    return [re.compile(line.strip(), re.IGNORECASE) for line in lines if line.strip() and not line.startswith("#")]


def check(files: list[str], staged: bool) -> list[str]:
    problems = []
    extra = private_patterns()
    for path in files:
        if any(p.search(path) for p in FORBIDDEN_PATHS):
            problems.append(f"{path}: file must not be published")
            continue
        text = read_text(path, staged)
        if text is None:
            continue
        for pattern, label in FORBIDDEN_CONTENT:
            if pattern.search(text):
                problems.append(f"{path}: contains a {label}")
        if path.startswith(FIXTURE_DIR) and FIXTURE_FORBIDDEN.search(text):
            problems.append(f"{path}: fixtures must be synthetic (real CT host found)")
        for pattern in extra:
            if pattern.search(text) or pattern.search(path):
                problems.append(f"{path}: matches a private pattern")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--staged", action="store_true", help="check only files staged for commit")
    args = parser.parse_args(argv)
    problems = check(tracked_files(args.staged), args.staged)
    for problem in problems:
        print(f"check_public: {problem}", file=sys.stderr)
    if problems:
        print("check_public: refusing; see CONTRIBUTING.md", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
