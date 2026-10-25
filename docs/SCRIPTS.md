# Scripts and commands

Everything in this repo that can be run by hand, with what it is for and how it can go wrong. **A new script or
command belongs here in the same change.** Server commands run from `server/` (`cd server` first) unless noted.

## Everyday development

<details>
<summary><code>PYTHONPATH=src python3 -m unittest discover -s tests</code> — server and gateway tests</summary>

The whole server suite (~26 s). Needs no network; tests that run real ffmpeg skip without it (the open-GOP order
test also needs libx265).

Can go wrong: running it from the repo root instead of `server/` finds no tests (`Ran 0 tests` is not a pass).

</details>

<details>
<summary><code>PYTHONPATH=src python3 -m cztvhub dvb-fake [--port P] [--channels N] [--tuners N]</code> — fake TVHeadend</summary>

A fake TVHeadend with synthetic HEVC test streams and fake recordings, for developing the gateway and the apps
without a tuner. `--channels 60` generates a big guide; `--tuners 1` helps to test "tuner busy".
Its programmes vary like real broadcasts: no episode line, a short one or one too long for the line, a long
description, none, and every sixth programme with neither description nor summary (the case that moved the Apple TV
guide detail, T09). Its live streams carry EIT present/following (programme changes 20 s, 2:30 and −0:30 after the
EPG start, the fourth channel none) for `dvb-starts`.

Can go wrong: the default port is 9981. If 9981 is already taken (for example by an SSH tunnel to a real
TVHeadend), the fake fails to start — and a gateway configured for 9981 then talks to whatever owns that port. Pick
another port and point a copy of `deploy/cztvhub-dev.conf` at it.

</details>

<details>
<summary><code>PYTHONPATH=src python3 -m cztvhub serve [--config FILE] [--host H] [--port P]</code> — the server</summary>

Without `--config` the CT-only server (playlist + redirects). With a config with `[dvb] enabled = true` also the
DVB-T2 gateway (`/dvb/*`) and, with `[admin] password`, the admin page `/admin`. For development:
`--config deploy/cztvhub-dev.conf` against the fake TVHeadend.

Can go wrong: two servers on one port; a dev config pointing at a real TVHeadend.

</details>

<details>
<summary><code>PYTHONPATH=src python3 -m cztvhub check</code> — live check of the CT streams</summary>

Resolves every CT channel (live + timeshift) and one archive item against the real CT API. The only command besides
`serve` and the Kodi add-on that talks to CT. Use it after API changes (skill `verify-streams`), not in tests.

Can go wrong: needs internet in Czechia; CT may change its API without notice.

</details>

<details>
<summary><code>PYTHONPATH=src python3 -m cztvhub playlist --base-url URL</code> — print the M3U</summary>

Prints the playlist the server would serve, for checking the channel list.

</details>

## Raspberry Pi

<details>
<summary><code>bash server/deploy/install.sh</code> / <code>bash server/deploy/install-dvb.sh</code> — install or update</summary>

`install.sh` copies the code to `/opt/cztvhub`, installs the systemd unit and restarts the service; an existing
`/etc/cztvhub/cztvhub.conf` is kept. `install-dvb.sh` also installs TVHeadend (project repository), ffmpeg and
avahi and creates the config with DVB enabled. Update: `cd ~/cz-tv-hub && git pull && bash server/deploy/install.sh`.

After the restart `install.sh` waits up to 30 s for `/health` (`server/deploy/health.sh`; the service first loads
the index of the continuous recording, which grows with every recorded day) and prints "running on port … (ready
after N s)"; if it never answers, it prints the service state and its log since the restart and exits non-zero.
Tests: `bash server/deploy/health.test.sh` (fake curl/systemctl, no Pi).

Can go wrong: `install-dvb.sh` asks for a TVHeadend admin name and password (interactive); TVHeadend must listen on
127.0.0.1 only — the script checks it and warns. Before T07 the check waited only 1 s and reported `curl: (7) Failed
to connect` for a service that was fine a moment later.

</details>

<details>
<summary><code>python3 -m cztvhub dvb-diag --config /etc/cztvhub/cztvhub.conf [--channel NAME] [--seconds S] [--frames]</code> — why Anténa stalls or glitches</summary>

