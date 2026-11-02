#!/usr/bin/env bash
# The Pi's Tailscale HTTPS certificate for the gateway's HTTPS port 8443 (spec/dvb-gateway.md "HTTPS for the
# installed web app", T47). Run as root by cztvhub-cert.service (daily timer) and once by install.sh.
#
#   sudo bash server/deploy/cert.sh
#
# Asks tailscale for the certificate of this machine's own MagicDNS name (Self.DNSName of `tailscale status --json`,
# without the trailing dot) and puts it in /var/lib/cztvhub/tls/ as cert.pem + key.pem (owner cztvhub, key 0600).
# The files are replaced only when tailscale handed out something new (renewal), so the gateway reloads only then.
# Nothing else gets the key. Fails with the reason (no tailscale, not logged in, no MagicDNS name, HTTPS certificates
# not enabled in the tailnet): the gateway's plain port is never affected, HTTPS just stays off.
# Tests: CZTVHUB_TLS_DIR, CZTVHUB_SERVICE_USER, CZTVHUB_SERVICE_GROUP (server/deploy/cert.test.sh, fake tailscale).
set -euo pipefail
dir=${CZTVHUB_TLS_DIR:-/var/lib/cztvhub/tls}
user=${CZTVHUB_SERVICE_USER:-cztvhub}
group=${CZTVHUB_SERVICE_GROUP:-$user}

fail() {
  echo "cert: $*" >&2
  exit 1
}

command -v tailscale >/dev/null || fail "tailscale is not installed (docs/dvb-setup.md, Tailscale); HTTPS stays off"
id "$user" >/dev/null 2>&1 || fail "user $user does not exist (run install.sh first)"

if ! status=$(tailscale status --json 2>&1); then
  fail "tailscale status failed: $(printf '%s' "$status" | tail -n 3)"
fi
name=$(printf '%s' "$status" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    sys.exit("tailscale status --json did not answer JSON")
if data.get("BackendState") not in (None, "Running"):
    sys.exit("tailscale is not running here (state %s): sudo tailscale up" % data.get("BackendState"))
name = ((data.get("Self") or {}).get("DNSName") or "").rstrip(".")
if not name:
    sys.exit("this machine has no MagicDNS name: turn on MagicDNS in the Tailscale admin console (DNS)")
print(name)
' 2>&1) || fail "$name"
[[ $name =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]] \
  || fail "unexpected MagicDNS name '$name'"

parent=$(dirname "$dir")
[[ -d $parent ]] || fail "$parent does not exist yet (cztvhub.service makes it at its first start)"
install -d -m 0750 "$dir"
chown "$user:$group" "$dir"
umask 077
new_cert="$dir/cert.pem.new" new_key="$dir/key.pem.new"
rm -f "$new_cert" "$new_key"
trap 'rm -f "$new_cert" "$new_key"' EXIT
if ! out=$(tailscale cert --cert-file "$new_cert" --key-file "$new_key" "$name" 2>&1); then
  fail "tailscale cert for $name failed: $(printf '%s' "$out" | tail -n 3)" \
    "(HTTPS certificates must be enabled once in the Tailscale admin console, DNS → HTTPS Certificates)"
fi
[[ -s $new_cert && -s $new_key ]] || fail "tailscale cert for $name wrote no files"

if cmp -s "$new_cert" "$dir/cert.pem" && cmp -s "$new_key" "$dir/key.pem"; then
  echo "cert: $name unchanged"
  exit 0
fi
chown "$user:$group" "$new_cert" "$new_key"
chmod 0600 "$new_key"
chmod 0644 "$new_cert"
# The key first: the gateway loads only a pair that fits, and keeps the old one until both are new.
mv -f "$new_key" "$dir/key.pem"
mv -f "$new_cert" "$dir/cert.pem"
echo "cert: $name installed in $dir (the gateway takes it from the next connection on)"
