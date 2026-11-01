#!/usr/bin/env bash
# Tests for server/deploy/update.sh (the update from /admin, T33) with throwaway git repos, a fake install.sh and a
# fake runuser (no root, no systemd, no network, never the real Pi). Real timeout/flock when the machine has them
# (Linux CI), small perl stand-ins otherwise (macOS).
#
#   bash server/deploy/update.test.sh
# Checks are eval'd strings on purpose (expanded when checked), hence:
# shellcheck disable=SC2016,SC2034
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/state"

# runuser -u USER -- CMD...: notes the user, runs CMD as us
cat >"$tmp/bin/runuser" <<'EOF'
#!/usr/bin/env bash
[[ $1 == -u ]] || { echo "fake runuser: expected -u" >&2; exit 99; }
{ printf '%s' "$2"; printf ' %q' "${@:4}"; echo; } >>"$FAKE_DIR/runuser.log"  # one line per call
shift 2
[[ ${1:-} == -- ]] && shift
exec "$@"
EOF
if ! command -v timeout >/dev/null; then
  cat >"$tmp/bin/timeout" <<'EOF'
#!/usr/bin/perl
# timeout [--kill-after=N] SECONDS CMD...: GNU semantics that update.sh uses (exit 124 on the limit)
shift if $ARGV[0] =~ /^--kill-after/;
my $secs = shift;
my $pid = fork();
if (!$pid) { setpgrp(0, 0); exec @ARGV or do { print STDERR "timeout: failed to run command '$ARGV[0]'\n"; exit 127; }; }
$SIG{ALRM} = sub { kill 'TERM', -$pid; waitpid($pid, 0); exit 124; };
alarm $secs;
waitpid($pid, 0);
exit(($? & 127) ? 128 + ($? & 127) : $? >> 8);
EOF
fi
if ! command -v flock >/dev/null; then
  cat >"$tmp/bin/flock" <<'EOF'
#!/usr/bin/perl
# flock -n FD: the lock belongs to the shell's open file description and outlives this process
use Fcntl qw(:flock);
open(my $fh, ">&=", $ARGV[-1]) or exit 1;
flock($fh, LOCK_EX | LOCK_NB) or exit 1;
exit 0;
EOF
fi
chmod +x "$tmp/bin/"*

export PATH="$tmp/bin:$PATH" FAKE_DIR="$tmp"
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.invalid GIT_COMMITTER_NAME=test
export GIT_COMMITTER_EMAIL=test@example.invalid
export CZTVHUB_REPO="$tmp/clone" CZTVHUB_OWNER=owner CZTVHUB_STATE_DIR="$tmp/state" CZTVHUB_SERVICE_USER=gateway
export CZTVHUB_LOCK="$tmp/update.lock" CZTVHUB_INSTALL_INFO="$tmp/install.json" CZTVHUB_TIMEOUT=60

# origin (bare) with main; the clone; a second clone that pushes new commits
git init -q --bare -b main "$tmp/origin.git"
git clone -q "$tmp/origin.git" "$tmp/work" 2>/dev/null
mkdir -p "$tmp/work/server/deploy"
# The fake install.sh notes each run and writes install.json like the real one (only when it succeeds)
cat >"$tmp/work/server/deploy/install.sh" <<'EOF'
#!/usr/bin/env bash
echo "run" >>"$FAKE_DIR/install.runs"
if [[ -n ${FAKE_INSTALL_SLEEP:-} ]]; then sleep "$FAKE_INSTALL_SLEEP"; fi
if [[ -n ${FAKE_INSTALL_FAIL:-} ]]; then
  for i in $(seq 1 300); do echo "noise line $i of the failing install"; done
  echo "ERROR: the last line"
  exit 3
fi
printf '{"commit": "%s"}\n' "$(git -C "$(dirname "$0")/../.." rev-parse HEAD)" >"$CZTVHUB_INSTALL_INFO"
EOF
echo "one" >"$tmp/work/file.txt"
git -C "$tmp/work" add -A && git -C "$tmp/work" commit -qm one && git -C "$tmp/work" push -q origin main 2>/dev/null
git clone -q "$tmp/origin.git" "$tmp/clone" 2>/dev/null
# a plain "git pull" here would merge a diverged main (the default on many installs): only --ff-only refuses it
git -C "$tmp/clone" config pull.rebase false

