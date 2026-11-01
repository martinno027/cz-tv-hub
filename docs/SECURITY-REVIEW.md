# Security review

What could be abused in what was built since the last review. These bugs have no symptoms: nothing breaks, someone
just gets in, or something leaves the household. The checklist is walked as its own task, and parts of it as part
of "done".

## When

| Trigger | Scope |
| ------- | ----- |
| Every tenth task (task number divisible by 10) | Everything since the previous run |
| Before a release to `main` that adds anything around access, the gateway, `/admin`, recordings or derived data | The changed areas |
| Any task touching those areas | Its sections, as part of "done" |

## Checklist (each item: what a wrong answer looks like)

### Access

- Every `/dvb/*` path requires the key (bearer header, or the `cztvhub_key` cookie for AVPlayer). *Wrong:* a new
  route added outside `DvbGateway.handle`, or a media path reachable without a session the key opened.
- The worker key reaches only the worker routes. *Wrong:* the worker key can list channels or play live.
- `/admin` requires its own password; state-changing requests require the `X-Cztvhub-Admin` header and a same-origin
  `Origin`. *Wrong:* a POST that works from a foreign page, or the admin password equal to the app key.
- The update from `/admin` (T33, `dvb/update.py`, `deploy/update.sh`): **who** — only a logged-in admin (password,
  `X-Cztvhub-Admin`, same origin) can request it; the app key, the cookie and the worker key cannot. **What runs** —
  the gateway only creates `<state dir>/update-request`; root runs the fixed `server/deploy/update.sh` of the clone
  baked into `cztvhub-update.service` by `install.sh`: branch/changes check and `git pull --ff-only origin main` as
  the clone's owner, then `install.sh`. **What cannot be chosen** — no command, branch, remote, argument or path
  comes from the request (the body and the request file's content are ignored); root reads nothing the gateway user
  wrote and writes nothing into its directory (the status goes through `runuser -u cztvhub`, the lock is in `/run`).
  The status file holds commit hashes and the step's output, no secrets. *Wrong:* a request field that reaches the
  script, root writing `update-status.json` (a planted symlink becomes a root write anywhere), the gateway user able
  to write the clone or the units, the update units installed with `User=cztvhub` able to run `install.sh`, a pull of
  anything but `origin main`. Accepted: whoever can push to `main` on GitHub decides what root runs on the next
  press (as with the manual `git pull && install.sh`), and the admin password now also means "deploy `main` now".
- Profiles (`/dvb/profile*`) need the app key as a header (not the cookie) plus a valid session token; deleting a
  profile or setting a new password on `/admin` ends all its sessions; unknown name and wrong password answer the same
  text after the same work; 5 failures per name lock it for a minute. *Wrong:* a profile call that works with the
  cookie alone, a session that survives "Nové heslo", a response that tells which names exist.
- The household default channel list (`GET /dvb/defaults`) needs the app key; it is changed only on `/admin` (login,
  admin header, same origin) and never linked to a profile. *Wrong:* an app route that writes the default, or a
  profile change that silently changes it.
- A feature with a missing key or password is off. *Wrong:* an empty `[admin] password` meaning "no login".
- The web app's files (`/web/`, `dvb/web.py`) are the only thing served without a key: only regular files of the
  served types under `[dvb] web_dir`; traversal (`..`, `%2e%2e`, encoded slashes), dot files, directories and
  symlinks leaving the directory are `404`; the web is off at start when `web_dir` is, contains or sits inside
  `hls_dir`, `buffer_dir`, `state_dir`, `breaks_dir` or the config's directory; `install.sh` copies only the built
  app (`deploy/stage_web.py`: no `local-config.js`, no fixtures). *Wrong:* a recording, `profiles.json` or the
  config reachable under `/web/`, a key preset in `/opt/cztvhub/web`.
- The `cztvhub_key` cookie never changes anything: every POST/PUT (and any future DELETE) under `/dvb/*` needs the
  key or the worker key as an `Authorization` header; the server grants no CORS preflight
  (`tests/test_dvb_cookie.py` sweeps every route shape). *Wrong:* a new state-changing route that accepts the
  cookie (with the web app the cookie is in every browser that opened `/web/`).
- GET routes with side effects (live sessions, thumbnails, TMDb lookups on request, `/dvb/ct/*`) can be started with
  the cookie alone; `SameSite=Strict` and `Path=/dvb` keep foreign sites out, but cookies ignore ports: every page
  on the Pi's host name is the same site and receives the cookie for its own `/dvb` paths. *Wrong:* another web UI
  with untrusted content on the Pi's name (TVHeadend on `127.0.0.1` only, as above).
- The web's `Content-Security-Policy` is on every `/web/` answer: `script-src 'self'` (no inline scripts, no
  `unsafe-*`), `frame-ancestors 'none'`, and other hosts only the measured ČT ones in `dvb/web.py`
  (`tests/test_dvb_web.py` checks the header). It is the guard for `/admin` on the same origin. *Wrong:* an inline
  script in `apps/web`, a host added without measuring it, the Chamber's CDN before the owner approves the web for it.

