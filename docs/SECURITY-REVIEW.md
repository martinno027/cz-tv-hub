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
- Profiles (`/dvb/profile*`) need the app key as a header (not the cookie) plus a valid session token; deleting a
  profile or setting a new password on `/admin` ends all its sessions; unknown name and wrong password answer the same
  text after the same work; 5 failures per name lock it for a minute. *Wrong:* a profile call that works with the
  cookie alone, a session that survives "Nové heslo", a response that tells which names exist.
- The household default channel list (`GET /dvb/defaults`) needs the app key; it is changed only on `/admin` (login,
  admin header, same origin) and never linked to a profile. *Wrong:* an app route that writes the default, or a
  profile change that silently changes it.
- A feature with a missing key or password is off. *Wrong:* an empty `[admin] password` meaning "no login".

### Network

- The server is meant for the home LAN and the household's own tailnet only; TVHeadend listens on 127.0.0.1.
  *Wrong:* docs or scripts that forward a port, enable Tailscale Funnel or share the node; TVHeadend on `*:9981`.
- mDNS on the LAN interface only. *Wrong:* the service advertised on `tailscale0`.
- Tailscale inside the apps (`apps/tailnet`): the bridge listens on `127.0.0.1` only and forwards only to the
  gateway `host:port` from the app's settings; Tailscale's log upload is compiled out (`ts_omit_logtail`) and disabled.
  *Wrong:* the bridge on `0.0.0.0`, a target taken from the request, or logs leaving the TV.

### What leaves the Pi

- No download or export of video: only HLS for playback, sessions deleted when viewing stops; recordings only through
  the views the spec names. *Wrong:* a route that returns a whole recording or file listing.
- Derived data stays as approved: thumbnails one still ≤ 480 px, memory only; ad breaks only timestamps and masks.
  *Wrong:* a cache on disk, a series of frames, a client-chosen seek for thumbnails.
- CT internet streams are only redirected. *Wrong:* the server fetching or caching CT video.

### Secrets

- Keys and passwords only in `/etc/cztvhub/cztvhub.conf` (0600, user `cztvhub`) and the platform keychains / encrypted
  prefs on the TVs. *Wrong:* a key in a log line, a URL, a playlist, a committed file or a screenshot in the repo.
- The apps' Tailscale node state (its private keys) only in the Keychain / encrypted prefs, passed to the bridge in
  memory; a Tailscale auth key typed on the TV is used once and never stored. *Wrong:* `tailscaled.state` or the key
  in a plain file, preferences or a log line.
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
- Irreversible admin actions are logged (who/what/result). *Wrong:* recordings deleted without a log line.

## Runs

| Date | Task | Scope | Findings |
| ---- | ---- | ----- | -------- |
| 2026-10-24 | T05 | Channel timeline (`/dvb/timeline`, views, apps' timeline players) | Key required (401), worker key 403/404, media outside the window 404, no playlist over a whole recording; open timeline views capped at 4 per channel; view keys were never pruned (fixed); apps accept the JSON's playlist only as a gateway path (added to the checklist). |
