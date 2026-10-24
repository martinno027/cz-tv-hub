# Shared functions for release scripts (sourced, not run). See scripts/merge-dev-main.sh.
# Messages are Czech (the owner reads them), comments English.
# shellcheck shell=bash

if [[ -t 1 ]]; then
  _c_info=$'\e[36m' _c_ok=$'\e[32m' _c_warn=$'\e[33m' _c_err=$'\e[31m' _c_off=$'\e[0m'
else
  _c_info='' _c_ok='' _c_warn='' _c_err='' _c_off=''
fi

say()  { printf '%s==>%s %s\n' "$_c_info" "$_c_off" "$*"; }
ok()   { printf '%s OK%s %s\n' "$_c_ok" "$_c_off" "$*"; }
warn() { printf '%s POZOR%s %s\n' "$_c_warn" "$_c_off" "$*"; }
# die goes to stderr and stops: every caller says what happened and what to do about it.
die()  { printf '%s CHYBA%s %s\n' "$_c_err" "$_c_off" "$*" >&2; exit 1; }

# Indents stdin by $1 (lists under a message).
indent() {
  local line
  while IFS= read -r line; do printf '%s%s\n' "$1" "$line"; done
}

# Repositories taking part in a release, main repo first. This project has no submodules; the private repo
# has only `main` and is not released.
# shellcheck disable=SC2034  # used by the scripts that source this file
REPOS=(.)

# A name the owner recognises ("cz-tv-hub"), never "all repos": the summary must say which repo changes.
repo_name() {
  local url
  url=$(git -C "$1" remote get-url origin)
  url=${url%/}
  url=${url%.git}
  printf '%s\n' "${url##*/}"
}

# The release is read from origin/<branch>; a commit only in the local branch would be in neither the summary
# nor the release, so stop instead of silently leaving it out.
require_pushed() {
  local repo=$1 branch=$2 count
  git -C "$repo" show-ref --verify --quiet "refs/heads/$branch" || return 0
  count=$(git -C "$repo" rev-list --count "origin/$branch..$branch")
  if [[ $count -ne 0 ]]; then
    die "$(repo_name "$repo"): lokální větev $branch má $count nepushnutých commitů. Vydává se origin/$branch,
       takže by v souhrnu ani ve vydání nebyly. Pushni je (git push origin $branch), nebo je zahoď, a pusť skript znovu."
  fi
}

# Commits on <to> that <from> lacks mean someone committed straight to the release branch. Overwriting them
# would lose work and merging them here would release something nobody reviewed on dev: a person decides.
require_not_diverged() {
  local repo=$1 from=$2 to=$3 count
  count=$(git -C "$repo" rev-list --count "origin/$from..origin/$to")
  if [[ $count -ne 0 ]]; then
    die "$(repo_name "$repo"): origin/$to má $count commitů, které origin/$from nemá (někdo commitoval rovnou do $to):
$(git -C "$repo" log --oneline "origin/$from..origin/$to")
       Vydání by je přepsalo. Dostaň je nejdřív do $from (merge do $from a push) a pusť skript znovu."
  fi
}

require_remote_branch() {
  local repo=$1 branch=$2
  git -C "$repo" show-ref --verify --quiet "refs/remotes/origin/$branch" ||
    die "$(repo_name "$repo"): na remote není větev $branch. Vydávat není kam/odkud; zkontroluj git branch -r."
}

commits_between() {
  git -C "$1" log --oneline "origin/$2..origin/$3"
}

