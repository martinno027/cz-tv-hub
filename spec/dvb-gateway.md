# DVB-T2: architecture and gateway API

Approved by the user on 2026-10-18 (see `CLAUDE.md` hard rules and `docs/legal-notes.md`): live TV, rewind and
recordings kept for 3 days (configurable, max 14) from the household's own DVB-T2 tuner, watchable on the LAN and on
the household's Tailscale tailnet.

```
antenna ─▶ tuner (USB in the Pi, or network tuner) ─▶ TVHeadend (Pi, 127.0.0.1:9981, login)
                                                         │  EPG (EIT), DVR: autorec of every programme
                                                         ▼
                              cztvhub gateway (Pi, :8098, /dvb/*, bearer key)  ◀── apps (LAN / tailnet)
                              - JSON: channels, EPG with recording state
                              - HLS: live + recordings, remuxed on demand by ffmpeg
```

- **TVHeadend** (apt package) owns tuners, scanning, EPG and recording. It listens on `127.0.0.1` only; the gateway
  talks to it with its own TVHeadend user. Free-to-air services only; no CA/descrambling configured.
- **Recordings**: the gateway records the channels selected on `/admin` **continuously** (see "Continuous recording",
  approved by the user 2026-10-23). The older per-programme recording by TVHeadend autorec rules (`dvb-setup`,
  retention `retention_days` through TVHeadend's DVR removal and the gateway's cleanup loop) still works for installs
  that set it up; channels recorded continuously take their recordings from the continuous recording only.
- **Gateway** = the existing `cztvhub serve` with DVB enabled in its config. Python standard library only; uses the
  system `ffmpeg` binary (apt) as a subprocess.
- **Why remux**: tvOS AVPlayer cannot play raw MPEG-TS over HTTP and needs HEVC in fMP4 HLS (`hvc1`). The gateway runs
  `ffmpeg -c:v copy` (no video transcoding) and converts audio to AAC unless it already is AAC, writing fMP4 HLS into a
  temporary directory. One ffmpeg per source, shared by all viewers; stopped and its directory deleted
  `idle_seconds` (default 30) after the last request. Android uses the same HLS.
- **Discovery**: avahi advertises `_cztvhub._tcp` on port 8098 on the LAN interface only. Over Tailscale the app uses the
  Pi's tailnet name (MagicDNS), entered once in the app settings.

## Config `/etc/cztvhub/cztvhub.conf` (INI, read with configparser)
```ini
[server]
host = 0.0.0.0          ; or the LAN + tailscale addresses
port = 8098

[dvb]
enabled = false
key = <random 32+ chars>       ; apps send "Authorization: Bearer <key>"
retention_days = 3
record_channels = all          ; or comma-separated TVHeadend channel names
hls_dir = /var/lib/cztvhub/hls  ; on the recordings disk, cleaned on start
idle_seconds = 30

[admin]
password =                     ; web admin /admin (user "admin"), empty = off; 12+ chars, needs DVB

[tvheadend]
url = http://127.0.0.1:9981
user = cztvhub
password = ...
```
- Without `--config` (or with `enabled = false`) the server is the CT-only server. `enabled = true` requires a
  key of at least 32 characters; `retention_days` is clamped to 1..14. Optional `[dvb] ffmpeg` / `ffprobe` paths.
- Template: `deploy/cztvhub.conf.example` (filled in by `deploy/install.sh` / `install-dvb.sh`).
- The TVHeadend user needs admin rights (tuner status, `dvb-setup`), restricted to `127.0.0.1/32`.

## TVHeadend API used (`src/cztvhub/dvb/tvheadend.py`)
Checked against TVHeadend's source (master, `src/api/api_epg.c`, `api_dvr.c`, `api_status.c`, `dvr/dvr_db.c`,
`dvr_autorec.c`, `dvr_config.c`, `webui/webui.c`); not yet against a real TVHeadend:
- `api/channel/grid`, `api/epg/events/grid` (numeric `filter` on `start`/`stop`), `api/dvr/entry/grid*`,
  `api/dvr/entry/remove`, `api/status/inputs` (admin only), `api/dvr/config/grid`, `api/dvr/autorec/grid|create`,
  `api/idnode/save|delete`, `api/mpegts/service/grid`, `api/mpegts/mux/grid`, `api/dvr/entry/cancel` (admin page),
  `/stream/channel/<uuid>?profile=pass`, `/dvrfile/<uuid>` (Range), `/imagecache/<n>`.
