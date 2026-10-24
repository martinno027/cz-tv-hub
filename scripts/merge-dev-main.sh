#!/usr/bin/env bash
# Release what is on origin/dev to main (what the Raspberry `git pull`s and the apps are built from).
#
#   ./scripts/merge-dev-main.sh            checks, summary, then asks you to type `main`
#   ./scripts/merge-dev-main.sh --dry-run  checks and summary only, stops before the confirmation
#   ./scripts/merge-dev-main.sh --help     this text
#
# What it does: fetches origin; stops if local dev has unpushed commits, if origin/main has commits that
# origin/dev lacks, or if CI is not green for the origin/dev commit; lists the commits to release and any change
# to the Pi's install or config; after you type `main` it fast-forwards main on the remote straight from
# origin/dev (git push origin refs/remotes/origin/dev:refs/heads/main) and moves a local main that is not
# checked out.
#
# What it does not do: no checkout, no merge commit, no force push; your working tree and its branch stay as they
# are. It deploys nothing: the Raspberry updates only when someone runs git pull + install.sh there, and the TV
# apps only when they are rebuilt. A push to main only starts the CI tests.
#
# Why not `git checkout main && git merge dev && git push`: a tree parked on main swallows the next commit made
# there by mistake, `git push origin dev` then answers "Everything up-to-date" with exit code 0, and the next
# release sends that commit to production unreviewed.

set -euo pipefail
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
# shellcheck source=scripts/lib/release.sh
source scripts/lib/release.sh

FROM=dev
TO=main

maybe_help "$0" "$@"
dry_run=false
for arg in "$@"; do
  case $arg in
    --dry-run) dry_run=true ;;
    *) die "Neznámý argument '$arg'. Použití: $0 [--dry-run | --help]" ;;
  esac
done

for repo in "${REPOS[@]}"; do
  say "$(repo_name "$repo"): git fetch origin"
  git -C "$repo" fetch --quiet --prune origin ||
    die "$(repo_name "$repo"): git fetch origin selhal; bez čerstvého stavu remote se nevydává. Zkontroluj síť a přístup."
done

# Read-only checks, all before anything is touched.
for repo in "${REPOS[@]}"; do
  require_remote_branch "$repo" "$FROM"
  require_remote_branch "$repo" "$TO"
  require_pushed "$repo" "$FROM"
  require_not_diverged "$repo" "$FROM" "$TO"
done

say "Co se vydává z origin/$FROM do $TO:"
pending=()
for repo in "${REPOS[@]}"; do
  log=$(commits_between "$repo" "$TO" "$FROM")
  if [[ -z $log ]]; then
    printf '  %s: beze změn\n' "$(repo_name "$repo")"
  else
    printf '  %s: %s commitů\n' "$(repo_name "$repo")" "$(grep -c '' <<<"$log")"
    indent '    ' <<<"$log"
    pending+=("$repo")
  fi
done

if [[ ${#pending[@]} -eq 0 ]]; then
  ok "Nic k vydání: $TO už je na origin/$FROM."
  exit 0
fi

for repo in "${pending[@]}"; do
  require_ci_green "$repo" "$FROM"
  warn_lasting_changes "$repo" "$TO" "$FROM"
done

say "Co se teď stane: větev $TO na GitHubu se posune na origin/$FROM (jen fast-forward). Nic se nenasadí;
    Raspberry se aktualizuje až příkazem git pull + install.sh a aplikace až novým sestavením."

if $dry_run; then
  ok "--dry-run: kontroly prošly, zastavuji před potvrzením. Nic se neposunulo."
  exit 0
fi

confirm_branch "$TO"

for repo in "${pending[@]}"; do
  fast_forward_remote "$repo" "$FROM" "$TO"
  follow_local_branch "$repo" "$TO"
done

for repo in "${REPOS[@]}"; do
  ok "$(repo_name "$repo"): origin/$TO je na $(git -C "$repo" log -1 --format='%h %s' "origin/$TO")"
done
say "Nic se nenasadilo. Na Raspberry: cd ~/cz-tv-hub && git pull && bash server/deploy/install.sh;
    aplikace na televizích přestav z $TO. CI na $TO: gh run list --branch $TO"