### Network

- The server is meant for the home LAN and the household's own tailnet only; TVHeadend listens on 127.0.0.1.
  *Wrong:* docs or scripts that forward a port, enable Tailscale Funnel or share the node; TVHeadend on `*:9981`.
- mDNS on the LAN interface only. *Wrong:* the service advertised on `tailscale0`.
- Tailscale inside the apps (`apps/tailnet`): the bridge listens on `127.0.0.1` only and forwards only to the
  gateway `host:port` from the app's settings; Tailscale's log upload is compiled out (`ts_omit_logtail`) and disabled.
  *Wrong:* the bridge on `0.0.0.0`, a target taken from the request, or logs leaving the TV.
- ČT stream answers (`/dvb/ct/live`, `/dvb/ct/vod`) go only to peers on the home LAN (`dvb/ctweb.py home_lan`:
  RFC 1918, link-local, ULA, loopback; the tailnet `100.64.0.0/10` and `fd7a:115c:a1e0::/48` excluded; IPv4-mapped
  IPv6 judged as IPv4), checked before ČT is asked (legal limit, tested with mutations). *Wrong:* a reverse proxy in
  front of the gateway (`tailscale serve`, nginx): every tailnet request then comes from `127.0.0.1` and passes as
  home.

### What leaves the Pi

- No download or export of video: only HLS for playback, sessions deleted when viewing stops; recordings only through
  the views the spec names. *Wrong:* a route that returns a whole recording or file listing.
- Recordings are never kept longer than the retention allows: the age limit applies to every block on the disk,
  also after the recordings disk was mounted later than the service started (the index is read again, T24).
  *Wrong:* blocks the gateway does not know about (a late mount, files left behind) that no rule ever deletes.
- Derived data stays as approved: thumbnails one still ≤ 480 px, memory only; ad breaks only timestamps and masks.
  *Wrong:* a cache on disk, a series of frames, a client-chosen seek for thumbnails.
- Only the cleaned title reaches TMDb (year and kind decide the match locally); `/dvb/details` takes a programme id,
  the archive's background lookups take titles the gateway itself lists (recordings within retention on offered
  channels, ČT's own listing), never client text; the poster proxy fetches only TMDb image paths from its own lookups
  (`image.tmdb.org`, fixed sizes); the TMDb key never leaves the config. The store (`tmdb.sqlite`, 0600) is purged
  by the `/admin` period, deleted without a key, and `/admin` shows it without any export. *Wrong:* a details route
  that looks up any text a client sends, archive search text sent to TMDb, a poster route that fetches a client URL,
  a download of the store.
- Archive search text goes only to ČT's own search (`searchShows`, ≤ 100 characters, cached 10 min), with our honest
  User-Agent; ČT is asked one request at a time with cached rows. *Wrong:* a faked browser, crawling whole
  categories, search text in a log line.
- CT internet streams are only redirected. *Wrong:* the server fetching or caching CT video.
- `/dvb/ct/*` sends ČT exactly the request of `api.py` (fixed live/vod parameters, the fixed schedule query; channel
  ids and slugs from `channels.py`, idecs digits only, dates in the guide range), with our User-Agent and nothing of
  the client's request; ČT's status and body go back unchanged; stream answers at most 60 s in memory, schedules
  10 min; no URL in a log line. *Wrong:* a client query or header forwarded, a DRM variant requested, a signed URL in
  the journal or on disk.