new_commit() {  # a new commit on origin/main
  echo "$1" >>"$tmp/work/file.txt"
  git -C "$tmp/work" commit -qam "$1" && git -C "$tmp/work" push -q origin main 2>/dev/null
}
head_of() { git -C "$1" rev-parse HEAD; }
field() { python3 -c 'import json, sys; v = json.load(open(sys.argv[1])).get(sys.argv[2]); print("" if v is None else v)' \
  "$tmp/state/update-status.json" "$2"; }
st() { field x "$1"; }
installs() { if [[ -f $tmp/install.runs ]]; then wc -l <"$tmp/install.runs" | tr -d ' '; else echo 0; fi; }
mode_of() { if stat -c %a "$1" >/dev/null 2>&1; then stat -c %a "$1"; else stat -f %Lp "$1"; fi; }

passed=0
failed=0
check() {
  local name=$1
  shift
  if "$@"; then
    passed=$((passed + 1)); printf 'ok   %s\n' "$name"
  else
    failed=$((failed + 1)); printf 'FAIL %s\n---- out:\n%s\n---- status:\n%s\n' "$name" "${out:-}" \
      "$(cat "$tmp/state/update-status.json" 2>/dev/null)"
  fi
}
run() {  # run update.sh with a request file; sets out, code, secs
  echo x >"$tmp/state/update-request"
  rm -f "$tmp/runuser.log"
  local t0=$SECONDS
  set +e
  out=$(bash "$here/update.sh" 2>&1)
  code=$?
  set -e
  secs=$((SECONDS - t0))
}

# 1. A new release on origin: pulled as the owner, installed, status ok with both commits
old=$(head_of "$tmp/clone")
new_commit two
run
check "new release: exit 0" eval '[[ $code -eq 0 ]]'
check "new release: state ok" eval '[[ $(st state) == ok ]]'
check "new release: before/after" eval '[[ $(st before) == "$old" && $(st after) == $(head_of "$tmp/origin.git") ]]'
check "new release: clone fast-forwarded" eval '[[ $(head_of "$tmp/clone") == $(head_of "$tmp/origin.git") ]]'
check "new release: install.sh ran once" eval '[[ $(installs) -eq 1 ]]'
check "new release: started and finished set" eval '[[ -n $(st started) && -n $(st finished) && -z $(st message) ]]'
check "request deleted" eval '[[ ! -e $tmp/state/update-request ]]'
check "status file 0644" eval '[[ $(mode_of "$tmp/state/update-status.json") == 644 ]]'
check "git runs as the owner" eval 'grep -q "^owner git -C $tmp/clone pull --ff-only origin main$" "$tmp/runuser.log" && grep -q "^owner bash -c" "$tmp/runuser.log"'
check "install.sh runs as root, not as the owner" eval '[[ $(installs) -eq 1 ]] && ! grep -q "install.sh" "$tmp/runuser.log"'
check "status written as the service user" eval 'grep -q "^gateway sh" "$tmp/runuser.log"'
check "nothing else as another user" eval '! grep -qv -e "^owner " -e "^gateway sh" "$tmp/runuser.log"'

# 2. Nothing new and that commit installed: up-to-date, no install (no restart)
run
check "nothing new: state up-to-date" eval '[[ $code -eq 0 && $(st state) == up-to-date ]]'
check "nothing new: install.sh not run" eval '[[ $(installs) -eq 1 ]]'

# 3. Same commit but its install never finished (no install.json): installed again
rm -f "$tmp/install.json"
run
check "not yet installed: install.sh runs" eval '[[ $(st state) == ok && $(installs) -eq 2 ]]'

# 4. A local change to a tracked file stops it before git pull
new_commit three
here_before=$(head_of "$tmp/clone")
echo "local edit" >>"$tmp/clone/file.txt"
run
check "local change: exit 1, failed at check" eval '[[ $code -eq 1 && $(st state) == failed && $(st step) == check ]]'
check "local change: says why" eval '[[ $(st message) == *"local changes to tracked files"* && $(st message) == *file.txt* ]]'
check "local change: not pulled, not installed" eval '[[ $(head_of "$tmp/clone") == "$here_before" && $(installs) -eq 2 ]]'
git -C "$tmp/clone" checkout -q -- file.txt