Run on the Pi (as the `cztvhub` user, `PYTHONPATH=/opt/cztvhub/src`) while a TV plays the channel. Follows the same
live session and reports tuner signal and error counters, segment lengths and gaps, simulated player stalls and,
with `--frames`, the picture (segment start pictures, open GOP, interlacing, decoder errors; ~2 min kept in memory).

Can go wrong: it is a viewer too — on a channel nobody watches it starts a session and uses a tuner.

</details>

<details>
<summary><code>python3 -m cztvhub dvb-starts --config /etc/cztvhub/cztvhub.conf [--hours 24] [--channel NAME]</code> — real programme starts from the broadcast vs the EPG (T14)</summary>

Run on the Pi (as the `cztvhub` user, `PYTHONPATH=/opt/cztvhub/src`; read-only). The recorder notes every change of
the present event in the broadcast's EIT present/following (`<state_dir>/eit-starts.jsonl`, only channels with
"Nahrávat"); this prints per channel each programme with its EPG start, the start the EIT states, when it became
present ("vidět"), the delay `vidět − EPG` and `EIT − EPG`, then the median, range and share within ±1 min per
channel and in total. A programme already running when the recorder tuned in ("při naladění") and one without an EPG
programme are listed but not counted. With the fake TVHeadend the channels switch 20 s, 2:30 and −0:30 after their
EPG start and the fourth channel sends no EIT.

