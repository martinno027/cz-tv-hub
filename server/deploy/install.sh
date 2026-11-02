#!/usr/bin/env bash
# Install cz-tv-hub on a Raspberry Pi (Raspberry Pi OS / Debian). Run as a normal user with sudo (from anywhere);
# cztvhub-update.service (the update from /admin, deploy/update.sh) runs it as root.
set -euo pipefail
cd "$(dirname "$0")/.."   # server/ directory: src/ and deploy/ are relative to it
repo=$(cd .. && pwd -P)   # the clone
# Run as root (the update from /admin) in the owner's clone, git refuses it ("dubious ownership"): trust this clone
# for this script's git calls (also stage_web.py's version line).
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$repo"

sudo useradd --system --no-create-home --shell /usr/sbin/nologin cztvhub 2>/dev/null || true
sudo mkdir -p /opt/cztvhub
sudo cp -r src /opt/cztvhub/

# The household web app for /web/ ([dvb] web_dir, default /opt/cztvhub/web; served only with the gateway on).
# Staged as this user (git gives the version line), then swapped in whole; on failure the old copy stays.
web_stage=$(mktemp -d)
trap 'rm -rf "$web_stage"' EXIT
if python3 deploy/stage_web.py "$web_stage/web"; then
  sudo rm -rf /opt/cztvhub/web.new
  sudo cp -R "$web_stage/web" /opt/cztvhub/web.new
  sudo chmod -R u=rwX,go=rX /opt/cztvhub/web.new
  sudo rm -rf /opt/cztvhub/web
  sudo mv /opt/cztvhub/web.new /opt/cztvhub/web
else
  echo "web: not installed (reason above); /opt/cztvhub/web left as it was" >&2
fi

# Config: created once (DVB disabled); deploy/install-dvb.sh enables DVB.
if ! sudo test -f /etc/cztvhub/cztvhub.conf; then
  sudo install -d -m 0750 -o cztvhub -g cztvhub /etc/cztvhub
  key=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
  sed -e "s|@KEY@|$key|" -e "s|@DVB_ENABLED@|false|" -e "s|@TVH_PASSWORD@||" -e "s|@ADMIN_PASSWORD@||" deploy/cztvhub.conf.example \
    | sudo install -m 0600 -o cztvhub -g cztvhub /dev/stdin /etc/cztvhub/cztvhub.conf
fi

sudo cp deploy/cztvhub.service /etc/systemd/system/cztvhub.service
# HTTPS on 8443 for the installed web app (T47): a root job puts the Pi's Tailscale certificate into
# /var/lib/cztvhub/tls/ daily; the gateway serves HTTPS once it is there. Harmless without Tailscale or with HTTPS
# certificates off in the tailnet: the job fails with the reason, the plain port works as before.
sudo install -m 0755 deploy/cert.sh /opt/cztvhub/cert.sh
sudo install -m 0644 deploy/cztvhub-cert.service /etc/systemd/system/cztvhub-cert.service
sudo install -m 0644 deploy/cztvhub-cert.timer /etc/systemd/system/cztvhub-cert.timer

# The update from /admin (spec/dvb-gateway.md, T33): a path unit for the gateway's request file and a root oneshot
# that runs this clone's deploy/update.sh as its owner. Only for a git clone with a plain path (it goes into the
# unit) and a plain owner name; otherwise /admin says it is not installed.
owner=$(stat -c %U "$repo" 2>/dev/null || true)
if [[ ! -d $repo/.git ]]; then
  echo "update from /admin: not installed ($repo is not a git clone)" >&2
elif [[ ! $repo =~ ^/[A-Za-z0-9._/-]+$ || ! $owner =~ ^[a-z_][a-z0-9_-]*$ ]]; then
  echo "update from /admin: not installed (path '$repo' or owner '$owner' has characters a unit cannot take)" >&2
else
  sed -e "s|@REPO@|$repo|g" -e "s|@OWNER@|$owner|g" deploy/cztvhub-update.service.in \
    | sudo install -m 0644 /dev/stdin /etc/systemd/system/cztvhub-update.service
  sudo install -m 0644 deploy/cztvhub-update.path /etc/systemd/system/cztvhub-update.path
  update_units=1
fi
sudo systemctl daemon-reload
sudo systemctl enable cztvhub
sudo systemctl enable --now cztvhub-cert.timer
if [[ -n ${update_units:-} ]]; then
  sudo systemctl enable --now cztvhub-update.path
  echo "update from /admin: installed (clone $repo, owner $owner)"
fi
# shellcheck source=server/deploy/health.sh
source deploy/health.sh
restarted=$(date '+%Y-%m-%d %H:%M:%S')
sudo systemctl restart cztvhub
wait_for_health "$(service_port /etc/cztvhub/cztvhub.conf)" 30 "$restarted"
# The certificate now (the service made /var/lib/cztvhub); the gateway picks it up within a minute. Its reason is
# printed when it cannot: not a failed install.
if sudo timeout 300 bash /opt/cztvhub/cert.sh; then
  echo "https: certificate in place; the web app also answers on https://<MagicDNS name>:8443/web/"
else
  echo "https: off (reason above); the plain port is not affected" >&2
fi
# Written only once the service answers: the commit of the last install that worked, for /admin's version line
# and the update's "nothing new" check (a failed install is tried again by the next update).
commit=$(git -C "$repo" rev-parse HEAD 2>/dev/null || true)
[[ $commit =~ ^[0-9a-f]{40}$ ]] || commit=""
python3 -c 'import json, sys, time
print(json.dumps({"commit": sys.argv[1] or None, "installed": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
' "$commit" | sudo install -m 0644 /dev/stdin /opt/cztvhub/install.json