# 5. Another branch checked out: stops (git pull origin main would merge main into it)
git -C "$tmp/clone" switch -q -c dev
run
check "other branch: failed at check" eval '[[ $(st state) == failed && $(st step) == check && $(st message) == *"branch dev, not main"* ]]'
git -C "$tmp/clone" switch -q main

# 6. Diverged (a local commit): --ff-only refuses, nothing installed
echo "local" >"$tmp/clone/local.txt"
git -C "$tmp/clone" add local.txt && git -C "$tmp/clone" commit -qm local
local_head=$(head_of "$tmp/clone")
run
check "diverged: failed at pull" eval '[[ $code -eq 1 && $(st state) == failed && $(st step) == pull ]]'
check "diverged: git says why" eval '[[ $(st message) == *"ast-forward"* ]]'
check "diverged: clone unchanged, not installed" eval '[[ $(head_of "$tmp/clone") == "$local_head" && $(installs) -eq 2 ]]'
check "diverged: after = before" eval '[[ $(st after) == "$local_head" && $(st before) == "$local_head" ]]'
git -C "$tmp/clone" reset -q --hard "origin/main"

# 7. install.sh fails: failed at install, the tail of its output (at most 2000 characters) in the message
new_commit four
FAKE_INSTALL_FAIL=1 run
check "install fails: failed at install" eval '[[ $code -eq 1 && $(st state) == failed && $(st step) == install ]]'
check "install fails: message is the tail" eval '[[ $(st message) == *"ERROR: the last line"* && $(st message) != *"noise line 1 of"* ]]'
check "install fails: message <= 2000 chars" eval '(( $(python3 -c "import json,sys; print(len(json.load(open(sys.argv[1]))[\"message\"]))" "$tmp/state/update-status.json") <= 2000 ))'
check "install fails: after = the pulled commit" eval '[[ $(st after) == $(head_of "$tmp/origin.git") ]]'
check "install fails: full output in the journal" eval '[[ $out == *"noise line 1 of"* ]]'

# 8. The time limit: a hanging install.sh is stopped, status says timeout
CZTVHUB_TIMEOUT=2 FAKE_INSTALL_SLEEP=30 run
check "timeout: failed with step timeout" eval '[[ $code -eq 1 && $(st state) == failed && $(st step) == timeout ]]'
check "timeout: stopped near the limit" eval '(( secs < 15 ))'
check "timeout: says so" eval '[[ $(st message) == *"time limit of 2 s"* ]]'

# 9. One at a time: while another run holds the lock, a request is dropped and the status left alone
cp "$tmp/state/update-status.json" "$tmp/status.before"
perl -MFcntl=:flock -e 'open(F, ">", $ARGV[0]) or die; flock(F, LOCK_EX) or die; open(R, ">", $ARGV[1]); close R; sleep 30' \
  "$tmp/update.lock" "$tmp/locked" &
holder=$!
for _ in $(seq 1 50); do [[ -e $tmp/locked ]] && break; sleep 0.1; done
runs_before=$(installs)
run
kill "$holder" 2>/dev/null || true
wait "$holder" 2>/dev/null || true
check "locked: exit 0, says ignored" eval '[[ $code -eq 0 && $out == *"another update is running"* ]]'
check "locked: request deleted" eval '[[ ! -e $tmp/state/update-request ]]'
check "locked: status unchanged" eval 'cmp -s "$tmp/status.before" "$tmp/state/update-status.json"'
check "locked: nothing installed" eval '[[ $(installs) -eq $runs_before ]]'

# 10. Without the unit's values it refuses before touching anything
CZTVHUB_REPO=relative/path run
check "no clone path: exit 2" eval '[[ $code -eq 2 && $out == *"absolute path"* ]]'
CZTVHUB_OWNER="" run
check "no owner: exit 2" eval '[[ $code -eq 2 ]]'

# 11. A malformed status left behind does not stop the next run
echo "{not json" >"$tmp/state/update-status.json"
run
check "after a broken status file: runs" eval '[[ $(st state) == ok ]]'

printf '\n%d prošlo, %d selhalo\n' "$passed" "$failed"
[[ $failed -eq 0 ]]
