# CT TV apps: shared specification

Native TV apps that replace Kodi for the family: **Apple TV** (`apps/tvos`, Swift/SwiftUI/AVPlayer),
**Android TV** (`apps/androidtv`, Kotlin/Compose for TV/Media3 ExoPlayer) and **Samsung TV** (`apps/tizen`,
Tizen 4.0+ web app, plain ES2017 JavaScript/AVPlay). All follow this document and the
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
   2 days ahead; day picker (Dnes, Včera, Zítra, `po 12. 10.` …). Opens scrolled to now. D-pad/Siri Remote focus
   navigation moves cell to cell; focused cell shows detail (title, episode, time, genre, description, image).
   Select a cell → action from **Decision rules**. Past cells clearly playable, unavailable ones dimmed.
   **Anténa thumbnails**: the detail's image of an Anténa programme is the gateway thumbnail
   (`spec/dvb-gateway.md` "Thumbnails") when the gateway reports `thumbnails: true` and the TV's switch "Náhledy
   anténních pořadů" (Nastavení → Server, default on) is on, else the antenna placeholder. Load it with the key
   (header; Samsung: fetch with the header into a blob URL), memory cache only, never a disk cache; request it only
   for the focused programme (after the focus rests ~0.3 s), never for every cell.
   **Across days**: the guide scrolls on past a day's edge. Right on the last cell of a row (the one reaching the
   broadcast day's end, 05:00) opens the next day (if it is in the range) with focus on that row's cell at the new
   day's start; left on the first cell opens the previous day with focus on that row's cell at its end. The day strip
   always highlights the day the grid shows (so "Dnes" is no longer highlighted after scrolling into "Zítra"); the
   "Live column first" mode ends on a day change. At the range's ends nothing happens. tvOS has no event for a
   left/right press without a target, so its rows end with a focusable "Zítra ›" cell and start with "‹ Včera" /
   "‹ Dnes" (none at the range ends); Android TV and Samsung react to the key at the row's edge directly. The grid
   covers exactly the broadcast day 05:00–05:00 Prague (programmes crossing 05:00 are clipped).
   **Performance**: with dozens of Anténa channels a day has thousands of cells. Never build them all: build only a
   window around the focus (about 5 rows above, 10 below, 3 h before and 5 h after the focused cell; empty space of
   the same size elsewhere so scroll positions stay exact) and move it when focus gets within 3 rows / 2 h of its
   edge. Compute rows, time axis and cell geometry once per data change, not per focus move; a focus move must not
   re-render the grid (only the detail). Left/right must never jump to another row because the next cell was not built.
   Target: smooth D-pad on the device with 60 channels (the fake TVHeadend: `cztvhub dvb-fake --channels 60`).
   **Live column first**: on today's guide, until the viewer presses left or right, up/down moves to the programme
   **airing now** in the next row (like zapping), whatever the cells' widths; a row with nothing airing gets the
   cell (or focusable "Mimo vysílání" gap) under the now line. The first left/right ends this mode until the guide is
   opened or today's day is loaded again; other days use normal grid navigation.
3. **Player**: full-screen, native controls. Live: seek within the timeshift buffer when started from it.
   Archive: normal VOD seeking. Subtitles/audio tracks from the HLS manifest via native UI if cheap.
   On playback error caused by URL expiry, re-resolve once and resume at the same position.
   Live (no start position) starts playing as soon as the player can, without waiting for a ready state first.
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

## "Pokračovat" (resume where the viewer stopped)
Each TV remembers, **on the device only**, how far it got in a programme, and offers to continue there. Nothing leaves
the device (no account, no sync, no analytics); only positions are stored, never stream URLs or video.
- **Key**: CT programme `ct:<idec>`; "Anténa" recording `dvb:<recording id>`. Live (`Živě`) is never remembered.
- **Progress** = seconds from the programme's start: archive (VOD) → player position; "Zpět v čase" (timeshift) →
  wall-clock of the shown frame − programme start (on players without wall-clock, position − (planned at − W) style
  estimate); Anténa recording (finished or growing) → player position.
- **Saving**: every 10 s while playing and when the player closes or the app goes to the background.
- **Rules** (`resume_cases.json`): `programme length` = end − start. Offer when `progress ≥ 60 s` and
  `progress < length − 120 s`. When `progress ≥ length − 120 s` the programme counts as watched and the entry is
  removed. Below 60 s nothing is stored and an existing position stays (pressing "Od začátku" by mistake does
  not lose it); a programme shorter than 3 min is never offered. At most 200 entries, entries older than 30 days are dropped.
- **Selecting a programme with an offer** shows a choice with **"Pokračovat od 23:10"** focused first:
  airing → `Pokračovat od …` / `Od začátku` / `Živě`; ended (CT archive, buffer or Anténa recording) →
  `Pokračovat od …` / `Od začátku`. Without an offer the behaviour is unchanged. Label format `m:ss` below an hour,
  else `h:mm:ss`.
- **Playing a resume**: the decision rules decide the source as usual (by time); then CT timeshift seeks by wall clock
  to `start + progress` (fallback offset `max(0, (start + progress) − (now − W))`), CT archive and Anténa recordings
  start at `progress`. A CT programme that began < 3 h ago is entirely in the buffer, so the resume point is too.