Can go wrong:
- **TVHeadend's `pass` stream profile decides what EIT reaches the gateway** (Configuration → Stream → Stream
  Profiles → pass: the "Rewrite …" options, including "Rewrite EIT"). If EIT is left out, the report shows "žádné
  EIT p/f do 5 min: v proudu není EIT …" for the channel (and the log `dvb: eit: no present/following on <channel
  id> (none)`); EIT only of other services (service id rewritten) shows as `otherServices` with the service ids
  seen; several services without a rewritten PAT as `unknownService`. Not checked on a real TVHeadend yet.
- `EIT − EPG` always `+0:00` means the broadcaster states the scheduled start (TVHeadend's EPG comes from the same
  EIT); only the delay `vidět − EPG` then tells the real start, and only as well as the broadcaster switches the
  present event on time.
- The file does not exist before the first change (exit 1 with "brána ještě nic nezměřila"). TVHeadend unreachable: channel ids instead of names, EPG from the gateway's
  history only.
- As another user than `cztvhub` it may not read `/var/lib/cztvhub`.

</details>

<details>
<summary><code>python3 -m cztvhub dvb-setup --config /etc/cztvhub/cztvhub.conf [--dry-run]</code> — TVHeadend per-programme recording</summary>

The older per-programme DVR rules in TVHeadend. Installs that use the gateway's continuous recording (`/admin`,
"Nahrávat") must not run it (`docs/dvb-setup.md`, chapter 4).

</details>

<details>
<summary><code>journalctl -u cztvhub -f</code> — gateway log</summary>

Includes one line per live start (`dvb: live start …` with the time of each step) and admin actions.

</details>

## Apps

<details>
<summary>tvOS — <code>cd apps/tvos/CTCore && swift test</code>; <code>cd apps/tvos && xcodegen generate</code>, then Xcode / <code>xcodebuild</code></summary>

Logic tests in the CTCore package; the app is built from `project.yml` with XcodeGen.

Can go wrong: `xcodegen generate` recreates `Televize.xcodeproj` and drops the signing team chosen in Xcode — pick it
again before running on a device. `Televize.xcodeproj` is generated and not in git: after a pull that adds Swift files, Xcode
reports "Cannot find type … in scope" until `xcodegen generate` runs again. In the simulator install `Televize.app`, not the UI-test runner.

</details>

<details>
<summary>Android TV — <code>cd apps/androidtv && ./gradlew test assembleDebug</code></summary>

Needs `JAVA_HOME` (Android Studio's JBR) and `ANDROID_HOME` (the SDK) when not set globally.

</details>

<details>
<summary>Tailscale bridge — <code>python3 apps/tailnet/build.py android|tvos|all</code>; tests <code>cd apps/tailnet && go test -tags ts_omit_logtail ./...</code></summary>

Builds the Go library that puts Apple TV and Android TV into the tailnet without the Tailscale app (`spec/apps.md`
"Tailscale v aplikaci"): `dist/android/tailnet.aar` (gomobile, pinned in `go.mod`, installed into `apps/tailnet/.bin`)
and `dist/tvos/<sdk>/libtailnet.a` + `dist/tvos/include` (C library built with `GOOS=ios` against the tvOS SDKs). The
Gradle build and the Xcode build phase call it; it does nothing when the outputs are newer than every source.
Needs Go (it fetches the version in `go.mod` by itself); android also the NDK (`ANDROID_NDK_HOME` or the newest in the
SDK) and a JDK (`JAVA_HOME`, else Android Studio's JBR); tvos Xcode with the tvOS platform.

Can go wrong: Go has no official tvOS target — a Go or Xcode update can break the tvOS link (the error then names
the platform of `go.o`); the first build downloads Go modules and takes a few minutes; a failed build deletes its
half-written output, so a rerun builds again instead of using it.

</details>

<details>
<summary>Samsung — <code>cd apps/tizen && node --test test/*.test.js</code>; <code>python3 apps/tizen/build.py [--gateway ADDR --key-file FILE] [--install TV_IP]</code></summary>

Tests include the Chromium 56 syntax guard. UI check in a browser: serve the repo root and open
`apps/tizen/app/index.html?demo` (`&channels=60` for a big guide; `&psp=13` = invented Chamber streams 1 and 3
on air, `&psp=0` none; the demo never contacts the Chamber's CDN; `&timeline` opens the Anténa timeline player bar
over `spec/fixtures/dvb/dvb_timeline.json`, `&timeline=growing` as a growing window; the demo player is a
clock-driven fake). `build.py` packages `dist/Televize.wgt` with the
Tizen Studio CLI; `--gateway`/`--key-file` preset the gateway in that build only.

Can go wrong: the preset key ends up in the built package — never share or commit `dist/`.

</details>

<details>
<summary>Kodi add-on — <code>python3 kodi-addon/build.py [--serve --port P]</code></summary>

Builds the add-on ZIP into `dist/`; `--serve` offers it on the LAN for installing on Apple TV / Android TV Kodi.

</details>

## Shared fixtures

<details>
<summary><code>PYTHONPATH=src:tests python3 -c 'import archive_world as w; w.write_shared_fixtures()'</code> — regenerate the archive samples</summary>

Run from `server/`. Rewrites `spec/fixtures/dvb/dvb_archive_*.json` from the gateway's real code path (a temporary
continuous recording, ČT and TMDb faked with invented titles and an `.invalid` image host). The apps' parser tests
read these files; a server test fails when the gateway's output and the files differ.

Can go wrong: regenerating after a gateway change silently changes what the apps' tests expect — run all three apps'
tests afterwards.

</details>

## Repository

<details>
<summary><code>./scripts/merge-dev-main.sh [--dry-run | --help]</code> — release <code>dev</code> to <code>main</code></summary>

Moves `main` (what the Raspberry `git pull`s and the apps are built from) to `origin/dev`. Fetches origin, then
stops before touching anything when local `dev` has unpushed commits, `origin/main` has commits `origin/dev` lacks,
or CI is not green for the `origin/dev` commit. Prints the commits to release and names changes to `server/deploy/`
and `config.py`, then asks to type `main` (anything else cancels). The move is a fast-forward push on the server
(`git push origin refs/remotes/origin/dev:refs/heads/main`): no checkout, no merge commit, the working tree is left
alone; a local `main` follows only when it is not checked out. `--dry-run` stops before the confirmation. Only on
the owner's go-ahead with the double approval naming `main` (`/release`). Tests:
`./scripts/merge-dev-main.test.sh` (throwaway repos and a fake `gh`, never the real remote).

What it does not do: deploy. The Pi updates only with `cd ~/cz-tv-hub && git pull && bash server/deploy/install.sh`,
the TV apps only when rebuilt from `main`.

Can go wrong: it moves production — what the Pi gets on its next `git pull`. A change to `server/deploy/` or the
config format is not undone by going back to an older release (what `install.sh` set up on the Pi and the edited
`/etc/cztvhub/cztvhub.conf` stay). Releasing by hand (`git checkout main && git merge dev && git push`) instead of
the script leaves a tree parked on `main`, where the next stray commit is released unreviewed.

</details>

<details>
<summary><code>python3 tools/check_public.py [--staged]</code> — public-repo guard</summary>

Fails when a tracked (or staged) file is forbidden or contains forbidden content (generic checks such as private
keys, plus one regex per line from the file named by the `CZTVHUB_PRIVATE_PATTERNS` environment variable — kept
outside this repo). Runs in CI and in the pre-commit hook.

</details>