- A live subscription without a free tuner does not reliably answer `503`: TVHeadend may end the stream without
  data. The gateway then asks `api/status/inputs`; all tuners busy → `503 Tuner je obsazený`, else `502`.
- `/dvrfile` answers a Range start at the end of a growing file without data (not `416`); the gateway polls.
- DVR retention in TVHeadend is a list (1, 3, 5, 7, 14 days); `dvb-setup` picks the next listed value ≥
  `retention_days`, the gateway deletes at exactly `retention_days`.

## Gateway API (all `/dvb/*` require the bearer key; 401 otherwise; JSON UTF-8; times ISO-8601 UTC)
Implemented in `src/cztvhub/dvb/gateway.py`. The bearer key is required on **every** `/dvb/*` request, including
HLS playlists and media segments, so players must send the header on all requests (AVURLAsset
`AVURLAssetHTTPHeaderFieldsKey` / Media3 `DefaultHttpDataSource.Factory.setDefaultRequestProperties`).
Players that cannot set headers (Samsung AVPlay) send the same key as cookie `cztvhub_key=<key>`
(`setStreamingProperty("COOKIE", …)`); the gateway accepts either.
- `GET /dvb/status` → `{"enabled": true, "retentionDays": 3, "tuners": [{"name": "...", "inUse": false}],
  "disk": {"freeBytes": 0, "totalBytes": 0}, "tvheadendReachable": true}`
  (always 200; `tuners` is `[]` and `tvheadendReachable` false when TVHeadend does not answer; `disk` = the
  filesystem of `hls_dir`)
