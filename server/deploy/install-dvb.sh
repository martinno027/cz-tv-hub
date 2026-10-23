#!/usr/bin/env bash
# DVB-T2 gateway on the Raspberry Pi: TVHeadend (localhost only) + ffmpeg + avahi (LAN only).
# Run as a normal user with sudo (from anywhere), after plugging in the tuner. See docs/dvb-setup.md.
set -euo pipefail
cd "$(dirname "$0")/.."   # server/ directory: src/ and deploy/ are relative to it

sudo apt-get update
sudo apt-get install -y ffmpeg avahi-daemon curl

# TVHeadend is not packaged by Debian / Raspberry Pi OS: use the TVHeadend project's own repository (Cloudsmith).
# Raspberry Pi OS 64-bit: bookworm builds are published as debian/bookworm, trixie builds as raspbian/trixie.
if ! apt-cache policy tvheadend | grep -q 'Candidate: [0-9]'; then
  # shellcheck source=/dev/null
  . /etc/os-release
  case "${VERSION_CODENAME:-}" in
    bookworm) tvh_distro=debian ;;
    trixie) tvh_distro=raspbian ;;
    *) echo "No TVHeadend packages known for ${PRETTY_NAME:-this system}; see docs/dvb-setup.md." >&2; exit 1 ;;
  esac
  curl -1sLf 'https://dl.cloudsmith.io/public/tvheadend/tvheadend/setup.deb.sh' \
    | sudo env distro="$tvh_distro" codename="$VERSION_CODENAME" bash
  sudo apt-get update
fi
# Interactive on purpose: the package asks for a TVHeadend administrator name and password.
sudo apt-get install -y tvheadend

# TVHeadend listens on 127.0.0.1 only (web UI via SSH tunnel, see docs/dvb-setup.md).
if ! grep -q -- '--bindaddr 127.0.0.1' /etc/default/tvheadend 2>/dev/null; then
  if grep -q '^TVH_ARGS=' /etc/default/tvheadend 2>/dev/null; then
    sudo sed -i 's|^TVH_ARGS="\(.*\)"|TVH_ARGS="\1 --bindaddr 127.0.0.1"|' /etc/default/tvheadend
  else
    echo 'TVH_ARGS="--bindaddr 127.0.0.1"' | sudo tee -a /etc/default/tvheadend >/dev/null
  fi
fi
sudo systemctl restart tvheadend

# cz-tv-hub itself (code, service user, unit); then the config with DVB enabled if it does not exist yet.
sudo useradd --system --no-create-home --shell /usr/sbin/nologin cztvhub 2>/dev/null || true
if ! sudo test -f /etc/cztvhub/cztvhub.conf; then
  sudo install -d -m 0750 -o cztvhub -g cztvhub /etc/cztvhub
  key=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
  tvh_password=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')
  sed -e "s|@KEY@|$key|" -e "s|@DVB_ENABLED@|true|" -e "s|@TVH_PASSWORD@|$tvh_password|" deploy/cztvhub.conf.example \
    | sudo install -m 0600 -o cztvhub -g cztvhub /dev/stdin /etc/cztvhub/cztvhub.conf
  echo
  echo "Created /etc/cztvhub/cztvhub.conf (DVB enabled)."
  echo "  App key:            $key"
  echo "  TVHeadend user:     cztvhub"
  echo "  TVHeadend password: $tvh_password   (create this user in the TVHeadend web UI)"
  echo
else
  echo "/etc/cztvhub/cztvhub.conf exists; set [dvb] enabled = true and the [tvheadend] password there."
fi
bash deploy/install.sh

# mDNS: advertise _cztvhub._tcp on the LAN interface only, never on Tailscale.
lan_if=$(ip route show default | awk '{print $5; exit}')
conf=/etc/avahi/avahi-daemon.conf
if [ -n "$lan_if" ] && ! grep -q '^allow-interfaces=' "$conf"; then
  if grep -q '^#allow-interfaces=' "$conf"; then
    sudo sed -i "s|^#allow-interfaces=.*|allow-interfaces=$lan_if|" "$conf"
  else
    sudo sed -i "s|^\[server\]|[server]\nallow-interfaces=$lan_if|" "$conf"
  fi
fi
if ! grep -q '^deny-interfaces=' "$conf"; then
  if grep -q '^#deny-interfaces=' "$conf"; then
    sudo sed -i "s|^#deny-interfaces=.*|deny-interfaces=tailscale0|" "$conf"
  else
    sudo sed -i "s|^\[server\]|[server]\ndeny-interfaces=tailscale0|" "$conf"
  fi
fi
sudo install -m 0644 deploy/cztvhub.avahi.service /etc/avahi/services/cztvhub.service
sudo systemctl restart avahi-daemon

echo "mDNS on: $(grep '^allow-interfaces=' "$conf" || echo 'all interfaces (check avahi-daemon.conf)')"
echo "Next: first scan in the TVHeadend web UI, then:"
echo "  sudo -u cztvhub env PYTHONPATH=/opt/cztvhub/src python3 -m cztvhub dvb-setup --config /etc/cztvhub/cztvhub.conf"
