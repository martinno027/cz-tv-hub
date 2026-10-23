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
- **Recordings**: one TVHeadend autorec rule per configured channel records every EPG event to the Pi's local disk.
  Retention = `retention_days` (default 3, clamped to 1..14), enforced by TVHeadend's DVR file removal **and** by the
  gateway's own cleanup loop (deletes finished recordings older than retention through the TVHeadend API).
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
  - TVHeadend forgets EPG events once they end, so past programmes come from DVR entries: their `id` is
    `dvr:<dvr entry uuid>` and `recording` is always set. Treat `id` as an opaque string.
  - `genre` is a Czech label from the DVB content nibble ("Zprávy", "Film / drama", …) or `""`.
  - State mapping from TVHeadend: `recording*` → recording, `scheduled` → scheduled, `completed` → completed,
    everything else (`completedError`, `completedRerecord`, `completedWarning` = missed) → failed.
- `GET /dvb/live/<channel id>.m3u8` → live HLS (fMP4, rolling 20 segments of ~2 s, no `EXT-X-PLAYLIST-TYPE`; short
  segments so the first one, which the first request blocks for, is ready sooner: tuning + ~2 s). Recordings use ~4 s. Media files under
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

## A/V sync of the remuxed HLS (`src/cztvhub/dvb/fmp4.py`)
DVB muxes carry audio and video with different start times, and a viewer joins mid-stream (video starts at the next
keyframe). ffmpeg's fMP4 writer starts every track's fragments at 0 and records the real offsets only in the init
segment's edit lists, which HLS players ignore: the sound was up to ~0.7 s off (found 2026-10-23 on Apple TV). The
gateway serves the init segment with identity edit lists and adds each track's `empty edit − first media time`
(plus a common non-negative base) to every fragment's `tfdt`, in place (sizes unchanged, Range still fine).
`tests/test_dvb_avsync.py` checks a flash + beep test stream for joins in both directions and mid-GOP
(within 60 ms, edit lists ignored).

## Thumbnails (approved by the user on 2026-10-23; see `docs/legal-notes.md`)
One still per recording so the viewer sees what a programme is ("it does not have to be current, just show what is
on"). Implemented in `src/cztvhub/dvb/thumbs.py`.
- `GET /dvb/recording/<dvr entry uuid>/thumb.jpg` (same key as all `/dvb/*`) → `200 image/jpeg`,
  `Cache-Control: private, no-store`. `404` when thumbnails are off, the entry is unknown, not `completed`/`recording`,
  its channel is not offered, or no frame could be made yet.
- Source: the recording only (`/dvrfile` byte range, at most 2 MB), never a live tuner subscription, never ČT internet
  streams. Frame position: `t = min(300 s, 20 % of the recorded duration)` after the start, as a byte offset from
  the file size per recorded second. ffmpeg reads stdin and writes one JPEG to stdout (`-frames:v 1`, width ≤ 480,
  fixed quality); no file outputs. No client parameters (no seek, no series).
- Cache: memory only, at most 200 entries; an entry is kept for the life of the recording once `t` reached 300 s or
  the recording finished, else refreshed after 60 s. Every request checks the DVR entry first (cache hit included);
  a gone entry drops its cache entry. Switching thumbnails off clears the cache. At most 2 ffmpeg at a time,
  10 s timeout.
- Household switch: on the `/admin` page ("Náhledy pořadů", default on), stored in `<state dir>/settings.json`
  (`/var/lib/cztvhub`, the service's StateDirectory). `GET /dvb/status` adds `"thumbnails": true|false`.
- Apps: the guide detail of an Anténa programme with `recording.state` `completed`/`recording` shows the thumbnail
  when the gateway reports `thumbnails: true` and the TV's own switch (Nastavení → Server, "Náhledy anténních
  pořadů", default on) is on; otherwise the antenna placeholder. Images are kept in memory only (no disk cache).

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
- MUX 21 carries all CT channels; commercial channels are in other multiplexes. One tuner = one multiplex at a time, so
  recording every channel needs one tuner per multiplex (+1 for live viewing of another multiplex).
- HEVC HD ≈ 2–4 Mbit/s per channel ≈ 1–1.8 GB/h. 3 days ≈ 70–130 GB per channel → USB SSD/HDD on the Pi
  (e.g. 2 TB for ~15 channels). `record_channels` lets you limit it.