- `GET /dvb/channels` → `{"channels": [{"id": "<tvh channel uuid>", "number": 1, "name": "ČT1 HD", "icon": "/dvb/icon/<id>" | null}]}`
  (sorted by number; `/dvb/icon/<id>` proxies TVHeadend's picon if it has one)
- `GET /dvb/epg?date=YYYY-MM-DD` (Prague broadcast day 05:00 local to 05:00 next day, DST-aware; without `date`
  the current broadcast day; range: today − retention … today + 2, else `400`) →
  `{"date": "YYYY-MM-DD", "events": [{"id": "<eventId>", "channel": "<channel id>", "title": "...", "subtitle": "...",
  "description": "...", "genre": "...", "start": "...Z", "end": "...Z", "recording": null |
  {"id": "<dvr entry uuid>", "state": "completed" | "recording" | "scheduled" | "failed"}}]}`
  - Events **overlapping** the day window, sorted by channel then start (an event across 05:00 is in both days).
  - TVHeadend forgets EPG events once they end, so past programmes come from DVR entries (their `id` is
    `dvr:<dvr entry uuid>` and `recording` is always set) or, without a recording, from the gateway's EPG history
    (`recording: null`, see "EPG history"). Treat `id` as an opaque string.
  - `genre` is a Czech label from the DVB content nibble ("Zprávy", "Film / drama", …) or `""`.
  - State mapping from TVHeadend: `recording*` → recording, `scheduled` → scheduled, `completed` → completed,
    everything else (`completedError`, `completedRerecord`, `completedWarning` = missed) → failed.
- `GET /dvb/live/<channel id>.m3u8` → live HLS (fMP4, a new segment at the first keyframe after 1 s, i.e. one ČT
  GOP of ~1.3 s; 40 kept; no `EXT-X-PLAYLIST-TYPE`). `#EXT-X-TARGETDURATION` is fixed at 2 (it only grows if a
  segment ever needs more; ffmpeg's own value grows with the longest segment so far, which HLS forbids). The first
  request of a session blocks until **6 s** of media exist (3 × target, Apple's live hold-back), so players start
  with a cushion; later requests answer at once. Before (2026-10-23) the Apple TV started 1.3 s from the live edge
  with a target of 1 and stalled / glitched ("restarting 1.28s from end of live playlist; target duration 1s - stall
  danger"). Media files under
  `/dvb/hls/<session>/` (`init.mp4`, `seg_NNNNN.m4s`; the playlist uses absolute paths).
- `GET /dvb/recording/<dvr entry uuid>.m3u8` → HLS of a recording (`404` unless its state is `completed` or
  `recording`). `EVENT` playlist while it is still recording (start-over of an airing programme; it grows until
  the recording ends, then gets `#EXT-X-ENDLIST`). A finished recording is `VOD` with `#EXT-X-ENDLIST` when its
  remux completes within 5 s of the first request; a longer one is served as `EVENT` that gains `#EXT-X-ENDLIST`
  when the remux is done (the type never changes within one session, as HLS requires). Seeking within the
  remuxed part.
- Video is copied (`hvc1` for HEVC, `avc1` for H.264); audio = the **first** audio track, AAC (copied if it already
  is AAC, else transcoded to stereo AAC 192 kbit/s).
- Sessions are shared by all viewers of the same source and deleted `idle_seconds` after the last request
  (playlist or segment). Players must keep polling the playlist; a paused player may lose the session.
- No download/export endpoint, ever. Range requests only on HLS media files.
- Optional ad breaks of recordings ("Přeskočit reklamu", off by default): `GET /dvb/recording/<id>/breaks` for the apps,
  plus worker-only routes with a separate scoped key; see `spec/ad-breaks.md`.
- Errors: `400` bad `date`, `401` missing/wrong key, `404` unknown id or DVB disabled,
  `503 {"error": "Tuner je obsazený"}` when TVHeadend has no free tuner, `502` TVHeadend unreachable or the stream
  could not be started, Czech `error` text in JSON bodies. When a live start finds no free tuner, the gateway
  first stops live sessions nobody has polled for 8 s (zapping) and retries once.

## Continuous recording (`buffer.py`, `recorder.py`, `archive.py`, `space.py`)
Approved by the user on 2026-10-23 (CLAUDE.md hard rules, `docs/legal-notes.md`; legal-guard: approve with changes).
- **Recorder**: per channel switched to "Nahrávat" on `/admin` (and offered, not scrambled), one ffmpeg reads
  TVHeadend's local stream (`/stream/channel/<uuid>?profile=pass`; the gateway refuses any TVHeadend that is not on
  `127.0.0.1`, CT internet streams are never a source), copies the video and writes fMP4 segments (`-hls_time 2`,
  cut at keyframes, ~2.6 s for ČT) with **every audio track muxed**: AAC, AC-3 and E-AC-3 copied, MPEG audio and
  others converted to AAC 192 kbit/s. Track languages and Czech labels ("Čeština", "Angličtina (Dolby)",
  "Čeština (pro nevidomé)") are kept per run. A mover takes each finished segment from ffmpeg's rolling playlist with
  its `#EXT-X-PROGRAM-DATE-TIME` and files it in a **10-minute block** (`<buffer_dir>/<channel>/blocks/<UTC
  YYYYMMDDTHHMM>/`, `index.jsonl`); segments are numbered per channel. When the source or ffmpeg ends, a new run
  starts after 10 s (60 s while no tuner is free). DVB-T2 audio is HE-AAC in LATM (`aac_latm`, seen 2026-10-23 on
  Nova: two Czech tracks, HE-AAC and HE-AACv2), which MP4 cannot carry, so it is re-encoded (`-aac_coder fast`,
  128 kbit/s, one ffmpeg thread per recorder). Ten recorded channels overloaded a Pi 4 (load 29, TVHeadend stopped
  answering); after the cheaper encoding one channel costs ~20 % of a core. An optional cap `[dvb] max_recordings`
  (default 0 = none, the household watches the load) limits channels recorded at once and `/admin` refuses more; and
  recording is refused while `buffer_dir` is on the system disk (SD card) unless `buffer_on_system_disk = true` (dev). Subtitles are not recorded yet (teletext → WebVTT needs a check of
  real broadcasts and the Pi's ffmpeg first).
- **Config**: `[dvb] buffer_dir` (default `/var/lib/cztvhub/buffer`; on the Pi the recordings disk, e.g.
  `/srv/tv/buffer`, the unit allows writing below `/srv/tv`). Selection and the day limit live in
  `<state dir>/settings.json` (`recordChannels`, `maxDays`), set on `/admin`; `[dvb] retention_days` is only the
  default of `maxDays`.
- **Retention** (every 60 s, `space.plan`, unit-tested): (1) **age always wins**: blocks older than `maxDays` (1..14,
  clamped) are deleted whatever the free space; (2) **reserve**: while less than max(10 % of the disk, 5 GB) is free,
  the oldest block of the channel holding the most hours is deleted (a channel's newest block never), so all recorded
  channels keep about the same last N hours; (3) if the reserve still cannot be reached, **recording pauses** (resumes
  once reserve + 1 GB is free). TVHeadend's own free-space setting does not cover these files. Switching "Nahrávat"
  off or hiding a channel deletes its recording at once; recordings of channels no longer selected or offered are
  deleted on the next pass (never while TVHeadend is unreachable).
- **Programmes**: in `/dvb/epg` an event of a recorded channel gets `recording: {"id": "b<channel>-<start>-<end>",
  "state"}` (epoch seconds of the event): `scheduled` (future, channel recorded), `recording` (airing, recorder
  running, something recorded), `completed` (ended, ≥ 30 s recorded), else `null` ("Pořad nebyl nahrán").
  `GET /dvb/recording/b….m3u8` only for a real EPG programme of an offered channel (exact times, from TVHeadend or the
  EPG history; at most 12 h): from 30 s before the start to 60 s after the end; `EVENT` with a fixed target of 3 s
  while airing, `VOD` + `ENDLIST` once ended. `#EXT-X-PROGRAM-DATE-TIME` on every segment, `#EXT-X-DISCONTINUITY`
  (+ new `#EXT-X-MAP`) across runs and gaps > 1 s. Positions (resume, ad breaks) count from the playlist start.
- **Audio choice**: with one audio track the playlist is the muxed media playlist (as before). With more, a master
  playlist: `#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud"` per track (`NAME`, `LANGUAGE` as RFC 5646, `DEFAULT=YES` on the
  first Czech non-audio-description track, `CHARACTERISTICS="public.accessibility.describes-video"` on audio
  description) and one video variant. The gateway cuts each rendition out of the same muxed fragments
  (`fmp4.split_init` / `split_segment`, after the A/V-sync and open-GOP fixes), so the tracks stay in sync.
- **Live**: `/dvb/live/<id>.m3u8` of a channel being recorded (a segment in the last 20 s) is served from the
  recording: the last ~40 s, fixed target 3 s, `MEDIA-SEQUENCE`/`DISCONTINUITY-SEQUENCE`, a few seconds behind the
  broadcast, no extra tuner. Other channels use the on-demand tuner session as before.
- **Views**: playlists and media of the recording are served only under `/dvb/v/<view id>/…` (same key): a view is one
  programme or the live window of one channel, kept 4 h after its last request. Media outside the view's range,
  unknown views and anything else answer `404`; there is no listing, no static file serving of `buffer_dir` and no
  playlist spanning "everything" (legal-guard).
- **Thumbnails** of `b…` recordings come from one segment of the recording (init + segment, ffmpeg pipes), same rules.
- **Ad breaks** (`spec/ad-breaks.md`): ended `b…` programmes of `[ads] channels` are analysable; the worker gets its
  own views (`owner=worker`), one programme at a time (another one only after 2 min without requests).
- `/admin`: per channel "Nahrávat" (`POST /admin/api/channels[/<id>]` `{"record": bool[, "ids"]}`; refused for hidden
  or scrambled channels), "Uchovávat nahrávky nejvýše" (`POST /admin/api/settings {"maxDays": 1..14}`), tab
  "Přehled nahrávek" (`GET /admin/api/storage`: disk, reserve, used, `maxDays`, `paused`, estimated hours per channel at
  the measured bitrates, and per channel: recording, state, hours, oldest/newest, bytes, measured GB/h, gaps). The page
  states that the recording is a private copy for this household, deleted after N days or earlier and not
  downloadable.

## Channel administration (`/admin`, `src/cztvhub/dvb/admin.py`)
A web page for the household (LAN / tailnet only, like the rest) to choose which antenna channels are offered.
On when `[admin] password` is set; HTTP Basic, user `admin` (separate from the app key, which stays read-only).
TVHeadend is the only state, so hiding a channel there by hand has the same effect on the apps.
- **Hide** a channel: TVHeadend channel `enabled = false` (gone from `/dvb/channels`, EPG, live `404`, its recordings
  `404`); its services disabled (unless shared with an offered channel); each of its multiplexes set to *Disable*
  once no offered channel uses it, so the tuner never tunes to it (no reception, no EPG scan); its `cztvhub`
  autorec rule disabled; scheduled entries cancelled (`dvr/entry/cancel`, also aborts a running recording) and its
  finished/failed recordings removed. A running live session of the channel is stopped.
- **Show**: the reverse (channel, services, disabled multiplexes, rule back on; also repairs an offered channel
  whose service or multiplex is off). Service and mux grids are read with `hidemode=none`, else TVHeadend leaves
  out services of disabled multiplexes. No rule is created before
  `dvb-setup` has run (recording stays a deliberate step). EPG returns once TVHeadend rescans the multiplex.
- `dvb-setup` keeps rules of disabled channels, disabled, instead of deleting them.
- `GET /admin` page, `GET /admin/api/channels` → `{"channels": [{"id", "number", "name", "offered", "received",
  "multiplexes": [names], "recordingRule": null | bool, "recordings": n, "now": null | {"title", "subtitle",
  "genre", "start", "end"}}], "status": <as /dvb/status>, "recordingSetUp": bool, "retentionDays": n}`, sorted by
  channel number (hidden ones stay in place); `now` = the EPG event airing now (none while not received).
- `POST /admin/api/channels/<id>` `{"offered": bool}`, or several at once (the page's bulk action, one confirmation)
  `POST /admin/api/channels` `{"ids": [...] (1..500), "offered": bool}` → `{"changes": ["…Czech…"]}`; unknown ids
  → `404` before anything changes; multiplex decisions consider all channels of the request together.
- **Rename**: `POST /admin/api/channels/<id>` `{"name": "…"}` or `POST /admin/api/channels` `{"names": {id: name}}`
  (1..64 printable chars, whitespace collapsed) → TVHeadend `name` with `autoname = false` (a rescan keeps it); the
  apps show it from `/dvb/channels`. Each row carries `shortName`, a TV-style suggestion ("CT 1 PHA HD T2" → "ČT1",
  "Nova HD" → "Nova"; the page's "Zkrátit názvy" applies it with one confirmation). Old names still listed in
  `record_channels` / `[ads] channels` are reported in `changes` (those settings match by name). POSTs need header `X-Cztvhub-Admin: 1` and a same-origin `Origin` if one is sent
  (CSRF; the server never answers CORS preflights). Errors: `401`, `403`, `400`, `404` unknown channel, `502`.

## EPG history (`src/cztvhub/dvb/epgstore.py`)
The gateway remembers the EPG events it sees (every 30 min the next 3 days, plus every `/dvb/epg` answer) in
`<state dir>/epg-history.json`: titles, times, genre only. For the not-yet-started part of a fetched window
TVHeadend is authoritative (a cancelled or moved programme is dropped). Entries older than `retention_days + 1`
days are pruned. `/dvb/epg` adds remembered events TVHeadend no longer lists, except where a DVR entry for the
same channel starts within 2 min (the recording is shown instead), so a past programme without a recording stays
in the guide ("Pořad nebyl nahrán").

## Diagnostics: `cztvhub dvb-diag --config FILE [--channel NAME] [--seconds 120]`
Run on the Pi while a TV plays the channel: follows the same live session through the local gateway and prints
the tuner's signal and error counters (TVHeadend `status/inputs` deltas), the segment lengths / arrival gaps /
`TARGETDURATION`, and how often a player holding 6, 12 or 20 s would have stalled (`src/cztvhub/dvb/diag.py`).

## A/V sync of the remuxed HLS (`src/cztvhub/dvb/fmp4.py`)
DVB muxes carry audio and video with different start times, and a viewer joins mid-stream (video starts at the next
keyframe). ffmpeg's fMP4 writer starts every track's fragments at 0 and records the real offsets only in the init
segment's edit lists, which HLS players ignore: the sound was up to ~0.7 s off (found 2026-10-23 on Apple TV). The
gateway serves the init segment with identity edit lists and adds each track's `empty edit − first media time`
(plus a common non-negative base) to every fragment's `tfdt`, in place (sizes unchanged, Range still fine).
`tests/test_dvb_avsync.py` checks a flash + beep test stream for joins in both directions and mid-GOP
(within 60 ms, edit lists ignored).

## Open GOP (no `EXT-X-INDEPENDENT-SEGMENTS`)
ČT's DVB-T2 HEVC (1080p50) has no IDR pictures: every segment starts with a CRA followed by ~7 RASL pictures that
reference the previous segment (`dvb-diag --frames`, 2026-10-23). The playlists therefore do not claim independent
segments, so players decode across segment boundaries instead of restarting (which showed brief artefacts).

## Open-GOP keyframe order (`fmp4.fix_leading_pictures`)
ffmpeg's HLS muxer (9.0.2) writes a too small composition offset for the CRA that starts each fragment of an
open-GOP HEVC stream, so it was presented before its RASL leading pictures: at every scene cut the TV showed one
frame of the new shot, then a few of the old one (found 2026-10-23 in a screen recording; ffmpeg's plain MP4 muxer
gets it right). The gateway rewrites that offset so the CRA is presented right after its last leading picture
(in place, served init/segments only). `tests/test_dvb_avsync.py` compares the display order with the source.

## Start-up timing
Every live start logs one line (`journalctl -u cztvhub`): `dvb: live start live:<id>: tuner+TVHeadend +Xs, first
bytes +Xs, probe sample +Xs, ffprobe +Xs, first segment +Xs, cushion 6.0s +Xs = Ns`, to see where a slow channel
start spends its time.

## Thumbnails (approved by the user on 2026-10-23; see `docs/legal-notes.md`)
One still per recording so the viewer sees what a programme is ("it does not have to be current, just show what is
on"). Implemented in `src/cztvhub/dvb/thumbs.py`.
- `GET /dvb/recording/<dvr entry uuid>/thumb.jpg` (same key as all `/dvb/*`) → `200 image/jpeg`,
  `Cache-Control: private, no-store`. `404` when thumbnails are off, the entry is unknown, not `completed`/`recording`,
  its channel is not offered, or no frame could be made yet. `503` + `Retry-After: 5` (JSON `{"error": "Náhledy se
  právě připravují"}`) when both ffmpeg slots stayed busy for 10 s: try again later, never remembered as missing.
- Source: the recording only (`/dvrfile` byte range, at most 2 MB), never a live tuner subscription, never ČT internet
  streams. Frame position: `t = min(300 s, 20 % of the recorded duration)` after the start, as a byte offset from
  the file size per recorded second. ffmpeg reads stdin and writes one JPEG to stdout (`-frames:v 1`, width ≤ 480,
  fixed quality); no file outputs. No client parameters (no seek, no series).
- Cache: memory only, at most 600 entries (the apps prefetch around the guide focus, spec/apps.md "Image prefetch"); an entry is kept for the life of the recording once `t` reached 300 s or
  the recording finished, else refreshed after 60 s. Every request checks the DVR entry first (cache hit included) against TVHeadend's DVR
  list, which is fetched at most every 5 s and shared by all requests; a gone entry drops its cache entry (so a
  deleted recording stops being served within 5 s). Requests for the same entry at the same time share one ffmpeg run. Switching thumbnails off clears the cache. At most 2 ffmpeg at a time,
  10 s timeout.
- Household switch: on the `/admin` page ("Náhledy pořadů", default on), stored in `<state dir>/settings.json`
  (`/var/lib/cztvhub`, the service's StateDirectory). `GET /dvb/status` adds `"thumbnails": true|false`.
- Apps: the guide detail of an Anténa programme with `recording.state` `completed`/`recording` shows the thumbnail
  when the gateway reports `thumbnails: true` and the TV's own switch (Nastavení → Server, "Náhledy anténních
  pořadů", default on) is on; otherwise the antenna placeholder. Images are kept in memory only (no disk cache).
  The apps ask for the focused programme first, then prefetch a bounded window around the focus (3 rows above,
  8 below, ±3 h), nearest first, at most 2 requests at a time (spec/apps.md "Image prefetch"); never the whole guide.

## App behaviour for "Anténa" programmes
Channels appear as a second group "Anténa" in Živě and in the TV guide (rows after the CT internet channels).
1. future → not playable ("Pořad ještě nezačal").
2. airing → choice "Živě" (`/dvb/live/…`) or "Od začátku" (`/dvb/recording/<id>.m3u8` if `recording.state == recording`;
   otherwise only Živě).
3. ended with `recording.state == completed` → play the recording.
4. ended without a usable recording → dimmed, "Pořad nebyl nahrán".
Settings: gateway address (Bonjour-discovered on the LAN, or typed tailnet name) + key, stored in the platform keychain /
encrypted prefs. Without settings the "Anténa" group is hidden.

## Developing without a tuner
`python3 -m cztvhub dvb-fake` runs a fake TVHeadend on 127.0.0.1:9981 (JSON API subset + a generated HEVC/MPEG-audio
MPEG-TS test stream via `ffmpeg -f lavfi testsrc`, plus a few short fake recordings), so the gateway and both apps can be
developed and tested end to end on the Mac.

```bash
# terminal 1: fake TVHeadend (user cztvhub / password fake, 2 tuners, data in $TMPDIR/cztvhub-fake)
PYTHONPATH=src python3 -m cztvhub dvb-fake
# terminal 2: gateway with the dev config
PYTHONPATH=src python3 -m cztvhub serve --config deploy/cztvhub-dev.conf
curl -H "Authorization: Bearer dev-key-dev-key-dev-key-dev-key-0000" http://127.0.0.1:8098/dvb/channels
```

`deploy/cztvhub-dev.conf` binds 127.0.0.1:8098 (the tvOS simulator reaches it directly, the Android emulator via
`10.0.2.2`; real devices are not meant to use the dev setup), key
`dev-key-dev-key-dev-key-dev-key-0000`, `hls_dir = /tmp/cztvhub-hls`. Fake data: 4 channels (stable ids across
restarts). "ČT1 HD" is autorecorded: past 30-min slots are `completed` (each plays a 60 s test clip,
`--clip-seconds`), one is `failed`, the airing slot is `recording` in real time, the next three are `scheduled`.
"ČT2 HD" has completed past slots only, the other two none. `--tuners 1` helps to test `503`. The Android emulator
reaches the Mac as `10.0.2.2`; the tvOS simulator as `127.0.0.1`.

## Sizing (for buying hardware)
- MUX 21 carries all CT channels; commercial channels are in other multiplexes. One tuner = one multiplex with all its
  channels, so recording needs one tuner per multiplex; recorded channels play "Živě" from the recording, so an extra
  tuner is only needed to watch a channel that is not recorded, from another multiplex.
- Measured on a Pi 4 (2 GB), Raspberry Pi OS 64-bit, WinTV-dualHD, 3 channels for 11 min (2026-10-23): per recorded
  channel ~12 % of one core on average (6–8 % with one audio track, ~22 % with two HE-AAC tracks; the LATM audio is
  re-encoded, video copied), ~67 MB RAM, 0.9–1.1 GB/h on disk; TVHeadend ~2 % / 80 MB, the gateway ~5 % / 80 MB;
  load 0.57, 39 °C, not throttled.
- Extrapolated: a Pi 4 manages roughly 12–16 channels (memory and the two-track channels are the limit); ~27 channels
  from 4 multiplexes need 4 tuners, a Pi 5 8 GB (or an Intel N100 mini PC) and ~650 GB per day, i.e. 4 TB for 3 days
  (2 TB ≈ 2.7 days). The Czech guide (`docs/dvb-setup.md`, "Hardware podle počtu nahrávaných kanálů") has the table.
