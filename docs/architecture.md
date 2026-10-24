# Architecture

CT's signed CDN URLs carry `skipIpAddressCheck=false`: they only play from the public IP that
requested them. That decides where resolution has to happen.

## Kodi add-on (any network inside CZ, live + start-over + archive)

```
Kodi device ──GET api.ceskatelevize.cz (live / vod / graphql)──▶ CT API
            ◀──signed HLS URL (bound to the device's IP)──
Kodi device ──────────────────HLS─────────────────────▶ CT CDN (o2tv.cz)
```

- `kodi-addon/plugin.video.cztvhub/main.py`: thin Kodi adapter (list items, playback properties).
- `src/cztvhub/plugin.py`: routes and decisions, no Kodi imports, unit-tested.
- `src/cztvhub/guide.py`: M3U + XMLTV for the TV guide.
- `src/cztvhub/api.py`: CT API client, shared with the server. `kodi-addon/build.py` bundles
  `api.py`, `channels.py`, `guide.py`, `plugin.py` into the add-on ZIP, so it must stay Python 3.8 compatible.
- No Pi involved: each family device talks to CT itself, so any number of devices work at once.

CT endpoints used:
- live: `video/v1/playlist-live/v1/stream-data/channel/<CH>` → `streamUrls.main`, `streamUrls.timeshift`
  (3 h rewind buffer, "Zpet v case").
- archive: `video/v1/playlist-vod/v1/stream-data/media/external/<IDEC>` → `streams[0].url`;
  403 with `message.labels.textShort` when not playable (`MISSING_LICENCE_NOW`, `UNSUPPORTED_DRM`);
  200 with only `platformChannel` when the item is airing now.
- schedule: GraphQL `TVProgramDailyChannelsPlanV2(channels: ["ct1"], date: "MM.DD.YYYY")` → `idec`,
  `start`, `end`, `isPlayableNow`.

TV guide (Kodi's EPG grid): `kodi-addon/plugin.video.cztvhub/service.py` runs in Kodi and every 6 h writes
`cztvhub.m3u` + `cztvhub-epg.xml` (XMLTV, 7 days back, 2 ahead, one GraphQL request per day) into the add-on's
profile folder (`src/cztvhub/guide.py`). PVR IPTV Simple Client reads them as local files. Stream URLs are
`plugin://plugin.video.cztvhub/play/live/<CH>`; catch-up uses `catchup="default"` with
`catchup-source="plugin://…/play/catchup/<CH>/{utc}/{utcend}?idec={catchup-id}"`, and each XMLTV programme
carries `catchup-id` = IDEC. So the guide also resolves on the device and works outside the home network.

Start-over: play `timeshift` with `inputstream.adaptive.play_timeshift_buffer` (opens at the oldest point)
and seek with `StartOffset` = programme start − (now − 3 h) − 30 s.

## Pi server (home LAN only, live, PVR IPTV Simple)

```
Kodi / future app ──GET /ct/CH_1.m3u8──▶ cz-tv-hub (Raspberry)
                                            │ GET api.ceskatelevize.cz/.../channel/CH_1
                                            ▼
                       ◀──302 Location: signed HLS URL──
Kodi / future app ──────────HLS────────▶ CT CDN (o2tv.cz)
```

- Works only when the client shares the Pi's public IP (same home network).
- Stateless apart from a 60 s in-memory cache of signed URLs. Video never touches the Pi.
- Endpoints: `/playlist.m3u` (alias `/ct.m3u`), `/ct/<CH_id>.m3u8`, `/health`.
- Errors: `404` unknown channel, `502` CT API failed or channel blocked.

## DVB-T2 gateway (Pi, LAN + household tailnet, live + recordings)

```
antenna ─▶ tuner ─▶ TVHeadend (127.0.0.1:9981): scan, EPG, autorec of every programme, local disk
                        │ JSON API, /stream/channel (MPEG-TS), /dvrfile (Range)
                        ▼
              cz-tv-hub serve --config … : /dvb/* (bearer key)
                - JSON: status, channels, EPG (events + DVR entries, Prague broadcast day)
                - HLS: Python reads the TS → ffmpeg -c:v copy (hvc1), audio → AAC, fMP4 segments
                       in hls_dir/<session>/, deleted idle_seconds after the last request
                - retention loop: deletes finished recordings older than retention_days
                        ▼
              apps ("Anténa" group)
```

- Modules: `config.py` (INI), `dvb/tvheadend.py` (client + parsing), `dvb/epg.py` (broadcast day, JSON mapping),
  `dvb/hls.py` (ffmpeg command, sessions, idle reaper), `dvb/sources.py` (following a growing recording),
  `dvb/gateway.py` (routes), `dvb/retention.py`, `dvb/setup.py` (`dvb-setup`), `dvb/fake.py` (`dvb-fake`).
- Unlike the CT path, video does flow through the Pi here: it is the household's own free-to-air reception,
  remuxed on demand, never kept beyond the viewing session (recordings themselves live in TVHeadend).
- The gateway never passes TVHeadend credentials to ffmpeg: Python opens the TVHeadend stream (so a busy tuner is
  detected before ffmpeg starts) and pipes it into ffmpeg's stdin.
- API contract for the apps: `spec/dvb-gateway.md`. Setup for the user (Czech): `docs/dvb-setup.md`.

## Chamber of Deputies live streams (TV apps only, live only)

```
TV app ──GET pspcr-ott-live.ssl.cdn.cra.cz/channels/ps-streamN/playlist/cze.m3u8──▶ Chamber's CDN
       ◀──HLS playlist (200 while broadcasting, 404 "not processed now" otherwise)──
```

- Five fixed public URLs (catalogue in `spec/fixtures/psp_cases.json`), played by the app's own player exactly as
  published. No Pi, no gateway, no Kodi add-on, no playlist export: there is nothing to resolve, and no household
  server handles the video, so nothing of ours can record or re-serve it (approved scope: play only, as offered).
- "Is it on now?" is a GET of the same playlist, only while Živě or the guide is visible, at most once a minute per
  stream; segments are never fetched. Rules and golden cases: `spec/apps.md` "Poslanecká sněmovna".
- Legal basis and limits: `docs/legal-notes.md`.

## Extension points
- `channels.py`: add channels (see skill `add-channel`), including the GraphQL `slug`.
- Future: TVHeadend channels in the IPTV Simple playlist for Kodi.
