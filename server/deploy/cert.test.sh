#!/usr/bin/env bash
# Tests for server/deploy/cert.sh (the Tailscale certificate for HTTPS on 8443, T47) with a fake `tailscale` on PATH
# (no root, no tailscaled, no network, never the real Pi). The fake answers `status --json` from $FAKE_STATUS and
# `cert` by writing $FAKE_CERT / $FAKE_KEY, or fails like tailscale does when HTTPS certificates are off.
#
#   bash server/deploy/cert.test.sh
# Checks are eval'd strings on purpose (expanded when checked), hence:
# shellcheck disable=SC2016,SC2034
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/state"

cat >"$tmp/bin/tailscale" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$FAKE_DIR/tailscale.log"
case "$1" in
  status)
    [[ $2 == --json ]] || exit 98
    if [[ -n ${FAKE_STATUS_FAIL:-} ]]; then echo "failed to connect to local tailscaled; it doesn't appear to be running" >&2; exit 1; fi
    printf '%s' "$FAKE_STATUS" ;;
  cert)
    [[ $2 == --cert-file && $4 == --key-file && $# == 6 ]] || { echo "fake tailscale: bad cert args: $*" >&2; exit 97; }
    if [[ -n ${FAKE_CERT_OFF:-} ]]; then
      echo "500 Internal Server Error: your Tailscale account does not support getting TLS certs" >&2
      exit 1
    fi
    printf '%s' "$FAKE_CERT" >"$3"
    printf '%s' "$FAKE_KEY" >"$5" ;;
  *) exit 96 ;;
esac
EOF
# chown: noted (a test without root cannot hand files to another user), then done for real
real_chown=$(command -v chown)
cat >"$tmp/bin/chown" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >>"\$FAKE_DIR/chown.log"
exec "$real_chown" "\$@"
EOF
chmod +x "$tmp/bin/tailscale" "$tmp/bin/chown"

me=$(id -un)
export FAKE_DIR="$tmp" CZTVHUB_TLS_DIR="$tmp/state/tls" CZTVHUB_SERVICE_USER="$me"
CZTVHUB_SERVICE_GROUP=$(id -gn)
export CZTVHUB_SERVICE_GROUP
running='{"BackendState": "Running", "Self": {"DNSName": "cztvhub.tail1234.ts.net.", "HostName": "cztvhub"}}'
export FAKE_STATUS="$running" FAKE_CERT="cert one" FAKE_KEY="key one"
base_path="$tmp/bin:$PATH"

failures=0
check() {  # check "description" 'condition'
  if eval "$2"; then
    echo "ok   $1"
  else
    echo "FAIL $1" >&2
    failures=$((failures + 1))
  fi
}
run() {  # run cert.sh; output in $out, exit code in $code
  set +e
  out=$(PATH="$base_path" bash "$here/cert.sh" 2>&1)
  code=$?
  set -e
}
mode() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"; }
inode() { stat -c %i "$1" 2>/dev/null || stat -f %i "$1"; }
owner() { stat -c %U "$1" 2>/dev/null || stat -f %Su "$1"; }

# 1. first run: the files for the Pi's own name, without the trailing dot
run
check "first run succeeds" '[[ $code == 0 ]]'
check "asks for the name without the trailing dot" \
  'grep -qx -- "cert --cert-file $CZTVHUB_TLS_DIR/cert.pem.new --key-file $CZTVHUB_TLS_DIR/key.pem.new cztvhub.tail1234.ts.net" "$tmp/tailscale.log"'
check "cert.pem and key.pem in place" '[[ $(cat "$CZTVHUB_TLS_DIR/cert.pem") == "cert one" && $(cat "$CZTVHUB_TLS_DIR/key.pem") == "key one" ]]'
check "key is 0600" '[[ $(mode "$CZTVHUB_TLS_DIR/key.pem") == 600 ]]'
check "cert is 0644" '[[ $(mode "$CZTVHUB_TLS_DIR/cert.pem") == 644 ]]'
check "directory is 0750" '[[ $(mode "$CZTVHUB_TLS_DIR") == 750 ]]'
check "owned by the service user" '[[ $(owner "$CZTVHUB_TLS_DIR/key.pem") == "$me" && $(owner "$CZTVHUB_TLS_DIR") == "$me" ]]'
check "handed to the service user and group" \
  'grep -qx -- "$me:$CZTVHUB_SERVICE_GROUP $CZTVHUB_TLS_DIR/cert.pem.new $CZTVHUB_TLS_DIR/key.pem.new" "$tmp/chown.log" && grep -qx -- "$me:$CZTVHUB_SERVICE_GROUP $CZTVHUB_TLS_DIR" "$tmp/chown.log"'
check "no temporary files left" '[[ ! -e $CZTVHUB_TLS_DIR/cert.pem.new && ! -e $CZTVHUB_TLS_DIR/key.pem.new ]]'
check "says what it did" '[[ $out == *"cztvhub.tail1234.ts.net installed"* ]]'

# 2. the same certificate again: the files are not touched (the gateway reloads only on a change)
before=$(inode "$CZTVHUB_TLS_DIR/cert.pem")
run
check "unchanged run succeeds" '[[ $code == 0 && $out == *"unchanged"* ]]'
check "unchanged files keep their inode" '[[ $(inode "$CZTVHUB_TLS_DIR/cert.pem") == "$before" ]]'

# 3. renewal: replaced
FAKE_CERT="cert two" FAKE_KEY="key two" run
check "renewal replaces both" '[[ $code == 0 && $(cat "$CZTVHUB_TLS_DIR/cert.pem") == "cert two" && $(cat "$CZTVHUB_TLS_DIR/key.pem") == "key two" ]]'
check "renewed key still 0600" '[[ $(mode "$CZTVHUB_TLS_DIR/key.pem") == 600 ]]'

# 4. HTTPS certificates off in the tailnet: fails with tailscale's reason and the hint, old files stay
FAKE_CERT_OFF=1 run
check "certificates off: exit 1" '[[ $code == 1 ]]'
check "certificates off: tailscale's reason" '[[ $out == *"does not support getting TLS certs"* ]]'
check "certificates off: where to turn them on" '[[ $out == *"HTTPS Certificates"* ]]'
check "certificates off: the old pair stays" '[[ $(cat "$CZTVHUB_TLS_DIR/cert.pem") == "cert two" ]]'
check "certificates off: no temporary files left" '[[ ! -e $CZTVHUB_TLS_DIR/cert.pem.new && ! -e $CZTVHUB_TLS_DIR/key.pem.new ]]'

# 5. no MagicDNS name, tailscale not running, tailscaled down, junk, an odd name: the reason, no cert asked for
: >"$tmp/tailscale.log"
FAKE_STATUS='{"BackendState": "Running", "Self": {"DNSName": ""}}' run
check "no MagicDNS name: reason" '[[ $code == 1 && $out == *"no MagicDNS name"* ]]'
FAKE_STATUS='{"BackendState": "NeedsLogin", "Self": {"DNSName": "cztvhub.tail1234.ts.net."}}' run
check "logged out: reason" '[[ $code == 1 && $out == *"not running here (state NeedsLogin)"* ]]'
FAKE_STATUS_FAIL=1 run
check "tailscaled down: its text" '[[ $code == 1 && $out == *"tailscale status failed: failed to connect to local tailscaled"* ]]'
FAKE_STATUS='not json' run
check "junk status: reason" '[[ $code == 1 && $out == *"did not answer JSON"* ]]'
FAKE_STATUS='{"BackendState": "Running", "Self": {"DNSName": "x; rm -rf /."}}' run
check "odd name: refused" '[[ $code == 1 && $out == *"unexpected MagicDNS name"* ]]'
check "no certificate asked for in any of them" '! grep -q "^cert" "$tmp/tailscale.log"'

# 6. no tailscale at all
set +e
out=$(PATH="/usr/bin:/bin" bash "$here/cert.sh" 2>&1)
code=$?
set -e
check "no tailscale: reason" '[[ $code == 1 && $out == *"tailscale is not installed"* ]]'

# 7. before the service ever ran (no /var/lib/cztvhub): reason, nothing created as root
CZTVHUB_TLS_DIR="$tmp/missing/tls" run
check "no state dir: reason" '[[ $code == 1 && $out == *"does not exist yet"* && ! -e $tmp/missing ]]'

if ((failures)); then
  echo "$failures check(s) failed" >&2
  exit 1
fi
echo "all cert.sh checks passed"