- The sign-in log (T37, `dvb/signinlog.py`, `<state dir>/signin-log.jsonl`, 0600) holds only connections (`GET
  /dvb/status` with the app key, once per 12 h per address and platform, ≤ 20 per address per 24 h) and profile events
  (sign-in, wrong password, unknown name, too many attempts, created, signed out, sessions ended on `/admin`; failed
  sign-ins ≤ 20 per address per 24 h, then one „příliš mnoho neúspěšných přihlášení", so a flood of made-up names
  cannot push real entries out): time,
  event, profile name, peer address, network kind, the validated `X-Cztvhub-Client` value (≤ 64 characters of
  `[A-Za-z0-9 ./()+_-]`) and a coarse browser/system. Newest 1 000 entries, at most 90 days (trimmed when written,
  every 30 min while running and at every start, also without `[dvb]`). Shown only on `/admin` „Přihlášení"
  (login, read-only, „Smazat záznamy" with the admin header and same origin, logged); no `/dvb/*` route returns it;
  the worker key, failed key checks and every other route write nothing. *Wrong:* a typed (unknown) name, a password,
  a token, a request body, a full User-Agent or anything about what is watched in the file; the header read on a
  playback route; a download or export of the log; an entry per request.
- **No per-request log** (T37, O019): `server.py` writes to the journal only failed requests (`http: <status>
  <method> <path>`, path without the query string, no address) and exceptions (traceback, no address); successful
  requests write nothing (`tests/test_dvb_signinlog.py` `JournalTest`). No journal line says what was watched:
  paths under `/dvb/` and `/ct/` keep only the fixed route words (`/dvb/live/*.m3u8`), errors lose TVHeadend
  stream/recording paths and ids, sessions are "a live session" / "a recording session" (`logsafe.py`,
  `PlaybackLogLinesTest`). *Wrong:* `log_message` printing the request line again, an address or a query string
  (idec, programme ids, positions) in the journal, a channel or recording id in `dvb: live start`, `hls: … idle`,
  an error or thumbnail line, a new "session started for <address>" line.

### Secrets

- Keys and passwords only in `/etc/cztvhub/cztvhub.conf` (0600, user `cztvhub`) and the platform keychains / encrypted
  prefs on the TVs. *Wrong:* a key in a log line, a URL, a playlist, a committed file or a screenshot in the repo.
- The apps' Tailscale node state (its private keys) only in the Keychain / encrypted prefs, passed to the bridge in
  memory; a Tailscale auth key typed on the TV is used once and never stored. *Wrong:* `tailscaled.state` or the key
  in a plain file, preferences or a log line.
- TMDb keys entered on `/admin` only in `<state dir>/tmdb-keys.json` (0600) and in the request to TMDb; `/admin`
  shows the last four characters; log lines name only which key; saving needs the admin login, header and same
  origin, and `[tmdb] key` in the config makes them read-only (`409`). *Wrong:* a key in an answer, a log line, an
  error text (a v3 key rides in the URL) or a file with wider bits.
- Profile passwords only as PBKDF2-SHA256 (200 000 iterations, salt per profile) and session tokens only as SHA-256
  in `profiles.json` (0600, atomic writes); the TVs keep only the profile name and token in secure storage, never the
  password. *Wrong:* a password or token in a log line, in `profiles.json` in clear, or in the TV's plain preferences.
- The apps send the key only to the gateway: a path taken from a gateway response (the timeline's `playlist`) is
  accepted only as a gateway path (`/…`, not `//…` or a full URL). *Wrong:* a client that follows any URL with the
  key in a header or cookie.
- The public repo has nothing about the household (`tools/check_public.py`). *Wrong:* a hostname, an IP, a name in a
  commit message, branch name or doc.

### Robustness

- Input from TVHeadend, CT and the apps is parsed defensively; sizes are capped (request bodies, thumbnail reads,
  bulk admin requests, profile documents 64 KB, 20 profiles, 10 sessions each; JSON with NaN, lone surrogates or deep
  nesting is refused). *Wrong:* an unbounded read or a crash on an unexpected field.
- Irreversible admin actions are logged (who/what/result). *Wrong:* recordings deleted without a log line, an
  update requested from `/admin` without `admin: update requested` (its result is in `journalctl -u cztvhub-update`
  and `update-status.json`).

## Runs

| Date | Task | Scope | Findings |
| ---- | ---- | ----- | -------- |
| 2026-10-24 | T05 | Channel timeline (`/dvb/timeline`, views, apps' timeline players) | Key required (401), worker key 403/404, media outside the window 404, no playlist over a whole recording; open timeline views capped at 4 per channel; view keys were never pruned (fixed); apps accept the JSON's playlist only as a gateway path (added to the checklist). |
| 2026-11-01 | T33 | `/admin` (Access), the update from `/admin`, "Irreversible admin actions are logged" | Admin auth, header and same origin cover `POST /admin/api/update` (tests, mutations); requests and refusals logged. Found while building: `git pull --ff-only` alone does not stop on non-conflicting local edits or on another branch (it fast-forwards that branch to `main`): explicit check step; the status written by root into the gateway user's directory would have been a symlink-write primitive: written as `cztvhub` instead. Item added under Access. |
| 2026-11-01 | T39 | `/admin` TMDb keys (Access, Secrets, irreversible actions logged) | Saving/removing needs admin login, `X-Cztvhub-Admin` and same origin (tests: 401/403, no TMDb request, no file); `[tmdb] key` in the config makes both `409`; the file is 0600 also over a leftover tmp file; no key value in any answer or log line after a save, a refusal, TMDb down (URL with `api_key` in the error) or a removal (tests, mutations red); the key check shares the lookups' limiter. Nothing open. |
