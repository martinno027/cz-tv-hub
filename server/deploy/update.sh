#!/usr/bin/env bash
# Update the Pi from /admin (spec/dvb-gateway.md "Updating the Pi from /admin", T33).
#
# Run only by cztvhub-update.service (root), which cztvhub-update.path starts when the gateway writes
# <state dir>/update-request. Does exactly this, and nothing a browser could choose:
#   1. check: the clone is on branch main without changes to tracked files
#   2. pull:  git pull --ff-only origin main, as the clone's owner
#   3. install: bash server/deploy/install.sh, as root (restarts cztvhub) — skipped when nothing new
# and writes <state dir>/update-status.json (as the service user, 0644) at the start and the end:
#   {"state": "running"|"ok"|"failed"|"up-to-date", "started", "finished", "before", "after", "step", "message"}
# message = the last 2000 characters of the failing step's output. All steps together stop after 10 minutes.
# One at a time (flock); the request file is deleted at the start and at the end (a request while one runs is
# ignored). The status is written by this script, not the gateway, so it survives install.sh's restart.
#
# Set by the unit: CZTVHUB_REPO (the clone's absolute path), CZTVHUB_OWNER (its owner). The other CZTVHUB_* below
# exist for server/deploy/update.test.sh; the unit does not set them.
# The whole script is read before anything runs (main at the end): step 2 replaces this very file.
set -uo pipefail

repo=${CZTVHUB_REPO:-}
owner=${CZTVHUB_OWNER:-}
state_dir=${CZTVHUB_STATE_DIR:-/var/lib/cztvhub}
service_user=${CZTVHUB_SERVICE_USER:-cztvhub}
lock=${CZTVHUB_LOCK:-/run/cztvhub-update.lock}
limit=${CZTVHUB_TIMEOUT:-600}
install_info=${CZTVHUB_INSTALL_INFO:-/opt/cztvhub/install.json}

request="$state_dir/update-request"
status="$state_dir/update-status.json"
log=""
started=""
before=""
after=""
deadline=0

as_owner() { runuser -u "$owner" -- "$@"; }

# write_status STATE [STEP [OUTPUT_FILE]]: JSON built by python3 (escaping, UTF-8 errors replaced, tail), written
# by the service user so the gateway owns and reads it; never by root into the service user's directory.
write_status() {
  local state=$1 step=${2:-} output=${3:-}
  # shellcheck disable=SC2016  # $1 is expanded by the inner sh
  python3 -c '
import json, sys, time
state, step, output, started, before, after = sys.argv[1:7]
message = None
if output:
    with open(output, "rb") as fh:
        message = fh.read().decode("utf-8", "replace")[-2000:]
now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
print(json.dumps({"state": state, "started": started or now, "finished": None if state == "running" else now,
                  "before": before or None, "after": after or None, "step": step or None, "message": message}))
' "$state" "$step" "$output" "$started" "$before" "$after" \
    | runuser -u "$service_user" -- sh -c 'umask 022; cat >"$1.tmp" && mv -f "$1.tmp" "$1"' sh "$status" \
    || echo "update: could not write $status" >&2
}

# fail STEP OUTPUT_FILE: status "failed" with the step's output tail, exit 1
fail() {
  local step=$1 output=$2
  echo "update: failed at $step" >&2
  [[ -n $after ]] || after=$(as_owner git -C "$repo" rev-parse HEAD 2>/dev/null || true)
  write_status failed "$step" "$output"
  exit 1
}

# step NAME CMD...: runs the program CMD (not a shell function: timeout execs it) within what is left of the 10 minutes; its output goes to the journal and $log
step() {
  local name=$1 rc left
  shift
  left=$((deadline - $(date +%s)))
  if (( left <= 0 )); then
    echo "Time limit of $limit s reached before step $name." >"$log"
    fail timeout "$log"
  fi
  timeout --kill-after=10 "$left" "$@" >"$log" 2>&1
  rc=$?
  cat "$log"
  if (( rc == 124 || rc == 137 )); then
    printf '\nStep %s stopped: time limit of %s s reached.\n' "$name" "$limit" >>"$log"
    fail timeout "$log"
  fi
  (( rc == 0 )) || fail "$name" "$log"
}

# Step 1, run as the owner (git reads the clone's own config, so never as root): branch main, no tracked changes.
# shellcheck disable=SC2016  # expanded by the inner bash
CHECK='repo=$1
branch=$(git -C "$repo" symbolic-ref --short -q HEAD) || branch="(detached HEAD)"
if [[ $branch != main ]]; then
  echo "The clone $repo is on branch $branch, not main; update by hand (git switch main)."
  exit 1
fi
changes=$(git -C "$repo" status --porcelain --untracked-files=no) || exit 1
if [[ -n $changes ]]; then
  printf "The clone %s has local changes to tracked files; commit, stash or undo them:\n%s\n" "$repo" "$changes"
  exit 1
fi'

installed_commit() {
  python3 -c '
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        print(json.load(fh).get("commit") or "")
except (OSError, ValueError, AttributeError):
    print("")
' "$install_info"
}

main() {
  rm -f "$request"
  if [[ $repo != /* || ! -d $repo/.git || ! -f $repo/server/deploy/install.sh || -z $owner ]]; then
    echo "update: CZTVHUB_REPO ($repo) must be the clone's absolute path and CZTVHUB_OWNER ($owner) its owner;" \
      "run bash server/deploy/install.sh again" >&2
    exit 2
  fi
  exec 9>"$lock" || exit 2
  if ! flock -n 9; then
    echo "update: another update is running; this request is ignored"
    exit 0
  fi
  log=$(mktemp) || exit 2
  trap 'rm -f "$log" "$request"' EXIT
  trap 'printf "\nStopped by a signal (systemd TimeoutStartSec or stop).\n" >>"$log"; fail timeout "$log"' TERM
  deadline=$(( $(date +%s) + limit ))
  started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  before=$(as_owner git -C "$repo" rev-parse HEAD 2>/dev/null || true)
  write_status running
  echo "update: $repo at ${before:-?}, as $owner"

  step check runuser -u "$owner" -- bash -c "$CHECK" check "$repo"
  step pull runuser -u "$owner" -- git -C "$repo" pull --ff-only origin main
  after=$(as_owner git -C "$repo" rev-parse HEAD 2>/dev/null || true)
  if [[ -n $after && $after == "$before" && $after == "$(installed_commit)" ]]; then
    echo "update: $after is already installed; nothing to do"
    write_status up-to-date
    exit 0
  fi
  step install bash "$repo/server/deploy/install.sh"
  write_status ok
  echo "update: ${before:-?} -> $after installed"
}

main "$@"
exit
