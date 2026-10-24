#!/usr/bin/env bash
# Tests for scripts/merge-dev-main.sh against throwaway repos: a bare repo in a temp directory plays "origin" and
# a fake `gh` answers the CI query, so nothing here touches GitHub or the real main.
#
#   ./scripts/merge-dev-main.test.sh
# Checks are eval'd strings on purpose (expanded when checked), hence:
# shellcheck disable=SC2016,SC2034
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/bin"
cat >"$tmp/bin/gh" <<'EOF'
#!/usr/bin/env bash
# Fake GitHub CLI: `run list` prints FAKE_GH_RUN ("<id> <status> <conclusion>", empty = no run).
case "$1 $2" in
  "run list") [[ -n ${FAKE_GH_RUN:-} ]] && printf '%s\n' "$FAKE_GH_RUN"; exit 0 ;;
  "run view") echo "fake failed step: tests"; exit 0 ;;
esac
exit 1
EOF
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH"
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.invalid
export GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.invalid

passed=0
failed=0
case_no=0

# Fresh origin + clone "work" on dev, main == dev with the scripts committed. Echoes nothing.
setup() {
  case_no=$((case_no + 1))
  root="$tmp/case$case_no"
  mkdir -p "$root"
  git init --quiet --bare "$root/cz-tv-hub.git"
  git init --quiet -b main "$root/work"
  mkdir -p "$root/work/scripts/lib"
  cp "$here/merge-dev-main.sh" "$root/work/scripts/"
  cp "$here/lib/release.sh" "$root/work/scripts/lib/"
  (
    cd "$root/work"
    git remote add origin "$root/cz-tv-hub.git"
    git add -A && git commit --quiet -m "initial"
    git push --quiet origin main
    git switch --quiet -c dev
    git push --quiet -u origin dev
  )
  export FAKE_GH_RUN="1 completed success"
}

# Commit a file on the current branch of $root/work (and push it unless "local").
commit_on_dev() {
  local file=$1 mode=${2:-push}
  (
    cd "$root/work"
    mkdir -p "$(dirname "$file")"
    echo "$RANDOM" >>"$file"
    git add "$file" && git commit --quiet -m "change $file"
    [[ $mode == local ]] || git push --quiet origin dev
  )
}

# Run the script in $root/work with $1 typed at the prompt; sets $out and $code.
run() {
  local answer=$1
  shift
  set +e
  out=$(cd "$root/work" && printf '%s\n' "$answer" | ./scripts/merge-dev-main.sh "$@" 2>&1)
  code=$?
  set -e
}

remote() { git -C "$root/cz-tv-hub.git" rev-parse "refs/heads/$1"; }

check() {
  local name=$1
  shift
  if "$@"; then
    passed=$((passed + 1))
    printf 'ok   %s\n' "$name"
  else
    failed=$((failed + 1))
    printf 'FAIL %s\n---- output (exit %s):\n%s\n----\n' "$name" "${code:-}" "${out:-}"
  fi
}

has() { grep -qF -- "$1" <<<"$out"; }

# 1 + 8b: release with `main` typed moves origin/main to origin/dev, leaves the tree and its branch alone and
# moves the idle local main.
setup
commit_on_dev a.txt
commit_on_dev b.txt
echo "work in progress" >"$root/work/wip.txt"
dev_sha=$(remote dev)
run main
check "release: origin/main = origin/dev" eval '[[ $code -eq 0 && $(remote main) == "$dev_sha" ]]'
check "release: summary lists 2 commits by repo name" eval 'has "cz-tv-hub: 2 commitů" && has "change a.txt"'
check "release: tree stays on dev with work in progress" \
  eval '[[ $(git -C "$root/work" branch --show-current) == dev && -f "$root/work/wip.txt" ]]'
check "release: idle local main follows origin/main" eval '[[ $(git -C "$root/work" rev-parse main) == "$dev_sha" ]]'
check "release: says nothing was deployed" eval 'has "Nic se nenasadilo"'

# 2: anything but `main` cancels with a non-zero code.
for answer in ano y ""; do
  setup
  commit_on_dev a.txt
  main_sha=$(remote main)
  run "$answer"
  check "answer '$answer': cancelled, origin/main unchanged" \
    eval '[[ $code -ne 0 && $(remote main) == "$main_sha" ]] && has "Vydání zrušeno"'
done

# 3: an unpushed commit on local dev stops before the confirmation.
setup
commit_on_dev a.txt
commit_on_dev b.txt local
main_sha=$(remote main)
run main
check "unpushed dev: stops before the prompt" \
  eval '[[ $code -ne 0 && $(remote main) == "$main_sha" ]] && has "1 nepushnutých" && ! has "napiš název větve"'

# 4: a commit straight on origin/main (diverged) stops with an explanation.
setup
commit_on_dev a.txt
git clone --quiet -b main "$root/cz-tv-hub.git" "$root/other"
(cd "$root/other" && echo x >hotfix.txt && git add hotfix.txt && git commit --quiet -m "hotfix on main" && git push --quiet)
main_sha=$(remote main)
run main
check "diverged main: stops, nothing moved" \
  eval '[[ $code -ne 0 && $(remote main) == "$main_sha" ]] && has "rovnou do main" && has "hotfix on main"'

# 5: nothing to release exits 0 without asking.
setup
run main
check "nothing to release: exit 0, no prompt" eval '[[ $code -eq 0 ]] && has "Nic k vydání" && ! has "napiš název větve"'

# 6: --dry-run shows the summary and moves nothing.
setup
commit_on_dev a.txt
main_sha=$(remote main)
run main --dry-run
check "dry run: summary, nothing moved" \
  eval '[[ $code -eq 0 && $(remote main) == "$main_sha" ]] && has "change a.txt" && has "--dry-run" && ! has "napiš název větve"'

# 7: CI red, still running or missing for the released commit stops.
for run_state in "7 completed failure" "7 in_progress " ""; do
  setup
  commit_on_dev a.txt
  main_sha=$(remote main)
  FAKE_GH_RUN=$run_state
  run main
  check "CI '${run_state:-none}': stops, nothing moved" \
    eval '[[ $code -ne 0 && $(remote main) == "$main_sha" ]] && ! has "napiš název větve"'
done
setup
commit_on_dev a.txt
FAKE_GH_RUN="7 completed failure"
run main
check "CI failure: prints the failed log, not a link" eval 'has "fake failed step: tests"'

# 8a: a checked-out local main is not moved under the tree.
setup
commit_on_dev a.txt
git -C "$root/work" switch --quiet main
old_local=$(git -C "$root/work" rev-parse main)
run main
check "checked-out main: remote moved, local ref untouched, tree clean" \
  eval '[[ $code -eq 0 && $(remote main) == $(remote dev) && $(git -C "$root/work" rev-parse main) == "$old_local" && -z $(git -C "$root/work" status --porcelain) ]]'

# Changes to the Pi's install or config are named.
setup
commit_on_dev server/deploy/install.sh
run main --dry-run
check "deploy change: named with a warning" eval 'has "server/deploy/install.sh" && has "install.sh"'
setup
commit_on_dev docs/readme.md
run main --dry-run
check "docs change: no install warning" eval '! has "mění se instalace"'

# --help prints the header and stops.
setup
commit_on_dev a.txt
main_sha=$(remote main)
run main --help
check "--help: header, nothing moved" \
  eval '[[ $code -eq 0 && $(remote main) == "$main_sha" ]] && has "What it does not do" && ! has "git fetch"'

printf '\n%d prošlo, %d selhalo\n' "$passed" "$failed"
[[ $failed -eq 0 ]]
