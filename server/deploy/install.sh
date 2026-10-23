#!/usr/bin/env bash
# Install cz-tv-hub on a Raspberry Pi (Raspberry Pi OS / Debian). Run as a normal user with sudo (from anywhere).
set -euo pipefail
cd "$(dirname "$0")/.."   # server/ directory: src/ and deploy/ are relative to it

sudo useradd --system --no-create-home --shell /usr/sbin/nologin cztvhub 2>/dev/null || true
sudo mkdir -p /opt/cztvhub
sudo cp -r src /opt/cztvhub/

# Config: created once (DVB disabled); deploy/install-dvb.sh enables DVB.
if ! sudo test -f /etc/cztvhub/cztvhub.conf; then
  sudo install -d -m 0750 -o cztvhub -g cztvhub /etc/cztvhub
  key=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
  sed -e "s|@KEY@|$key|" -e "s|@DVB_ENABLED@|false|" -e "s|@TVH_PASSWORD@||" -e "s|@ADMIN_PASSWORD@||" deploy/cztvhub.conf.example \
    | sudo install -m 0600 -o cztvhub -g cztvhub /dev/stdin /etc/cztvhub/cztvhub.conf
fi

sudo cp deploy/cztvhub.service /etc/systemd/system/cztvhub.service
sudo systemctl daemon-reload
sudo systemctl enable cztvhub
sudo systemctl restart cztvhub
sleep 1
curl -fsS http://127.0.0.1:8098/health && echo "cz-tv-hub is running on port 8098"