# CI must be green for exactly the commit being released, not for "the last run on dev": a newer push may not
# have finished, and an older green run says nothing about what is released now.
require_ci_green() {
  local repo=$1 branch=$2 sha line id state conclusion
  command -v gh >/dev/null ||
    die "Chybí gh (GitHub CLI), bez něj nejde ověřit CI. Nainstaluj ho (brew install gh) a přihlas se (gh auth login)."
  sha=$(git -C "$repo" rev-parse "origin/$branch")
  if ! line=$(cd "$repo" && gh run list --branch "$branch" --commit "$sha" --workflow CI --limit 1 \
      --json databaseId,status,conclusion --jq '.[] | "\(.databaseId) \(.status) \(.conclusion)"' 2>&1); then
    die "gh nevrátil běhy CI: $line"
  fi
  [[ -n $line ]] ||
    die "$(repo_name "$repo"): pro origin/$branch (${sha:0:7}) není žádný běh CI. Bez zelené se nevydává;
       počkej, až CI naběhne (gh run list --branch $branch), a pusť skript znovu."
  read -r id state conclusion <<<"$line"
  [[ $state == completed ]] ||
    die "$(repo_name "$repo"): CI pro ${sha:0:7} ještě běží (běh $id, stav $state). Počkej (gh run watch $id) a pusť skript znovu."
  if [[ $conclusion != success ]]; then
    printf '%s\n' "--- konec logu selhání (gh run view $id --log-failed) ---" >&2
    (cd "$repo" && gh run view "$id" --log-failed 2>&1 | tail -n 30) >&2 || true
    die "$(repo_name "$repo"): CI pro ${sha:0:7} skončilo '$conclusion' (běh $id). Červené se nevydává ani se schválením; oprav to na dev."
  fi
  ok "$(repo_name "$repo"): CI pro ${sha:0:7} je zelené (běh $id)."
}

# Changes the Pi keeps even after going back to an older release: what install.sh put in place (systemd unit,
# users, TVHeadend setup) and the config file format. They are the closest thing this project has to migrations.
LASTING_PATHS=(server/deploy server/src/cztvhub/config.py)

warn_lasting_changes() {
  local repo=$1 from=$2 to=$3 files
  files=$(git -C "$repo" diff --name-only "origin/$from" "origin/$to" -- "${LASTING_PATHS[@]}")
  [[ -n $files ]] || return 0
  warn "$(repo_name "$repo"): mění se instalace nebo konfigurace Raspberry:"
  indent '       ' <<<"$files"
  warn "Po git pull na Raspberry je potřeba install.sh; změna, kterou udělá v systému nebo v /etc/cztvhub,
       se návratem ke starší verzi sama nevrátí."
}

# Only a fast-forward on the server: the remote refuses anything else, so no merge commit and no rewritten
# history can come out of it, and the working tree (with whatever is in progress there) is never touched.
fast_forward_remote() {
  local repo=$1 from=$2 to=$3 out
  if ! out=$(git -C "$repo" push origin "refs/remotes/origin/$from:refs/heads/$to" 2>&1); then
    die "$(repo_name "$repo"): push origin/$from -> $to selhal, $to se neposunul:
$out"
  fi
  git -C "$repo" fetch --quiet origin "$to"
  [[ $(git -C "$repo" rev-parse "origin/$to") == "$(git -C "$repo" rev-parse "origin/$from")" ]] ||
    die "$(repo_name "$repo"): push prošel, ale origin/$to neukazuje na origin/$from. Zkontroluj git log origin/$to."
}

# Moving a checked-out branch's ref would make the whole tree look like staged deletions, so a checked-out
# local <branch> is left alone; an idle one follows the remote if that is a fast-forward.
follow_local_branch() {
  local repo=$1 branch=$2
  git -C "$repo" show-ref --verify --quiet "refs/heads/$branch" || return 0
  if git -C "$repo" worktree list --porcelain | grep -qx "branch refs/heads/$branch"; then
    warn "Lokální $branch je checkoutnutá, neposouvám ji pod pracovním stromem (git pull v ní ji srovná)."
  elif git -C "$repo" merge-base --is-ancestor "$branch" "origin/$branch"; then
    git -C "$repo" update-ref "refs/heads/$branch" "origin/$branch"
  else
    warn "Lokální $branch má vlastní commity, nechávám ji být (porovnej s origin/$branch)."
  fi
}

# Only the typed branch name moves production: a bare "ano"/"ok" meant for something else must not.
confirm_branch() {
  local branch=$1 answer=''
  printf 'Pro vydání napiš název větve (%s), cokoli jiného vydání zruší: ' "$branch"
  read -r answer || true
  [[ $answer == "$branch" ]] || die "Vydání zrušeno (napsáno '${answer}', ne '$branch'). Nic se neposunulo."
}

# Prints the script's header comment: someone opens a production script exactly when unsure whether to run it.
maybe_help() {
  local script=$1
  shift
  local arg
  for arg in "$@"; do
    if [[ $arg == -h || $arg == --help ]]; then
      sed -n '2,/^$/{/^$/q;s/^# \{0,1\}//;p;}' "$script"
      exit 0
    fi
  done
}
