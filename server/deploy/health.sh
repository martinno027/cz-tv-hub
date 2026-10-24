# Waiting for the service after a restart (sourced by install.sh, not run on its own).
# shellcheck shell=bash

# The port the service listens on: [server] port of the config, else the default 8098.
service_port() {
  local conf=${1:-/etc/cztvhub/cztvhub.conf} port
  port=$(sudo awk -F'=' '
    /^\[/ { section = $0 }
    section == "[server]" && $1 ~ /^[ \t]*port[ \t]*$/ { gsub(/[ \t]|;.*/, "", $2); print $2; exit }
  ' "$conf" 2>/dev/null || true)
  [[ $port =~ ^[0-9]+$ ]] && printf '%s\n' "$port" || printf '8098\n'
}

# Polls http://127.0.0.1:<port>/health for up to <seconds> (default 30). A restarted service needs a while before
# it listens: it loads the index of the continuous recording first, which grows with every recorded day, so one
# fixed second was not enough (2026-10-24: "Failed to connect" from a service that was fine a moment later).
# On failure prints why: the service state and its log since <since> (the restart), not a pointer to it.
wait_for_health() {
  local port=$1 seconds=${2:-30} since=${3:-} waited=0
  while (( waited < seconds )); do
    if curl -fsS -m 2 "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
      echo "cz-tv-hub is running on port $port (ready after ${waited} s)"
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  echo "ERROR: cz-tv-hub did not answer on http://127.0.0.1:$port/health within $seconds s." >&2
  systemctl status cztvhub --no-pager -n 0 >&2 || true
  echo "--- journalctl -u cztvhub${since:+ --since \"$since\"} (last 30 lines) ---" >&2
  if [[ -n $since ]]; then
    sudo journalctl -u cztvhub --since "$since" -n 30 --no-pager >&2 || true
  else
    sudo journalctl -u cztvhub -n 30 --no-pager >&2 || true
  fi
  return 1
}
