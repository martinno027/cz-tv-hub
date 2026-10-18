# Ad breaks in "Anténa" recordings ("Přeskočit reklamu")

Status: approved 2026-10-18 (legal-guard: approve with changes, applied below). **Implemented:** gateway endpoints,
worker key and the button in all three apps. **Not yet:** the worker itself (needs real recordings to tune). Applies only to the household's own DVB-T2 recordings
(`spec/dvb-gateway.md`). CT internet streams are never recorded or analysed (CLAUDE.md hard rules), so they get
no ad-break detection.

## Goal
While a viewer plays an "Anténa" recording, the app shows a **"Přeskočit reklamu"** button in the bottom right
corner during an ad break; pressing it seeks to the end of the break. Skipping is always the viewer's choice:
nothing is skipped automatically, and the recording on disk stays complete (nothing is cut or re-encoded).
The feature is off by default and switched on in the gateway's config.

## Architecture
```
TVHeadend (Pi) ─▶ recordings on the Pi's disk
                      │
cztvhub gateway (Pi) ─┤  /dvb/recording/<id>.m3u8   (existing HLS, now also readable with the worker key)
                      │  /dvb/ads/jobs              (new: recordings waiting for analysis)
                      │  /dvb/recording/<id>/breaks (new: PUT by the worker, GET by the apps)
                      ▼
ads worker (Windows PC now, a newer Pi later): reads the HLS, keeps only timestamps, uploads the breaks
                      ▼
apps (tvOS, Android TV, Samsung): fetch breaks while playing, show the button
```
- The **worker** is a separate program, `python3 -m cztvhub ads-worker`, Python standard library plus the system
  `ffmpeg`, running unchanged on Windows, macOS and Linux. It can run on the Pi itself
  (`--gateway http://127.0.0.1:8098`) once the hardware is fast enough.
- The worker reads recordings **only through the gateway**, never from a share or a copied file, and **never writes
  video, audio or frames to disk**: ffmpeg decodes into a pipe, the worker analyses the samples in memory and keeps
  only the resulting timestamps (and per-channel logo masks, see below). It talks to the gateway over the home LAN or
  the household tailnet like any app.
- The worker runs **only on a household-owned machine** at home or on the household tailnet: never on a cloud VM, a
  rented GPU, a CI runner or someone else's computer (that would be the cloud copy the hard rules forbid). It has no
  debug option that writes frames, crops or audio to disk.

Fixtures: `spec/fixtures/ads/` (`breaks_done.json` with invalid entries to drop, `breaks_pending.json`,
`breaks_off.json`, golden button cases `skip_cases.json`).

## Config (`/etc/cztvhub/cztvhub.conf`)
```ini
[ads]
enabled = false                 ; off by default
channels = Nova HD, Prima HD    ; TVHeadend channel names, or "all"; antenna-received ČT channels rarely need it
worker_key = <random 32+ chars> ; separate from [dvb] key, see "Worker key"
worker_hosts =                  ; optional comma-separated source IPs allowed to use worker_key
breaks_dir = /var/lib/cztvhub/breaks
```
- `enabled = false` (or no `[ads]` section): the new endpoints answer `404`, apps never show the button.
- Switching it off later deletes nothing automatically; stored breaks are removed with their recordings anyway.

## Worker key
The worker uses its own bearer key (`worker_key`, at least 32 characters, different from `[dvb] key`) with a
**narrow scope**: `GET /dvb/ads/jobs`, `GET` of recording HLS (`/dvb/recording/<id>.m3u8` and its `/dvb/hls/…`
media) of completed recordings on `[ads] channels`, and `PUT /dvb/recording/<id>/breaks`. Everything else (live TV,
EPG, status, other recordings) answers `403` for it. The app key cannot `PUT` breaks. A leaked worker key therefore cannot be used to watch live TV,
and can be rotated without touching the TVs.
- Accepted **only** as the `Authorization` header, never as the `cztvhub_key` cookie (that exists for Samsung AVPlay
  and the app key only).
- At most **one worker HLS session** at a time; the usual `idle_seconds` cleanup applies to it.
- Optional `worker_hosts = 192.168.1.10, 100.101.102.103` restricts the source addresses allowed to use the key.

## Gateway API (new)
- `GET /dvb/ads/jobs?analyzer=logo-v1` (worker key) →
  `{"analyzer": "logo-v1", "jobs": [{"recordingId": "<dvr uuid>", "channelId": "<uuid>", "channelName": "Nova HD",
  "start": "…Z", "end": "…Z", "playlist": "/dvb/recording/<dvr uuid>.m3u8"}]}`
  Completed recordings of `[ads] channels` without stored breaks or with breaks from another `analyzer` (the worker
  names its own version), oldest first, at most 10 per call. A recording still being recorded is not a job (v1).