## "Kanály" (hide and reorder channels on this TV)
In **Nastavení → Kanály** the viewer hides channels and changes their order, **on this device only** (like
"Pokračovat": no sync, no account; another TV keeps its own). Applies to **Živě** and the **TV průvodce** alike.
Separate from the gateway's `/admin` page: a channel switched off there is not received at all; here a channel is just
not shown on this TV.
- **Keys**: CT internet channel `ct:<API id>` (`ct:CH_1`); Anténa channel `dvb:<gateway channel id>`.
- **Stored**: `{"order": [keys], "hidden": [keys], "sourcesOff": ["ct" | "dvb"]}` (missing `sourcesOff` = both on; tvOS `UserDefaults` key `channelPrefs`, Android
  `SharedPreferences` `channel_prefs`, Samsung `localStorage` `cztvhub.channelPrefs`; JSON, at most 500 keys each).
  Unknown keys are kept (an Anténa channel switched off in `/admin` keeps its place when it comes back).
- **Sources**: the whole **Internetová TV** (`ct`, the six CT channels) and the whole **Anténa** (`dvb`, the gateway's
  channels) can each be switched off. A source that is off contributes no channels anywhere (Živě, guide, the
  settings order list); its channels keep their stored order/hidden state for when it is switched on again. Anténa
  without a configured gateway has no channels anyway (its switch is not shown).
- **One mixed list** (no "Internet ČT" / "Anténa" groups any more): the channels of the sources that are on, in default
  order CT first (catalogue order), then Anténa (gateway order). The viewer may put Anténa channels between CT ones.
- **arrange(channels in default order, prefs)** → `all` = keys of `order` that are in the list (first occurrence),
  then the list's other channels in default order (a new channel is appended at the end); `visible` = `all` without
  `hidden`. Živě and the guide show `visible`; the settings list shows `all`.
- **move(key, by ±1)** swaps the channel with its neighbour in `all` (no-op at the edge); the new `order` = `all`
  followed by the stored keys not in the list (e.g. the channels of a source that is off). Hiding/showing does not
  change the order. **Obnovit výchozí** clears `order` and `hidden` (and switches both sources on).
- **Source icon**: every channel shows where it comes from, an internet/globe icon for CT internet channels and an
  antenna icon for Anténa (SF Symbols `globe` / `antenna.radiowaves.left.and.right`, Material `Language` / `SettingsInputAntenna`
  or similar, inline SVG on Samsung): in the settings rows (required), on the Živě cards and next to the channel name
  in the guide (small, so "ČT1" and "ČT1 HD" can be told apart).
- **Version line**: the top of Nastavení (the choice screen, always visible there) shows
  "Verze <version> (build <n> · <commit> · <d. m. HH:MM>)": app version, build number = number of git commits
  (`git rev-list --count HEAD`), short commit hash (`+` appended when built from uncommitted changes) and the build
  time (Prague), all stamped at build time (tvOS build phase into Info.plist, Gradle `BuildConfig`, Samsung
  `build.py` into a generated script; the Samsung browser demo shows "vývojová verze"). Nothing is fetched at run time.
- **Nastavení is a choice of two sections**: the Nastavení screen shows only two large items, **Kanály** (first) and
  **Server**, each with a one-line description. Selecting one opens that section **full screen** (no tab bar row, no
  other section): Kanály = this list; Server = the Anténa gateway (address, key, "Uložit a ověřit", "Odebrat bránu",
  discovery in the local network; "O aplikaci" at its end). The remote's **Back** (Siri Remote Menu/‹, Android
  Back, Samsung Return) returns to the choice, with focus on the item just left; Back on the choice leaves Nastavení
  as before.
- **Settings UI (Kanály)**: first the two source switches `[☑ Internetová TV (ČT)]` and `[☑ Anténa]` (the latter only
  with a gateway), then one list "Pořadí kanálů" of rows `[icon ☑ ČT1] [▲] [▼]` (left/right moves between the three
  controls; select on the name toggles "zobrazovat", hidden rows dimmed, ▲ on the first / ▼ on the last row
  disabled), then "Obnovit výchozí pořadí". Anténa channels need the gateway (unreachable → Czech message above the
  list, the CT channels still listed). Focus stays on the moved row's button after ▲/▼ and the list scrolls so the
  moved row stays visible.
- When no channel is left (all hidden or both sources off), Živě and the guide say
  "Všechny kanály jsou skryté. Zobrazíte je v Nastavení → Kanály."
- Golden cases: `spec/fixtures/channel_prefs_cases.json` (`arrange` and `move` on mixed lists; the input list is
  already filtered by the sources that are on), tested in all three apps.

## Quality bar
- Unit tests for API parsing (all fixtures) and decision rules (all golden cases); no network in tests.
- Builds from the command line (`xcodebuild` / `./gradlew` / `apps/tizen/build.py`), runs in the tvOS simulator /
  Android TV emulator / a desktop browser (`index.html?demo`, fixtures only) for the Samsung app.
- Samsung (AVPlay) has no wall-clock seek: start-over uses the computed buffer offset only.
- Small, readable modules: `api` (client + models), `domain` (decisions, day labels), `ui` (screens), `player`.
- Remote-friendly focus everywhere; large readable type (10-foot UI); dark theme.
