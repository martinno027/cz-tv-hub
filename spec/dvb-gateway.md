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
  (+ new `#EXT-X-MAP`) across runs and gaps > 1 s. Positions (ad breaks) count from the playlist start. While
  airing (`EVENT`) the media playlists carry `#EXT-X-START:TIME-OFFSET=0,PRECISE=YES` (also the TVHeadend DVR's
  growing recordings): without it players start a growing playlist at its live edge, so "Od začátku" of an airing
  programme played live (AVPlayer ignored the app's seek to 0, tvOS simulator 2026-10-24).
- **Channel timeline** (approved by the user on 2026-10-24; legal-guard: approve with changes): the apps play a
  recorded programme (airing or ended) inside a bounded window of its channel's recording, so the viewer can go
  before its EPG start (EPG times are often off) and on into the neighbouring programmes, as far as the recording
  reaches, one window at a time:
  - `GET /dvb/timeline/<b… id>` (JSON) and `GET /dvb/timeline/<b… id>.m3u8` (playlist, master or media as for
    programmes, served under a view like them). The id must be a real EPG programme of an offered, recorded channel
    validated exactly like `/dvb/recording/b…` (state `recording` or `completed`); the client never sends start/end.
  - **Window** = from `start − 3 h` to `end + 3 h` of that programme (`TIMELINE_MARGIN`), at most **12 h** in total
    (`MAX_PROGRAMME`; a longer programme gets equal smaller margins, never below 0), clipped to what is recorded
    and to now. `EVENT` (fixed target 3 s, growing) while the window's end is in the future, `VOD` + `ENDLIST` after.
    `#EXT-X-START:TIME-OFFSET=<position of programme start − 30 s, ≥ 0>,PRECISE=YES` so a player without a seek
    starts at the programme. Discontinuities, `#EXT-X-MAP`, `#EXT-X-PROGRAM-DATE-TIME` and the audio renditions as
    for programmes.
  - JSON: `{"id", "channel", "playlist": "/dvb/timeline/<id>.m3u8", "complete": bool, "programme": {"start", "end"},
    "pieces": [{"position": 0.0, "time": "…Z", "duration": 1234.5}], "programmes": [{"id": "b…" | null, "title",
    "subtitle", "start", "end", "breaks": bool}], "previous": "b…" | null, "next": "b…" | null}`.
    `pieces` = the recorded stretches in playlist order (a new piece after every discontinuity: new run or gap > 1
    s), `position` = sum of `EXTINF` before it in the playlist, `time` = the program date-time of its first segment,
    `duration` = its `EXTINF` sum; the last piece of a growing window keeps growing (re-fetch the JSON every 60 s).
    `programmes` = the EPG programmes overlapping the window (from TVHeadend or the EPG history), `id` when the
    programme is a valid `b…` id, i.e. one `/dvb/timeline` accepts (`breaks` = `[ads]` breaks stored for it);
    zero-length EPG events are no programmes. `previous` = the programme with a valid
    `b…` id airing 1 s before the window's first recorded media, else the latest such programme starting before it;
    `null` when nothing is recorded before the window. `next` = the same after the window's last recorded media, only
    once the window is complete and something is recorded after it. The app opens `previous`/`next` with its own
    `/dvb/timeline/<id>` (a new window) and continues at the same wall clock.
  - Same key, same view rules (below); the worker key and thumbnails never use timelines. At most **4 timeline views
    per channel and key** at a time; opening a fifth drops the one requested longest ago (the app key is
    shared by all TVs of the household, so this counts per channel for the whole household; 2 until
    2026-10-24, raised for that reason). Errors as for programmes (`404` not available).
- **Audio choice**: with one audio track the playlist is the muxed media playlist (as before). With more, a master
  playlist: `#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud"` per track (`NAME`, `LANGUAGE` as RFC 5646, `DEFAULT=YES` on the
  first Czech non-audio-description track, `CHARACTERISTICS="public.accessibility.describes-video"` on audio
  description) and one video variant. The gateway cuts each rendition out of the same muxed fragments
  (`fmp4.split_init` / `split_segment`, after the A/V-sync and open-GOP fixes), so the tracks stay in sync.
  Every rendition lists the same segments: in a run with fewer tracks a missing rendition plays that run's first
  track (before 2026-10-24 it left those segments out, so its positions differed from the video's).
- **Live**: `/dvb/live/<id>.m3u8` of a channel being recorded (a segment in the last 20 s) is served from the
  recording: the last ~40 s, fixed target 3 s, `MEDIA-SEQUENCE`/`DISCONTINUITY-SEQUENCE`, a few seconds behind the
  broadcast, no extra tuner. Other channels use the on-demand tuner session as before.
- **Views**: playlists and media of the recording are served only under `/dvb/v/<view id>/…` (same key): a view is one
  programme, the live window of one channel, or a **timeline window** of one channel (anchored on a real EPG
  programme of an offered, recorded channel; programme ± 3 h, at most 12 h in total, clipped to what is recorded and
  to now); the next window is a new view requested by the app near the edge. Kept 4 h after its last request. Media
  outside the view's range, unknown views and anything else answer `404`; there is no listing, no static file
  serving of `buffer_dir` and no playlist spanning a channel's whole recording (legal-guard, approved 2026-10-24).
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
  apps show it from `/dvb/channels`. Each row carries `originalName`: the name of the channel's first service (what
  TVHeadend's autoname uses; nothing extra is stored), `null` when it equals `name` or the channel has no service; the
  page shows it small and dimmed under the name. Each row carries `shortName`, a TV-style suggestion ("CT 1 PHA HD T2" → "ČT1",
  "Nova HD" → "Nova"; the page's "Zkrátit názvy" applies it with one confirmation). Old names still listed in
  `record_channels` / `[ads] channels` are reported in `changes` (those settings match by name). POSTs need header `X-Cztvhub-Admin: 1` and a same-origin `Origin` if one is sent
  (CSRF; the server never answers CORS preflights). Errors: `401`, `403`, `400`, `404` unknown channel, `502`.
- **Filters** above the list, client-side only (no request, nothing stored on the server; kept in the URL hash
  `#q=…&offered=on|off&rec=on|off&mux=<name>`), combined with AND: text in `name` or `originalName` (case- and
  diacritics-insensitive, every word must match: "ct" finds "ČT1"), shown/hidden (`offered`), recorded
  (`record` or `recordingRule == true`) / not, one multiplex (from those in the rows). The page shows "N z M kanálů".
  Select-all, the bulk buttons and "Zkrátit názvy" act only on visible channels; a filter change drops the selection
  of channels it hides.

## Profiles (T08, `src/cztvhub/dvb/profiles.py`)
A household member can keep the TV apps' settings in a **profile** on the Pi and load them on any TV by signing in
with name + password. Only settings travel (channel order/hidden/sources and the other Nastavení switches, below),
never positions ("Pokračovat"), the gateway address or key, recordings or anything about viewing.
- **Storage**: `<state_dir>/profiles.json` (written atomically, `0600`, user `cztvhub`), at most **20 profiles**.
  Per profile: `name` (as typed, trimmed), the password as **PBKDF2-HMAC-SHA256** (`hashlib.pbkdf2_hmac`, random
  16-byte salt, 200 000 iterations, both stored), `settings` (the document below), `version` (integer, +1 per
  save), `created`, `updated`, and **sessions**: at most 10 per profile (the oldest dropped), each only the SHA-256
  of its token plus `created`/`lastUsed`. No password or token is ever stored or logged in clear.
- **Name**: 1–32 characters after trimming, no control characters; unique ignoring case and diacritics
  ("Jana" = "jána"). **Password**: 4–64 characters (a TV remote types slowly; this is a household, not the internet).
- **Settings document** (version 1, ≤ 64 KB, else `413`): `{"v": 1, "channelPrefs": {"order": [...], "hidden":
  [...], "sourcesOff": [...]}, "thumbnails": true}` — `channelPrefs` exactly as `spec/apps.md` "Kanály" stores it,
  `thumbnails` = the TV switch "Náhledy anténních pořadů". The gateway checks only the shape (object, the size, `v`)
  and stores the rest as it came, so a newer app can add fields an older one keeps (apps keep unknown fields).
- **API** (all `/dvb/*`: the app key is required first, here only as the `Authorization: Bearer` header — the
  cookie alone gets `403`, so a foreign web page cannot use the TV's cookie; the worker key gets `403`). Passwords are
  NFC-normalised when set and at login (TV keyboards may send a decomposed "á"); login attempts for one name run one
  at a time and an unknown name costs the same PBKDF2 work as a wrong password; at most 2 PBKDF2 at once; the
  session dropped at the 11th sign-in is the one unused longest. The session token goes in the
  header `X-Cztvhub-Profile: <token>` (fetch/XHR on every platform; AVPlay never needs it).
  - `POST /dvb/profiles` `{"name", "password", "settings"}` → `201 {"token", "name", "settings", "version"}`;
    `409 {"error": "Profil s tímto jménem už existuje"}`, `400` bad name/password/settings, `507` 20 profiles already.
  - `POST /dvb/profiles/login` `{"name", "password"}` → `200 {"token", "name", "settings", "version"}`; wrong name
    or password → `401 {"error": "Nesprávné jméno nebo heslo"}` (the same text for both). After **5 failures in 10
    minutes** for one name, further attempts for that name answer `429 {"error": "Příliš mnoho pokusů, zkuste to za
    minutu"}` with `Retry-After: 60` for one minute (in memory; a restart forgets it).
  - `GET /dvb/profile` → `{"name", "settings", "version", "updated"}`; `PUT /dvb/profile` `{"settings"}` →
    `{"version", "updated"}` (last write wins; the apps re-read on start and when Nastavení opens);
    `POST /dvb/profile/logout` → `204` (deletes this session only).
  - A missing, unknown or revoked token → `401 {"error": "Profil byl odhlášen", "code": "profileSignedOut"}`
    (no `WWW-Authenticate`). The apps sign out locally **only on this `code`**: a 401 of the app key (wrong or changed
    gateway key, `WWW-Authenticate: Bearer`, no such code) is shown as a gateway error and keeps the profile.
  - Request bodies are capped (64 KB settings, 1 KB login), JSON only; times ISO-8601 UTC.
- **`/admin` tab "Profily"**: name, created, last change, number of signed-in TVs; **Smazat** (with its sessions)
  and **Nové heslo** (sets a password typed by the admin and ends all sessions of that profile); **Nový profil**
  (name + password, default settings). Each of these is logged (`admin: profile <name>: deleted | new password |
  created`, never the password). State changes need the admin header and same-origin `Origin` as every `/admin` POST.
- Profiles exist whenever the gateway runs (`[dvb] enabled`, the app key); no extra config key.

## Household default channel list (T11, `src/cztvhub/dvb/defaults.py`)
One channel list for the whole household that TVs **without a profile and without their own changes** use, edited
only on `/admin` and **never linked to a profile** (approved wording of the owner, 2026-10-24: take that profile's
current list once and keep it elsewhere as the default).
- **Storage**: `<state_dir>/defaults.json` (atomic, `0600`): `{"channelPrefs": {"order", "hidden", "sourcesOff"},
  "version", "updated"}` — `channelPrefs` exactly as `spec/apps.md` "Kanály" stores it (≤ 500 keys each, ≤ 64 KB).
  No file = no household default (the apps' built-in order).
- `GET /dvb/defaults` (app key) → `200 {"channelPrefs": {...} | null, "version": <int>, "updated": "…Z" | null}`;
  `version` grows with every save, clear and copy: `max(previous + 1, now in epoch seconds)`, so it never goes back,
  also when a broken `defaults.json` is set aside (a clear keeps the file with `channelPrefs: null`); 0 = never set.
- **`/admin`, tab "Výchozí kanály"**: the list of every known channel — the six ČT channels (`channels.py`), the
  offered Anténa channels (TVHeadend, names as on the channels tab) and the five Poslanecká sněmovna streams (a
  server copy of the catalogue in `spec/fixtures/psp_cases.json`, test-checked equal) — in the default's order with
  ▲/▼, a "zobrazovat" switch per row and the three source switches; **Uložit** (`POST /admin/api/defaults
  {"channelPrefs"}`), **Zrušit výchozí** (`{"clear": true}`, back to the built-in order). Keys the gateway does not
  know (e.g. an Anténa channel hidden in `/admin`) are kept in `order` at their place.
  `GET /admin/api/defaults` → `{"channelPrefs", "version", "updated", "channels": [{"key", "name", "source":
  "ct" | "dvb" | "psp"}], "dvbError": null | "…"}` (`channels` in the built-in order; without TVHeadend only ČT and
  Sněmovna, with `dvbError`). Validation: `order` and `hidden` lists required, `sourcesOff` optional, each ≤ 500
  strings of 1–128 characters without control characters; other fields kept as they came; `400` bad document
  (also `{"channelPrefs": null}`), `413` over 64 KB, `503` the file could not be written; admin header and
  same-origin `Origin` as every `/admin` POST.
- **`/admin`, tab "Profily"**: per profile **Použít jako výchozí**: copies that profile's current `channelPrefs`
  once into the default (`POST /admin/api/profiles/<id> {"makeDefault": true}`); later changes of the profile do not
  touch it (`400` when the profile's `channelPrefs` is missing or invalid, `404` unknown profile). Every
  save/clear/copy is logged (`admin: defaults: saved | cleared | copied from profile <name>`).

## Programme details (T15, TMDb, approved by the user on 2026-10-24; `src/cztvhub/dvb/details.py`)
Extra facts for the apps' programme page: year, a poster, TMDb's rating, the original title. Off without
`[tmdb] key` in the config (the household's own free key; never in the template as a value, never logged).
- `GET /dvb/details?source=dvb&id=<event id from /dvb/epg>` or `GET /dvb/details?source=ct&channel=<CT slug>&date=
  <YYYY-MM-DD broadcast day>&idec=<idec>` (app key). **A programme id, never free text**: the gateway finds the
  programme in its own EPG (TVHeadend + EPG history) or in the CT schedule of that day (`api.py`, cached 10 min, the
  `check`-free path of `serve`), and only then asks TMDb. Unknown id → `404`; `[tmdb] key` missing → `404 {"error":
  "Podrobnosti nejsou zapnuté"}` (the apps then show the page without the TMDb lines).
- **Lookup**: title cleaned of what EPGs append (`(14)`, roman series numbers `VI`, `- díl 3`, trailing `II`/`(R)`/
  `HD`, a leading `Film:`), a year from the description when it states one (`(USA, 2010)`, `… 2010)`), the kind
  guessed (episode line or a series genre = `tv`, else `movie`, falling back to the other). TMDb `search/movie` or
  `search/tv` with `language=cs-CZ`, then `.../{id}` for details. **A match only when** the cleaned title equals the
  result's Czech or original title (casefold, no diacritics, letters and digits only) and, when we have a year, it is
  within ±1; otherwise `{"match": null}` (no rating is better than a wrong one). At most one TMDb request at a time,
  at most 30 per minute; TMDb errors → `{"match": null, "error": "…"}` with 200, nothing cached.
- **Answer**: `{"match": null | {"kind": "movie"|"tv", "title", "originalTitle", "year", "overview"?, "genres":
  [..], "runtimeMinutes"?, "rating": {"value": 7.4, "votes": 1234} | null, "poster": "/dvb/details/image/w342/<file>"
  | null, "tmdbId"}, "attribution": "This application uses TMDB and the TMDB APIs but is not endorsed, certified, or
  otherwise approved by TMDB."}`; a rating only with ≥ 20 votes.
- **Posters**: `GET /dvb/details/image/w342/<file>.(jpg|png)` (app key) fetches **only** a path the gateway got from its
  own lookup, from `https://image.tmdb.org/t/p/w342/<file>` (fixed host and size, ≤ 1 MB), kept in memory (LRU 40 MB).
  The client never sends a URL.
- **Cache**: since T13 the TMDb store (`<state_dir>/tmdb.sqlite`, see "Archive"), refreshed / deleted after N months;
  `tmdb-cache.json` of T15 (30 days) is migrated once and deleted. TMDb's title, original title, overview and runtime
  are kept in memory only (the rule stores none of them); a stored match whose texts are not in memory costs one
  `movie|tv/{id}` request when a viewer opens the page, and without TMDb the stored facts and our title are shown.
- **Details of the implementation** (T15):
  - Errors: `400` missing/invalid `source`, `id` (`[A-Za-z0-9:_-]{1,128}`), `date` or `idec`, or a CT `date`
    outside the guide's range (7 days back, 2 ahead); `404` unknown programme or CT channel, or a programme of a
    channel hidden on `/admin`; `502` TVHeadend or the CT schedule unreachable. Posters: `404` any file not from a
    current match, `502` TMDb failed (also > 1 MB or not JPEG/PNG), `503` + `Retry-After: 5` over the TMDb limit;
    `200` with `Cache-Control: private, no-store`. The worker key gets `403` on all of them.
  - The programme key is the guessed kind, the year and the cleaned title, so all episodes of a series share one
    entry. TMDb gets only the cleaned title (`query`, `language=cs-CZ`, `include_adult=false`); the year and kind
    only decide which result counts. A roman number is a series number only for `tv` ("Kobra 11 IV" → "Kobra 11");
    for a film it stays ("Rocky II"). Several results equally good (same title, no year, or the same year
    difference) are no match. The DVB subtitle is not an episode line (TVHeadend fills it with genre/summary text);
    an episode number in the title (`(14)`, `(3/12)`, `díl 3`), the CT episode title, DVB genre 0x15 or a CT genre
    containing "seriál" make it `tv`.
  - Over 30 requests per minute (posters count too) a lookup answers at once with the `error`, it does not wait;
    a lookup waits at most 10 s for the one request slot. Redirects from TMDb are not followed.
  - A poster is served only while the match it came from is in the store (≤ N months), also from memory.

## Real programme starts from the broadcast (T14, phase 1: measure; `src/cztvhub/dvb/eit.py`)
Commercial channels often start a programme minutes after its EPG time (ads), so "Od začátku" (start − 30 s) shows the
end of the previous one. Many broadcasters announce the change in the stream itself: the DVB **EIT present/following**
table (PID `0x12`, `table_id 0x4E`) names the event now running (`running_status` 4) and its start. Phase 1 only
**measures** whether that is usable on the household's channels; nothing in playback changes yet.
- **Where**: the recorder already pumps TVHeadend's MPEG-TS (`profile=pass`) through Python into ffmpeg; a small
  parser reads the TS packets of PID `0x12` on the way (no copy of the stream, no extra tuner; section CRC checked,
  defensive parsing). Only channels being recorded; nothing for live-only tuner sessions.
- **Our service**: the stream may carry the EIT of every service of the multiplex (TVHeadend's pass profile without
  "Rewrite EIT"), so only sections whose `service_id` is ours count: the one program of the (rewritten) PAT, or with
  several programs the one whose PMT PID actually appears in the stream; otherwise none.
- **What is kept**: per channel, every change of the **present** event (section 0; a change = another `event_id`,
  start, duration or `running_status`, so "starts soon" (2) → "running" (4) of one event is two lines; an empty
  present section writes nothing): `{"channel", "eventId", "title"?, "eitStart" (the start the broadcaster states;
  `null` if undefined), "eitDuration" (s or `null`), "seenAt" (wall clock when it became present), "running",
  "atTune"?}` in `<state_dir>/eit-starts.jsonl` (`channel` = TVHeadend channel uuid, times ISO UTC to the second).
  `atTune: true` marks the first present event after the recorder (re)tuned when it differs from the last one noted:
  its `seenAt` is the tune-in, not the change. The same event after a re-tune writes nothing. At most the recording
  retention (`maxDays`) and 2 MB per channel, the oldest lines dropped (checked at most hourly or after 256 kB of new
  lines; lines are appended whole, a cut last line is skipped). Event names are EPG data (like the EPG history), no
  video, no audio. If a recorder run sees no present event of our service within 5 min, one log line per recorder
  says so (`dvb: eit: no present/following on <channel> (<reason>)`) and one line `{"channel", "seenAt", "noEit":
  "none" | "otherServices" | "unknownService", "services"?, "serviceId"?}` goes into the same file, so the report
  tells that apart from "accurate" (`none` = no EIT p/f of any service, `otherServices` = only of other service ids,
  `unknownService` = several services and the PAT does not tell ours). A parser exception stops sniffing for that run
  only (logged once per recorder); ffmpeg always gets the bytes unchanged.
- **Report** (`src/cztvhub/dvb/starts.py`): `cztvhub dvb-starts --config FILE [--hours 24] [--channel NAME]` matches
  each present-event change with the EPG programme (TVHeadend + EPG history) by time and title and prints per channel:
  programme, EPG start, EIT start, seen at, delay `seenAt − EPG start` (and `eitStart − EPG start`), then a summary
  (median, range, share within ±1 min) per channel and in total. Consecutive lines of one event are one airing,
  timed by its line with `running` 4 (else its first). Matching: the same title (case and spaces ignored) with the
  nearest start within 3 h; else the programme airing at the EIT start or starting within 15 min of it (noted "v EPG:
  <title>"); zero-length EPG events are no programmes. `atTune` and unmatched airings are listed but not counted.
  Recorded channels without any line and `noEit` lines are listed with the reason. Read-only, runs on the Pi like
  `dvb-diag`.
- **Fake TVHeadend**: its live streams carry EIT p/f of service 1 (channel 1 also of a service 2), switching 20 s,
  2:30 and −0:30 after the EPG start on channels 1-3 (names in ISO 6937, UTF-8 and ISO 8859-2), none on channel 4.
- Phase 2 (only after the owner has looked at a day of the report): use the measured start for "Od začátku", the
  timeline's programme ticks and ▲/▼ where the channel's EIT proved accurate.

## Archive (T13, draft for the owner's review; `src/cztvhub/dvb/archive_catalog.py`, `tmdbstore.py`)
The apps' **Archiv** tab: iVysílání and the household's own recordings in shared categories, searchable by title.
The gateway builds it, because it alone may call TMDb and it knows the recordings; the apps only list and play
(iVysílání episodes are still resolved on the TV through ČT's VOD API — IP-bound URLs, as for the guide).
- **Sources**:
  - **iVysílání** from ČT's public GraphQL API (`api.py`, the same honest `User-Agent` as today, never the website,
    never a faked browser): the 14 categories (`3947` Filmy … `4732` Archiv), their sub-genres and sorts
    (`popular_previous_seven_days`, newest, A–Z), `searchShows`, a show's seasons and playable episodes. Cached on the
    Pi: categories and rows 6 h, a show 1 h, a search 10 min; at most 60 shows per row and 3 pages per request; one ČT
    request at a time. No crawling of whole categories.
  - **Our recordings**: ended programmes of the continuous recording within retention, on offered channels, with
    state `completed` (from the EPG + EPG history, as `/dvb/epg` annotates them). Programmes with the same cleaned
    title (`details.clean_title` for `tv`) are **one show** with episodes, newest first.
- **Categories of our recordings** (a show may be in several; golden `spec/fixtures/archive_cases.json`
  "categories"): with a stored TMDb match by its TMDb genre ids (stable across languages) — a movie → Filmy, a `tv`
  match → Seriály; Documentary 99 → Dokumenty (instead of Filmy/Seriály); News 10763 → Zpravodajství only; Talk
  10767 / Reality 10764 → Zábava (instead of Seriály); Kids 10762, or Animation 16 + Family 10751 → also Pro děti;
  History 36 → also Historie; Music 10402 → also Kultura. Without a match by the DVB genre: Film / drama → Filmy (a
  title with an episode number → Seriály), Zprávy → Zpravodajství, Zábava → Zábava, Sport → Sport, Pro děti → Pro
  děti, Hudba and Kultura → Kultura, Společnost → Společnost, Vzdělávání / věda → Dokumenty, Volný čas → Rady
  a recepty; none → only "Naše nahrávky". Computed when the catalogue is built, never stored separately.
- **One card per show**: an iVysílání show and our recordings with the same normalised title (casefold, no
  diacritics, letters and digits) are one card with `source: "both"`, **iVysílání first** (its image, year, episodes);
  the show page lists our recorded episodes as an extra group "Nahráno z antény".
- **TMDb store** (`<state_dir>/tmdb.sqlite`, 0600; replaces `tmdb-cache.json`): key = kind + year + cleaned title;
  match / no match, TMDb id, rating value + votes, genres, year, poster path, `fetchedAt`. Background worker: titles
  the archive lists that have no entry, or whose entry is older than **N months** (`/admin` setting "Obnovit údaje
  z TMDb po N měsících", any positive integer, default 5; a warning above 6: "Licence TMDb povoluje uchovávat údaje
  nejvýš 6 měsíců. Delší doba je vaše rozhodnutí a TMDb může klíč zrušit."), one TMDb request at a time, ≤ 30 per
  minute together with the page lookups, page lookups first; an entry not refreshed is deleted N months after
  `fetchedAt` (checked on read and at start). No `[tmdb] key` → no lookups and the store is deleted. Only the
  cleaned title goes to TMDb, never client text (search results are looked up by their ČT/EPG title).
- **API** (app key; worker key `403`; JSON):
  - `GET /dvb/archive` → `{"rows": [{"id": "recordings" | "<category slug>", "title", "cards": [card…], "more":
    bool}]}`: "Naše nahrávky" first (newest), then the 14 categories incl. ČT's "Archiv" (`4732`, owner 2026-10-25)
    (their ČT "popular" block with our recordings of that category mixed in by recording date), empty rows left out.
  - `GET /dvb/archive/category/<slug>?sort=popular|newest|az&genre=<sub-genre id>&page=<n>` → `{"title",
    "genres": [{"id","title"}], "cards": [...], "nextPage": n | null}` (`slug` `recordings` = ours only).
    `genres` are ČT's sub-genres of the category in ČT's order (`categoryBlocks` with a `categoryId`; the sort
    blocks "Nejsledovanější za 7 dní" / "Naposledy vysílané" have none and are left out). **With `genre` (T23)** our
    recordings of the category are mixed in when their stored TMDb match fits the sub-genre by
    `archive_cases.json` "subgenres" (sub-genre title → TMDb genre ids or kind; a sub-genre without a TMDb
    counterpart — České, Zahraniční, Pohádky, Klasika, Psychologické, Literární adaptace, the decades of Archiv … —
    and a recording without a TMDb match: ČT only) When ČT fails, the sub-genre's title is unknown: no recordings
    either, `200` with empty cards and the `error`.
  - `GET /dvb/archive/categories` (T23) → `{"categories": [{"id": "<slug>", "title"}]}`: the 14 categories in the
    row order, from the gateway's own list (no ČT request), for the apps' menu.
  - `GET /dvb/archive/search?q=<text>&page=<n>` (text ≤ 100 chars) → `{"cards": [...], "nextPage"}`: ČT
    `searchShows` + our recordings whose title contains the text (no diacritics, case-insensitive), merged as above.
  - `GET /dvb/archive/show/<id>` (`ct:<ČT show id>` or `rec:<normalised title>`; the colon plain or as `%3A`) →
    `{"card": card, "description", "creators"? (one display string as ČT sends it, "Režie: … Hrají: …"),
    "countries"? [names], "seasons": [{"id","title"}], "groups": [{"title", "episodes": [episode…]}],
    "attribution"?, "error"?}`. The show page's `card` always has the show's own `kind` and `year` (from ČT's
    `showType`/`year`), also when the card in a list came from a search without them.
  - **Season page** `GET /dvb/archive/show/<id>?season=<id>&page=<n>` answers the same show object; its ČT group
    (`groups[0]`) holds that season's page `n` and `nextPage` (`null` = no more); "Nahráno z antény" stays as it
    is. The apps replace the ČT group's episodes on page 1 and append them on later pages. Sample:
    `spec/fixtures/dvb/dvb_archive_show_season_page.json`.
  - **card** `{"id", "source": "ct"|"rec"|"both", "title", "kind": "movie"|"series"|null, "year"?, "image"
    (ČT image URL, or a gateway path `/dvb/details/image/…` / thumbnail for recordings) | null, "rating":
    {"value","votes"} | null, "genres": [..], "episodeCount"?, "durationMinutes"?, "tmdb": bool}`; **episode**
    `{"source": "ct"|"rec", "idec" | "recordingId", "title", "description", "durationSeconds", "aired": "…Z" | null,
    "channel", "channelId", "playable": bool}`. `channel` is the display name; `channelId` the CT API id (`CH_1`,
    `CH_2`, `CH_24`, `CH_4`, `CH_5`, `CH_6`) when ČT's `channelName` equals a name in `channels.py` (spaces
    normalised, nothing else guessed), else `null` (always `null` for our recordings: they play from the Anténa
    recording); the apps use it for the 3-hour buffer rule. `tmdb` = the card shows TMDb data (rating, TMDb poster or TMDb genres): the apps
    show the TMDB logo and attribution then; every `/dvb/archive*` answer with such a card has `"attribution"`
    (TMDb's sentence, as in `/dvb/details`). `kind` is null for a ČT search card (ČT's `searchShows` does not say;
    merged with our recording it takes ours). Keys marked `?` are left out when unknown.
  - Home, category and search answer `200` with `"error": "iVysílání teď neodpovídá"` added when ČT failed (our
    recordings are still listed); a ČT show page answers `502`, except a `both` show the archive listed before
    (its last card kept in memory): `200` with that card, `"seasons": []`, only "Nahráno z antény" and the
    `error`. On the show page the ČT group is `{"title" (season
    title, or "Díly"), "season": id | null, "episodes", "nextPage": n | null}`, "Nahráno z antény" `{"title",
    "episodes"}` (all our episodes, newest first).
- **`/admin`, tab "TMDb"**: the store, read-only and paginated (title, match, rating, genres, year, fetched), refresh
  / delete per entry, "Smazat vše" (all logged: `admin: tmdb: <action> <title>`), the N-months setting with its
  warning. No download or export.
- **Details of the implementation** (T13):
  - **ČT GraphQL shapes** (introspection is off; found 2026-10-25 with 36 manual requests, our User-Agent; the
    error messages are masked, so an unknown field shows only as `GRAPHQL_VALIDATION_FAILED`):
    rows `showFindByGenre(categoryId, limit, offset, orderBy: popular_previous_seven_days | catchup_broadcast |
    alphabet[, order: asc]) { totalCount items { id title year showType playableEpisodeCount durationText images
    { card } flatGenres { id title } } }` (a sub-genre id works as `categoryId`; "newest" = `catchup_broadcast`,
    ČT's "Naposledy vysílané"; `alphabet` alone is Z–A); sub-genres `category(categoryId) { categoryBlocks { title
    categoryId } }` (the blocks with a `categoryId`); search `searchShows(keyword, limit, offset) { items { id title
    images { card } } }` (a `ShowCard`: no `showType`, `year`, `flatGenres`; `totalCount` not checked); show
    `programme(legacySidpOrIdp: id) { ... on Show { … description shortDescription creators (a string)
    countriesOfOrigin { title } seasons { id title } } }` (unknown id → error code `NOT_FOUND`) and its episodes
    `episodes(limit, offset, onlyPlayable: true, orderBy: oldest | newest[, seasonId]) { totalCount items { id title
    description duration playable date { datetime channelName } } }`. `year` is a string; `durationText` "98 min"
    with a no-break space. Home rows use `showFindByGenre` with the popular sort instead of the category's
    `categoryBlocks` cards (their `CategoryBlockCard` fields are not known). Variables carry ids and the search
    text; page numbers, sorts and the season id (checked against the show's seasons) are written into the query.
  - Pages: 20 ČT shows, pages 1–3 (`400` beyond); `recordings` 20 per page, pages 1–50; a show's episodes 20 per
    page, pages 1–10. A series with seasons lists the first season unless `season` is given, oldest first; without
    seasons newest first. Inputs: `q` 1–100 characters after collapsing spaces, no control characters (`400`);
    `slug` `recordings` or one of the 14 (`404`); `sort` `popular|newest|az`; `genre` digits and one of the
    category's sub-genres (`400`; none for `recordings`); show id `ct:[A-Za-z0-9]{1,32}` or `rec:<letters and
    digits>` (`400`), unknown `404`.
  - Our shows mixed into ČT's: a merged card where the key matches, the rest of ours into every third place (A–Z:
    by title), up to 10 per page; in a sub-genre none of ours. "Naše nahrávky" in the home row: the newest 20.
    "Completed" is `/dvb/epg`'s state: at least 30 s of the programme recorded, so a partly recorded programme is
    listed. Programmes that started before the retention window are left out. A card of ours is a film only with
    one recording and a TMDb film match, or without a match a "Film / drama" programme without an episode number.
    Its image is the newest recording's thumbnail (thumbnails on), else the TMDb poster. Card images are only an
    `https` URL on a `*.ceskatelevize.cz` host (anything else from ČT becomes `null`), `/dvb/details/image/w342/…`
    or `/dvb/recording/<id>/thumb.jpg`. The samples use the invented host `img.example.invalid`.
  - `rec:` ids are ASCII: the normalised title, with any letter `normalise` keeps but is not ASCII ("ł", "ø",
    Cyrillic) written as `u` + 4 hex digits (`łodz` → `u0142odz`); the route accepts `rec:[a-z0-9]{1,600}`.
  - Store: a "month" is 30 days (N = 6 → 180 days, inside TMDb's 6 months); the worker refreshes an entry still
    listed from N months − 7 days (− N/4 for short periods) and it is deleted at N months; N up to 999 999 999.
    "Listed" = our shows within retention, and ČT shows in any archive answer of the last 7 days (memory only).
    Worker: every 5 min (sooner after an answer listed new titles); one TMDb request at a time, at most 20 per
    minute for the worker (the other 10 for pages), never while a page lookup waits; a refusal ends the round (next
    try in 20 s), a failed title waits an hour. TMDb `429`: no request for `Retry-After` seconds (number form;
    otherwise 60 s, at most 1 h), also for pages; never retried by itself.
  - `/admin`: `GET /admin/api/tmdb?page=n` (50 per page, by title), `POST /admin/api/tmdb` `{"months": N}` |
    `{"refresh": key}` (the stored cleaned title only) | `{"delete": key}` | `{"deleteAll": true}`; logged `admin:
    tmdb: refresh after N month(s)`, `… refresh <title>: match | no match | failed (…)`, `… delete <title>`, `…
    delete all (n entries)`. A shorter N deletes older entries at once. Without `[tmdb] key` the tab says TMDb is
    off; the months setting can still be changed.
- legal-guard 2026-10-25: approve with changes (all in this section); approved by the owner the same day.

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
2. airing → choice "Živě" (`/dvb/live/…`) or "Od začátku" (if `recording.state == recording`; otherwise only Živě).
3. ended with `recording.state == completed` → play the recording.
   A `b…` recording (continuous recording) plays in the channel timeline (`/dvb/timeline/<id>`, `spec/apps.md`
   "Anténa timeline player"); a TVHeadend DVR recording (uuid) plays `/dvb/recording/<id>.m3u8`.
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
