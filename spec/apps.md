# CT TV apps: shared specification

Native TV apps that replace Kodi for the family: **Apple TV** (`apps/tvos`, Swift/SwiftUI/AVPlayer) and
**Android TV** (`apps/androidtv`, Kotlin/Compose for TV/Media3 ExoPlayer). Both follow this document and the
behaviour of the verified Python reference (`src/cztvhub/api.py`, `plugin.py`, `guide.py`).

## Hard rules (from CLAUDE.md, non-negotiable)
- Only Ceska televize's own official streams, through the endpoints below. No other sources.
- Resolve every stream **on the device** right before playback (URLs are IP-bound, expire ~1 h). Never persist
  signed URLs; an in-memory reuse of at most 60 s is fine.
- Never circumvent DRM, geo-blocking or token/IP checks (never touch `skipIpAddressCheck`, never request DRM).
  Show CT's own message instead.
- Play only: no download, recording or offline caching of video.
- No analytics, ads SDKs or third-party tracking. No accounts.
- UI text in **Czech**. Code and comments in English.

## Scope v1
1. **Živě**: row/grid of the 6 channels with the programme on air now (title, time, progress bar).
   Select → full-screen live playback.
2. **TV průvodce** (the main screen feature): classic EPG grid, channels as rows, time on the horizontal axis
   (e.g. 30-min ticks, ~2 h visible), a "now" line, programme cells sized by duration. Range: 7 days back,
   2 days ahead; day picker (Dnes, Včera, `po 12. 10.` …). Opens scrolled to now. D-pad/Siri Remote focus
   navigation moves cell to cell; focused cell shows detail (title, episode, time, genre, description, image).
   Select a cell → action from **Decision rules**. Past cells clearly playable, unavailable ones dimmed.
3. **Player**: full-screen, native controls. Live: seek within the timeshift buffer when started from it.
   Archive: normal VOD seeking. Subtitles/audio tracks from the HLS manifest via native UI if cheap.
   On playback error caused by URL expiry, re-resolve once and resume at the same position.
4. **Errors**: show CT's `labels.textShort` (Czech) when present, else a generic Czech message.
Out of scope v1: search, show/episode browsing, favourites, settings, DVB-T2 (TVHeadend comes later; keep the
channel source abstract so a second source can be added).

Channels (order, API id, GraphQL slug, name):
`CH_1 ct1 ČT1`, `CH_2 ct2 ČT2`, `CH_24 ct24 ČT24`, `CH_4 sport ČT sport`, `CH_5 decko ČT :D`, `CH_6 art ČT art`.

## CT API contract
Headers: `User-Agent: Mozilla/5.0 (compatible; ct-tv-app/0.1)`, `Accept: application/json`.
Common query: `canPlayDrm=false&quality=web&streamType=hls&origin=ivysilaniweb&usePlayability=true&clientVersion=0.0.0`.
Parse defensively: the API is undocumented; unknown/missing fields must not crash.

**Live** `GET https://api.ceskatelevize.cz/video/v1/playlist-live/v1/stream-data/channel/{CH}?{common}`
→ `streamUrls.main` (live HLS), `streamUrls.timeshift` (3 h rewind buffer, optional), `streamUrls.isBlocked`.
`isBlocked == true` or no `main` → error "Vysílání je blokováno" / generic. Fixtures: `live_CH_1.json`, `live_blocked.json`.

**Archive (VOD)** `GET https://api.ceskatelevize.cz/video/v1/playlist-vod/v1/stream-data/media/external/{IDEC}?{common}`
- 200 with `streams[].url` → first non-empty url (HLS master). Fixture `vod_ok.json`.
- 200 with only `platformChannel` → item is airing now ("still live") → fall back to start-over on that channel.
  Fixture `vod_still_live.json`.
- 4xx with `message.status.name` + `message.labels.textShort` → error with that Czech text
  (`MISSING_LICENCE_NOW` → "ČT nemá práva pro internet", `UNSUPPORTED_DRM`, `OTHER`). Fixtures `vod_no_licence.json`,
  `vod_not_found.json`, `vod_drm.json` (StarDance, `UNSUPPORTED_DRM`). Non-JSON error body → "Video není k dispozici".

**Schedule** `POST https://api.ceskatelevize.cz/graphql/?client=iVysilaniWeb&version=1.131.1&use-new-playability=true`
body `{"query": Q, "variables": {"channels": ["ct1", …], "date": "MM.DD.YYYY"}}` with
```graphql
query($channels: [String!]!, $date: Date!) {
  TVProgramDailyChannelsPlanV2(channels: $channels, date: $date) {
    channel
    program { idec title episodeTitle description genre imageUrl start end isPlayableNow liveOnly }
  }
}
```
`start`/`end` are ISO-8601 UTC (`2026-10-17T05:00:00.000Z`). One request returns all channels for one broadcast
day; skip malformed entries; `errors` without `data` → error "Program se nepodařilo načíst". A failing day
(future days may be unpublished) must not break the other days. Fixtures `schedule_ct1_ct24.json`, `graphql_error.json`.
Cache schedules in memory per day; refetch today's after ~1 h. Polite: no more than ~10 requests per screen open.

Fixture files wrap responses as `{"status": <http status>, "body": <json>}`; tokens are replaced by `TOKEN`.

## Decision rules (selecting a programme)
`W = 3 h` (timeshift window), `M = 30 s` (start margin), `now` = current UTC.
1. `now < start` → **future**: not playable, show "Pořad ještě nezačal".
2. `start ≤ now < end` → **startover** (airing; also offer "Živě" for the live edge).
3. ended and `now − start < W` → **startover**, regardless of `isPlayableNow`: a programme that began less than
   3 h ago always plays from CT's "Zpět v čase" buffer. Decide **by time only**, never by a VOD error.
4. ended, `now − start ≥ W`, `isPlayableNow` → **vod** (resolve by IDEC; on still-live fall back to startover).
5. otherwise → **vod_unavailable**: dim the cell but still call VOD on select, so CT's reason is shown.

Note: for DRM-only programmes (e.g. StarDance) CT replaces the picture in the non-DRM live and timeshift streams
with a slate ("Pořad nelze přehrát v tomto prohlížeči…"); ads around it play normally. The playlist gives no
signal, so the app cannot detect it in advance. This is expected; never work around it.

When VOD fails with `UNSUPPORTED_DRM`, show CT's text and add: "Pořad můžete pustit v oficiální aplikaci
iVysílání." Never request DRM, never retry such an item through another stream. UI calls the buffer only
"Zpět v čase" (never "bez DRM" or similar).

**Start-over**: resolve live, take `timeshift` (missing → "Zpět v čase není pro tento kanál k dispozici"), start
playback at `offset = max(0, (start − M) − (now − W))` seconds from the beginning of the buffer. On AVPlayer/ExoPlayer
prefer seeking by wall-clock using `#EXT-X-PROGRAM-DATE-TIME` (AVPlayerItem `seek(to: Date)`, ExoPlayer
`Timeline.Window.windowStartTimeMs`) to `start − M`; fall back to the computed offset.
Golden cases: `spec/fixtures/decisions.json` (unit-test both the action and the offset).

## Quality bar
- Unit tests for API parsing (all fixtures) and decision rules (all golden cases); no network in tests.
- Builds from the command line (`xcodebuild` / `./gradlew`), runs in the tvOS simulator / Android TV emulator.
- Small, readable modules: `api` (client + models), `domain` (decisions, day labels), `ui` (screens), `player`.
- Remote-friendly focus everywhere; large readable type (10-foot UI); dark theme.