- `PUT /dvb/recording/<id>/breaks` (worker key), body
  `{"analyzer": "logo-v1", "breaks": [{"start": 812.0, "end": 1105.5, "confidence": 0.9}]}` →
  `204`. `start`/`end` are **seconds from the beginning of the recording** (the position in its HLS), sorted,
  non-overlapping, each break 20 s – 20 min long, `confidence` 0–1, at most 200 breaks; invalid bodies → `400`;
  a recording that is not a completed recording of `[ads] channels` → `403`. Stored as a small JSON file in
  `[ads] breaks_dir` (default `/var/lib/cztvhub/breaks`); the retention loop deletes files of recordings that no
  longer exist.
- `GET /dvb/recording/<id>/breaks` (app key) →
  `{"status": "done" | "pending", "breaks": [...]}`; `pending` with `[]` while not analysed yet. `404` when `[ads]`
  is off. The app key cannot `PUT` (`403`).
- A second worker recording while the worker's previous recording session is still alive → `409`.
- The EPG's `recording` object gains `"breaks": true` when breaks are stored, so the guide could mark such recordings
  (optional for the apps).

## Detection (worker, v1 "logo-v1")
Czech commercial channels hide their on-screen logo during ad breaks and separate breaks with jingles.
1. ffmpeg reads the recording's HLS with the worker key (`-headers "Authorization: Bearer …"`), decodes **video
   keyframes only** (`-skip_frame nokey`, ~1 frame per 1–2 s), crops the four corners at low resolution (grey, e.g.
   160×90 each) and decodes **audio** as 8 kHz mono; both go to pipes, nothing to disk. Cheap enough to run faster
   than real time on a PC and probably on a Pi 4 for finished recordings overnight (to be measured).
2. **Logo mask, learned automatically per channel**: pixels in a corner that stay nearly constant across many
   keyframes of many recordings are the logo. The mask (a few KB of numbers, no picture of the programme) is kept
   in the worker's state directory and refined over time; no manual labelling needed. Masks stay private household
   data: never committed to the public repo, never shipped in builds, never prebuilt per channel (the state directory
   is in `.gitignore`).
3. Per keyframe: logo present / absent score. Breaks = runs of "absent" ≥ 60 s, gaps ≤ 20 s merged, edges moved to
   the nearest black frame or silence (audio RMS) within ±5 s, longer than 20 min → discarded (probably a film
   without a logo, not ads).
4. `confidence` from the logo score margin; breaks below 0.5 are not uploaded.
Later (v2): jingle fingerprints per channel (audio), repeated-spot detection across recordings, analysing growing
recordings so breaks appear a few minutes behind live.

## App behaviour
- Only for "Anténa" recordings (`dvbRecorded` play requests), never for CT internet streams.
- On start and every 60 s while playing: `GET /dvb/recording/<id>/breaks` with the app key. Failures are silent
  (no button, no error message). Parsing drops entries without numeric `start < end`, sorts by `start`, defaults
  `confidence` to 1.
- When the position is inside a break and at least 3 s before its end, show **"Přeskočit reklamu"** bottom right
  (focusable; on Samsung the OK key presses it while it is visible, otherwise OK keeps meaning pause). Pressing it
  seeks to `end`. The button hides after a skip or when the break ends; it is not shown again for the same break
  unless the viewer seeks back into it.
- A still-growing recording (start-over of an airing programme) shows no button in v1.
- No app setting in v1: the household switches the feature on or off on the gateway.

## Developing without a tuner
`deploy/cztvhub-dev.conf` enables `[ads]` with worker key `dev-worker-dev-worker-dev-worker-00`. With `dvb-fake`
and the dev gateway running, store test breaks for a fake recording (its clips are 60 s long) and play it in an app:
```bash
W="Authorization: Bearer dev-worker-dev-worker-dev-worker-00"
curl -H "$W" "http://127.0.0.1:8098/dvb/ads/jobs?analyzer=logo-v1"        # pick a recordingId
curl -X PUT -H "$W" --data '{"analyzer":"logo-v1","breaks":[{"start":20,"end":45}]}' \
  http://127.0.0.1:8098/dvb/recording/<recordingId>/breaks
```

## Tests
- Gateway: scopes of the two keys, jobs listing (channel filter, analyzer version), PUT validation, retention
  deletes breaks, `404` when off.
- Worker: break building from synthetic logo/silence score sequences (golden cases in `spec/fixtures/ads/`, synthetic
  numbers only, no recorded TV, no logo masks); the ffmpeg command line has only `pipe:` outputs (no `-f segment`,
  no file output, no `-c copy`); nothing written outside the state directory.
- Gateway: the worker key is refused as a cookie, a second concurrent worker session is refused, `worker_hosts`.
- Apps: button visibility and skip target from the golden cases, in all three apps.

## Legal notes (see `docs/legal-notes.md`)
- Skipping ads in one's own private recording is the viewer's own choice, like fast-forwarding a VCR; the recording
  stays unchanged and nothing leaves the household.
- Only transient in-memory copies during analysis; nothing is written to disk except timestamps and logo masks.
  No download/export endpoint is added; the worker key cannot reach live TV.
- Break lists and logo masks are household data: never published, shared or aggregated across households (no
  community break database), which would turn this into a service for others.
- Same access rules as the rest of the gateway: LAN or the household tailnet, never the public internet.
