#!/usr/bin/env bash
# Tests for server/deploy/health.sh with fake curl/sudo/systemctl/journalctl/sleep (no service, no Pi).
#
#   bash server/deploy/health.test.sh
# Checks are eval'd strings on purpose (expanded when checked), hence:
# shellcheck disable=SC2016,SC2034
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"

# curl fails until it has been called FAKE_CURL_OK_AT times (0 = never succeeds).
cat >"$tmp/bin/curl" <<'EOF'
#!/usr/bin/env bash
n=$(( $(cat "$FAKE_DIR/curl.count" 2>/dev/null || echo 0) + 1 ))
echo "$n" >"$FAKE_DIR/curl.count"
echo "$*" >>"$FAKE_DIR/curl.args"
[[ $FAKE_CURL_OK_AT -ne 0 && $n -ge $FAKE_CURL_OK_AT ]]
EOF
cat >"$tmp/bin/sudo" <<'EOF'
#!/usr/bin/env bash
exec "$@"
EOF
cat >"$tmp/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "fake status: failed (Result: exit-code)"
EOF
cat >"$tmp/bin/journalctl" <<'EOF'
#!/usr/bin/env bash
echo "fake log line; args: $*"
EOF
cat >"$tmp/bin/sleep" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$tmp/bin/"*
export PATH="$tmp/bin:$PATH" FAKE_DIR="$tmp"
# shellcheck source=server/deploy/health.sh
source "$here/health.sh"

passed=0
failed=0
check() {
  local name=$1
  shift
  if "$@"; then
    passed=$((passed + 1)); printf 'ok   %s\n' "$name"
  else
    failed=$((failed + 1)); printf 'FAIL %s\n---- out:\n%s\n---- err:\n%s\n' "$name" "${out:-}" "${err:-}"
  fi
}
run() {  # run wait_for_health "$@" with fresh counters; sets out, err, code
  rm -f "$tmp/curl.count" "$tmp/curl.args"
  set +e
  out=$(wait_for_health "$@" 2>"$tmp/err")
  code=$?
  set -e
  err=$(cat "$tmp/err")
}

# A slow start (answers on the third try) is a success, not "Failed to connect".
FAKE_CURL_OK_AT=3 run 8098 30 "2026-10-24 16:51:20"
check "slow start: success" eval '[[ $code -eq 0 ]]'
check "slow start: says when it was ready" eval '[[ $out == *"running on port 8098 (ready after 2 s)"* ]]'
check "slow start: no error output" eval '[[ -z $err ]]'
check "asks the configured port" eval 'grep -q "http://127.0.0.1:8098/health" "$tmp/curl.args"'

# Never answers: fails after the limit and prints the service state and its log since the restart.
FAKE_CURL_OK_AT=0 run 9000 5 "2026-10-24 16:51:20"
check "no answer: non-zero exit" eval '[[ $code -ne 0 ]]'
check "no answer: tried for the whole limit" eval '[[ $(cat "$tmp/curl.count") -eq 5 ]]'
check "no answer: says what failed" eval '[[ $err == *"did not answer on http://127.0.0.1:9000/health within 5 s"* ]]'
check "no answer: prints the service state" eval '[[ $err == *"fake status: failed"* ]]'
check "no answer: prints the log since the restart" eval '[[ $err == *"--since 2026-10-24 16:51:20"* && $err == *"fake log line"* ]]'

# The port comes from [server] port of the config, comments and other sections ignored.
cat >"$tmp/conf1" <<'EOF'
[dvb]
port = 1234
[server]
host = 0.0.0.0 ; comment
port = 9000          ; home LAN + Tailscale
EOF
printf '[server]\nhost = 0.0.0.0\n[dvb]\nport = 1234\n' >"$tmp/conf2"
check "port from [server]" eval '[[ $(service_port "$tmp/conf1") == 9000 ]]'
check "no port in [server]: 8098" eval '[[ $(service_port "$tmp/conf2") == 8098 ]]'
check "no config: 8098" eval '[[ $(service_port "$tmp/missing") == 8098 ]]'

printf '\n%d prošlo, %d selhalo\n' "$passed" "$failed"
[[ $failed -eq 0 ]]
