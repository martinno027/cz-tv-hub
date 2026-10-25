# CT TV apps: shared specification

Native TV apps that replace Kodi for the family: **Apple TV** (`apps/tvos`, Swift/SwiftUI/AVPlayer),
**Android TV** (`apps/androidtv`, Kotlin/Compose for TV/Media3 ExoPlayer) and **Samsung TV** (`apps/tizen`,
Tizen 4.0+ web app, plain ES2017 JavaScript/AVPlay). All follow this document and the
behaviour of the verified Python reference (`src/cztvhub/api.py`, `plugin.py`, `guide.py`).

## Hard rules (from CLAUDE.md, non-negotiable)
- Only Ceska televize's own official streams, through the endpoints below, the household's own antenna (gateway) and
  the Chamber of Deputies' public live streams ("Poslanecká sněmovna", live only). No other sources.
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
   2 days ahead; day picker (Dnes, Včera, Zítra, `po 12. 10.` …). **Every open of the guide** (app start included,
   also between 00:00 and 05:00) shows **now**: the grid of the broadcast day containing now (`openDay`, golden
   cases `spec/fixtures/guide_days_cases.json`) with focus on the programme airing now in the first row. D-pad/Siri
   Remote focus navigation moves cell to cell; focused cell shows detail (title, episode, time, genre, description, image).
   **Today follows the clock**: "today" for labels and the day strip is the calendar day (Prague) of now, recomputed at
   least every minute while the app runs (after midnight "Dnes" moves on by itself, and a programme airing at 0:10
   says "Dnes 0:10", never "Zítra").
   **Fixed detail layout**: the detail area has a constant height and every element a constant place whatever the
   programme, so nothing moves while the viewer clicks through cells: image (fixed size), title (one line,
   truncated), **episode line always reserved** (empty when there is none), meta line (channel · day time · genre),
   status line (reserved, empty when there is none), description (a fixed number of lines, reserved), the "Otevřít v
   iVysílání" button at a fixed place top right.
   Select a cell → action from **Decision rules**. Past cells clearly playable, unavailable ones dimmed.
   **Anténa thumbnails**: the detail's image of an Anténa programme is the gateway thumbnail
   (`spec/dvb-gateway.md` "Thumbnails") when the gateway reports `thumbnails: true` and the TV's switch "Náhledy
   anténních pořadů" (Nastavení → Server, default on) is on, else the antenna placeholder. Load it with the key
   (header; Samsung: fetch with the header into a blob URL), memory cache only, never a disk cache.
   **Image prefetch** (CT images and Anténa thumbnails alike): when the guide opens and whenever the focus has moved
   more than 1 h or 3 rows from the last prefetch point, queue the images of the programmes in the rows from 3 above to
   8 below the focused row that overlap ±3 h around the focused time, nearest to the focus first (by rows, then by
   time). The focused programme's image goes ahead of the queue at once. Queued requests that left the window are
   dropped (one already running finishes). At most 4 CT image requests and **2 gateway thumbnail requests** at a time
   (the gateway runs at most 2 ffmpeg). In-memory LRU in the app (about 300 images, kept as the downloaded JPEG bytes within a memory budget of at most
   48 MB; decode only what is on screen), never disk. Anténa thumbnails
   only for `completed`/`recording` entries while thumbnails are on; a `404` is not asked again for the guide's
   lifetime (`completed`) or for 60 s (`recording`); a `503` (gateway busy) is not remembered: the item goes back into
   the queue and is retried after `Retry-After` (5 s) if it is still in the window (the focused one always).
   **ČT images for Anténa ČT channels**: an Anténa channel that broadcasts a ČT programme gets the official ČT image
   (and nothing else) from the CT schedule the app already loads, so no recording is needed. Rules (golden cases
   `spec/fixtures/ct_antenna_match.json`, all three apps):
   - **Channel → CT slugs**: normalise the name (casefold, drop diacritics, drop the tokens `hd sd uhd 4k t2 hevc`
     and the regional tokens `pha praha jm sm brno ostrava`, remove spaces): `ct1`→`ct1`, `ct2`→`ct2`,
     `ct24`→`ct24`, `ctsport`→`sport`, `ct:d`/`ctd`→`decko`, `ctart`→`art`, `ct:d/art`/`ctd/art`→`decko`+`art`;
     anything else → none (commercial channels never get CT images).
   - **Programme**: among the CT programmes of those slugs that overlap the Anténa event, take the one whose
     normalised title (casefold, no diacritics, only letters and digits) equals the event's; else the one with the
     largest overlap if it covers at least half of the Anténa event; else none.
   - **Detail image priority**: ČT image (if matched) → gateway thumbnail (own recording) → antenna placeholder. The
     image is fetched like the CT internet images (no key, CT's CDN), memory cache only.
   **Across days**: the guide scrolls on past a day's edge. Right on the last cell of a row (the one reaching the
   broadcast day's end, 05:00) opens the next day (if it is in the range) with focus on that row's cell at the new
   day's start; left on the first cell opens the previous day with focus on that row's cell at its end. **The day strip
   highlights the calendar day of the focused programme's start** (`chipDay`; Hercule Poirot at 22:15 → "Dnes", the
   next cell at 0:10 → "Zítra", inside the same grid), and up from the first row lands on that highlighted chip (never on the "Otevřít v iVysílání" button first;
   up from the strip reaches the button, down from the strip goes to the grid, down from the button to the strip). Coming back from the player is not a new open: the guide stays where it was.
   **Selecting a chip** (`select`): "Dnes" = the grid containing now with focus on now (as on open); any other day =
   that broadcast day, scrolled to 20:00. The "Live column first" mode ends on a day change. At the range's ends nothing happens. tvOS has no event for a
   left/right press without a target, so its rows end with a focusable "Zítra ›" cell and start with "‹ Včera" /
   "‹ Dnes" (none at the range ends); Android TV and Samsung react to the key at the row's edge directly. The grid
   covers exactly the broadcast day 05:00–05:00 Prague (programmes crossing 05:00 are clipped).
   **Performance**: with dozens of Anténa channels a day has thousands of cells. Never build them all: build only a
   window around the focus (about 5 rows above, 10 below, 3 h before and 5 h after the focused cell; empty space of
   the same size elsewhere so scroll positions stay exact) and move it when focus gets within 3 rows / 2 h of its
   edge. Compute rows, time axis and cell geometry once per data change, not per focus move; a focus move must not
   re-render the grid (only the detail). Left/right must never jump to another row because the next cell was not built.
   Target: smooth D-pad on the device with 60 channels (the fake TVHeadend: `cztvhub dvb-fake --channels 60`).
   **Live column first**: on the grid that contains now (`openDay`, not the calendar date: after midnight that is
   the previous broadcast day), up/down moves to the programme **airing now** in the next row (like
   zapping), whatever the cells' widths, **whenever the focused programme is airing now** (also after left/right
   moves) and, when the guide opens, until the first left/right. A row with nothing airing gets the cell (or
   focusable "Mimo vysílání" gap) under the now line. From a programme that is not airing, up/down keeps the time
   as before. Other days use normal grid navigation.
3. **Player**: full-screen, native controls. **Title in the player's info bar**: the programme, not the channel. Live (CT and
   Anténa): title = the programme airing now (from the guide data the app already has), subtitle/second line = the
   channel name (e.g. "ČT1" / "CT 1 PHA HD T2"); it changes by itself when the next programme starts (re-check at
   the programme's end, and at least every minute). Without guide data for now: the channel name as before.
   Start-over, archive and recordings: the programme title, subtitle = channel name (+ episode title if any). Live: seek within the timeshift buffer when started from it.
   Archive: normal VOD seeking. Subtitles/audio tracks from the HLS manifest via native UI if cheap.
   **Audio choice for Anténa** (continuous recording, `spec/dvb-gateway.md`): a programme or live channel with several
   broadcast audio tracks comes as a master playlist with one `#EXT-X-MEDIA TYPE=AUDIO` rendition per track (Czech
   labels such as "Čeština", "Angličtina (Dolby)", "Čeština (pro nevidomé)"). The player starts with the default
   rendition (Czech) and offers the others in its audio menu: AVPlayerViewController's own menu (tvOS), Media3's
   track selection in the player controls (Android TV, preferred language `cs`), an OSD choice on the Samsung
   (AVPlay tracks, AUDIO/MTS or the green key = next audio track, its label shown). A single-track playlist stays as before.
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

## Anténa timeline player (T05)
A recorded Anténa programme of the continuous recording (`b…` id = matching
`^b[A-Za-z0-9_]{1,40}-\d{9,11}-\d{9,11}$`, the gateway's `RECORDING_ID_RE`; never a bare leading `b`: TVHeadend DVR
uuids are hex and can start with `b`) (airing or ended) plays in a **window of its
channel's recording** (`spec/dvb-gateway.md` "Channel timeline": programme ± 3 h, at most 12 h), not as the bare
programme, so the viewer can go before the EPG start (EPG times are often off) and on into the neighbouring
programmes, as far as the recording reaches. TVHeadend DVR recordings (uuid ids), ČT and live keep the native
player as before; "Od začátku" of a growing DVR recording seeks to 0 explicitly (players start a growing playlist
at its live edge; the gateway's `EXT-X-START:TIME-OFFSET=0` is the fallback).
- **Open**: `GET /dvb/timeline/<id>` (JSON), then play its `playlist` (accepted only as a gateway path: starts with
  `/`, not `//`; the key goes with every request). A failed JSON or playlist fetch for `previous`/`next` keeps the
  old window playing and shows the gateway's Czech text in the bar. "Od začátku" starts at programme start
  − 30 s, "Pokračovat" at programme start + progress, both through the map below (the playlist's `EXT-X-START`
  is only the fallback). An ended programme without an offer starts like "Od začátku".
- **Map** position ↔ wall clock through `pieces` (golden `spec/fixtures/timeline_cases.json` `map`): a position on
  a boundary belongs to the later piece, a time in a gap maps to the next piece's start, outside the window clamps.
  Every platform uses this map (also where the player knows `PROGRAM-DATE-TIME`), so all three show the same times.
  A growing window (`complete: false`) re-fetches the JSON every 60 s; beyond its last piece the time runs on with
  the position.
- **Own player bar** instead of the native controls (all three apps): shown at the start and on any key or pause
  for 5 s (stays while paused), bottom of the screen:
  - the second line as now (channel · episode), the **title = the programme at the playhead** (`programmeAt`, from
    the JSON's `programmes`; in a gap the channel name). It changes while playing and while seeking.
  - a bar across the width = the window **on a wall-clock axis** (from the first to the last recorded time; on a
    position axis gaps would have no width): recorded pieces filled, gaps empty, programme boundaries as ticks, the
    programme at the playhead highlighted, the played part up to the playhead.
  - above the playhead its **wall clock**: `Dnes 21:14:30`, `Včera 23:59:59`, `čt 22. 10. 10:05:00` (`labels`;
    the calendar day in Prague compared with now, `H:MM:SS` without a leading zero). Under the bar's ends the
    window's first and last recorded times `H:MM`, with the day label in front only when that day is not the
    playhead's (`edges`). A growing window's right end is "Teď" while its last piece ends within 30 s of now.
  - while a seek is being chosen (held key or swipe) the playhead and its label move, the video seeks on release.
- **Keys** (while the bar is hidden the first key only shows it, except play/pause, Back, the audio keys, and OK
  while "Přeskočit reklamu" is shown):
  - **OK / play-pause** = pause/play. **Back** closes the player as before.
  - **← / →** = −10 s / +10 s (released within 0.5 s); **held** = scrub: offset `60 × held s` up to 3 s, then
    `+600 × s` (`hold`: 5 s held = 23 min); seek on release. Without a key-up event the release counts 0.8 s after the
    last repeat. **⏪ / ⏩** scrub the same way but count at least 1 s (a tap = 60 s). tvOS: clickpad left/right click = the same, a horizontal swipe on the touch surface
    scrubs (about 10 min per full swipe); Android TV `DPAD_LEFT/RIGHT` (+ `MEDIA_REWIND/FAST_FORWARD` as held);
    Samsung ◀/▶ (+ ⏪/⏩).
  - **▲ / ▼** = start of this programme / of the next one (`jump`: ▲ more than 10 s in goes to its start, else to
    the previous programme's start; nothing there = nothing happens). The target goes through the map; when the
    mapped position is within 10 s of the playhead (a start inside a recording gap), ▲ takes the programme before.
    A target outside the window follows the edge rule (`previous`/`next`, else clamp). No zapping in this player.
  - **Audio**: long press OK opens the list of audio renditions (tvOS: long press of the clickpad; Android TV:
    long press `DPAD_CENTER`; Samsung keeps AUDIO/MTS and the green key, plus long OK). The default stays Czech.
  - **Přeskočit reklamu** as before (`spec/ad-breaks.md`): breaks of the programme at the playhead (when it has an
    id, `breaks: true` and has ended, also inside a growing window), their positions (from that programme's playlist start = programme start − 30 s)
    converted to wall clock and then through the map.
- **Edges** (`edge`): a seek target before 0 opens `previous` (if any, else stays at 0); a target at/after the end
  of a complete window, or playback reaching it, opens `next` (else stays). A growing window's end is the live edge
  (stays). The new window is opened with its own JSON and continues at the target's wall clock (mapped in the new
  window); the bar stays on screen across the switch. At most one switch at a time.
- **Pokračovat**: the programme at the playhead (with an id) is the one remembered; progress = wall clock −
  programme start (`resume`; before the start = 0). Positions stored before T05 counted from 30 s before the start;
  they now resume 30 s later (accepted).
- Golden cases: `spec/fixtures/timeline_cases.json` (`map`, `edge`, `hold`, `labels`, `edges`, `programmeAt`,
  `jump`, `resume`), tested in all three apps.

## Programme page (T15)
Selecting a programme in the **TV průvodce** opens a full-screen **page of that programme** instead of the
"Pokračovat / Od začátku / Živě" choice (every programme: ČT and Anténa, ended, airing and future; Poslanecká
sněmovna streams keep playing live at once). **Back** returns to the guide with the same focus.
- **Shows**: the image (TMDb poster when the gateway has a match, else the guide image / gateway thumbnail /
  placeholder as in the guide detail), the title, the episode line, a facts line — channel (with its source icon) ·
  `Dnes 20:15–22:05` (day label as in the guide) · `110 min` · genre · year — then **"Hodnocení TMDB 7,4 (1 234
  hlasů)"** when there is a rating (only with ≥ 20 votes; Czech plural 1 hlas / 2–4 hlasy / else hlasů, one decimal, decimal comma, space as thousands separator), the original
  title when it differs ("Původní název: …"), the full description (the guide's, else TMDb's overview), and the state
  line as in the guide ("Právě běží", "Pořad nebyl nahrán", "Ještě nezačalo" …). With TMDb data the attribution line
  and the TMDB logo at the bottom, small.
- **Buttons** (`spec/fixtures/programme_page_cases.json`, the same decision as the old choice, now a list):
  airing → [`Pokračovat od …`] `Od začátku` `Živě` (`Od začátku` only when the airing programme is playable from
  the start: CT buffer, or an Anténa recording in state `recording`); ended and playable → [`Pokračovat od …`]
  `Od začátku`; an ended ČT programme always offers `Od začátku` (playing shows ČT's own text when it cannot be
  played: licence, DRM — the decision rules); future → none; an Anténa programme not recorded → none, the state line
  says why; `Pokračovat` only together with a playable start; ČT programmes that have started also get **`Otevřít v
  iVysílání`** (Samsung: not offered, as before); every page ends with **`Zpět`**. Focus starts on the first button.
  Facts: duration = broadcast end − start; genre = the guide's, else the first two TMDb genres; year from TMDb only;
  the original title only when it differs from the shown and the TMDb Czech title. The page stays under the player
  and is recomputed (buttons, a new `Pokračovat` focused) when the player closes.
- **Details** are fetched from `GET /dvb/details` (`spec/dvb-gateway.md`) when the page opens (CT programmes too,
  with `source=ct`), only with a configured gateway; the page shows at once and the TMDb lines fill in; `404` or no
  gateway = the page without them. Posters through the gateway (`/dvb/details/image/…`, with the key), memory cache
  only, like thumbnails.
- Golden cases: `spec/fixtures/programme_page_cases.json` (buttons for each state; the rating line incl. plurals),
  all three apps.

## Archiv (T13, draft for the owner's review)
A fourth tab **Archiv** (Živě · TV průvodce · Archiv · Nastavení) on all three apps: iVysílání and our recordings in
shared categories, from the gateway's `/dvb/archive` (`spec/dvb-gateway.md` "Archive"). Without a configured gateway
the tab is empty with "Archiv potřebuje připojení k serveru. Nastavte ho v Nastavení → Server." (owner, 2026-10-25).
- **Home**: a search field on top (the TV's keyboard; results after 0.5 s without typing, at least 2 characters), then
  rows of cards: **"Pokračovat ve sledování"** (below), "Naše nahrávky", then the 14 categories incl. ČT's "Archiv" (Filmy, Seriály, Dokumenty, Zpravodajství, Sport, Pro děti,
  Zábava, Historie, Příroda, …) as the gateway orders them. A card: image (landscape), title, year · "film"/"seriál",
  "★ 7,4" for a TMDb rating (≥ 20 votes; the rating line rules of `programme_page_cases.json`), a source icon (globe
  = iVysílání, antenna = our recording, both icons for `both`). The TMDB logo + attribution sentence small at the
  bottom of the tab whenever a card shows TMDb data. The end of a row (or ›) opens the whole category: sub-genre
  chips and the sort (Nejsledovanější · Naposledy vysílané · A–Z) on top, a grid below, more pages as the focus nears the end.
- **Show page**: the programme page of T15 extended for shows: image, title, facts (source · year · kind · countries
  · episode count), TMDb rating, description, creators; for a film the buttons as T15 (`Přehrát` / `Pokračovat od …`
  / `Zpět`); for a series season chips and the episode list (`1/26 Od začátku · 53 min · ČT1 9. 1.`), each episode
  `Přehrát` or `Pokračovat od …` (resume keys as today: `ct:<idec>`, `dvb:<recording id>`); not playable → dimmed with
  ČT's text when played. The extra group "Nahráno z antény" lists our recorded episodes of the show.
- **Playing**: iVysílání episodes through the existing archive path (`idec` → VOD on the TV; DRM-only → ČT's text);
  our recordings through the Anténa timeline player (T05). Back returns to the page, then to the row.
- **"Pokračovat ve sledování"** (owner, 2026-10-25, v1): the row lists this TV's "Pokračovat" entries
  (`spec/apps.md` "Pokračovat") newest first, at most 20, from both sources and from the guide alike. To show them
  every resume entry saved from T13 on also keeps, on the device only, `title` (programme or episode), `showTitle`,
  `image` (the card image URL or gateway path), `source` (`ct` / `dvb`) and `savedAt`; entries without `title` (saved
  before T13) are left out of the row (they still offer "Pokračovat" in the guide). Selecting a card plays at once
  from the saved position (as "Pokračovat od …"); long OK / menu offers "Odebrat z řady" (deletes the entry). Nothing
  of it leaves the TV; profiles do not carry it (T08).
- **Shared details (T13, from the first implementation)**:
  - Each row ends with a "Vše ›" tile that opens the whole category. Return/Back on search results first clears
    the search. Source label in the facts: "iVysílání" / "Z antény" / "iVysílání a z antény". An episode date gets
    the year when it is not this year (`9. 1. 2005`).
  - An episode with a "Pokračovat" offer: OK plays from the position; long OK / menu offers "Přehrát od začátku"
    (this exact label). A series page has no Zpět button (Back/Menu/Return); a film page ends with `Zpět` as T15.
  - `creators` is one display string ("Režie: … Hrají: …"), shown as it comes (no extra "Tvůrci:" label).
  - Resume entries of ČT programmes also keep `start` and `channel` (CT API id), so a programme that began < 3 h ago
    resumes from the timeshift buffer by the decision rules (also from the "Pokračovat ve sledování" row); without
    them (older entries, archive episodes) it is VOD by idec. An episode's `channel` display name ("ČT1") maps to the
    CT channel the same way.
  - The TMDB attribution: the answer's `attribution` when present (the gateway sends it whenever cards carry TMDb
    data), else TMDb's standard sentence. Card images only as an `https` ČT URL, `/dvb/details/image/w342/…` or
    `/dvb/recording/<id>/thumb.jpg` (the latter follows the TV's thumbnails switch); anything else dropped.
  - Texts (all three apps): search hint "Hledat v archivu"; "Archiv se nepodařilo načíst"; "Archiv je zatím
    prázdný"; "Nic nenalezeno"; the chip for all sub-genres "Vše"; episode counts with the three plural
    forms "1 díl / 2–4 díly / 5 dílů" (golden in each app's tests). Number keys (channel numbers) are off on Archiv,
    so the search field gets digits.
  - Film buttons follow T15: `Pokračovat od …` first when there is an offer, then `Přehrát` (the film's first
    iVysílání episode, else its first episode), `Zpět`.
  - The TMDB logo/sentence shows when any card on screen has `"tmdb": true` (the gateway decides).
  - An episode's playback channel is its `channelId` (CT API id `CH_1` … or `null`; the gateway sends it); when it is
    `null` the display name `channel` is mapped the same way ("ČT1" → `CH_1`); still unknown → VOD by idec only.
- **Not in v1**: favourites, profiles' watch history.
- Golden cases: `spec/fixtures/archive_cases.json` (genre mapping, title merging, card source/order), all three apps
  where they decide something (merging is the gateway's; the apps test card/line rendering rules).

## "Pokračovat" (resume where the viewer stopped)
Each TV remembers, **on the device only**, how far it got in a programme, and offers to continue there. Nothing leaves
the device (no account, no sync, no analytics); only positions are stored, never stream URLs or video.
- **Key**: CT programme `ct:<idec>`; "Anténa" recording `dvb:<recording id>`. Live (`Živě`) is never remembered.
- **Progress** = seconds from the programme's start: archive (VOD) → player position; "Zpět v čase" (timeshift) →
  wall-clock of the shown frame − programme start (on players without wall-clock, position − (planned at − W) style
  estimate); Anténa continuous recording (`b…`) → wall clock of the playhead (timeline map) − programme start;
  TVHeadend DVR recording → player position.
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
  to `start + progress` (fallback offset `max(0, (start + progress) − (now − W))`), CT archive and TVHeadend DVR
  recordings start at `progress`, Anténa timeline at the map position of `start + progress`. A CT programme that began < 3 h ago is entirely in the buffer, so the resume point is too.

## "Kanály" (hide and reorder channels on this TV)
In **Nastavení → Kanály** the viewer hides channels and changes their order **on this device**, or, when the TV is
signed in to a **profile** ("Profil" below), in that profile on the Pi (another TV signed in to the same profile
gets the same; "Pokračovat" never leaves the device). Applies to **Živě** and the **TV průvodce** alike.
Separate from the gateway's `/admin` page: a channel switched off there is not received at all; here a channel is just
not shown on this TV.
- **Keys**: CT internet channel `ct:<API id>` (`ct:CH_1`); Anténa channel `dvb:<gateway channel id>`; Chamber of
  Deputies stream `psp:<stream id>` (`psp:stream1`, see "Poslanecká sněmovna").
- **Stored**: `{"order": [keys], "hidden": [keys], "sourcesOff": ["ct" | "dvb" | "psp"]}` (missing `sourcesOff` = all on; tvOS `UserDefaults` key `channelPrefs`, Android
  `SharedPreferences` `channel_prefs`, Samsung `localStorage` `cztvhub.channelPrefs`; JSON, at most 500 keys each).
  Unknown keys are kept (an Anténa channel switched off in `/admin` keeps its place when it comes back).
- **Sources**: the whole **Internetová TV** (`ct`, the six CT channels) and the whole **Anténa** (`dvb`, the gateway's
  channels) can each be switched off. A source that is off contributes no channels anywhere (Živě, guide, the
  settings order list); its channels keep their stored order/hidden state for when it is switched on again. Anténa
  without a configured gateway has no channels anyway (its switch is not shown).
- **One mixed list** (no "Internet ČT" / "Anténa" groups any more): the channels of the sources that are on, in default
  order CT first (catalogue order), then Anténa (gateway order), then Poslanecká sněmovna (stream 1…5). The viewer may put Anténa channels between CT ones.
- **arrange(channels in default order, prefs)** → `all` = keys of `order` that are in the list (first occurrence),
  then the list's other channels in default order (a new channel is appended at the end); `visible` = `all` without
  `hidden`. Živě and the guide show `visible`; the settings list shows `all`.
- **move(key, by ±1)** swaps the channel with its neighbour in `all` (no-op at the edge); the new `order` = `all`
  followed by the stored keys not in the list (e.g. the channels of a source that is off). Hiding/showing does not
  change the order. **Obnovit výchozí** clears `order`, `hidden` and `sourcesOff` (all sources on), or, when the
  gateway has a **household default** (below), makes the TV follow that default again.
- **Household default (T11)**: a TV that is **not signed in** and whose channel list the viewer **never changed**
  shows the household default from `GET /dvb/defaults` (`spec/dvb-gateway.md`), fetched on app start and when
  Nastavení opens and kept on the device for offline starts; a newer `version` replaces it. The first change on the
  TV (hide/show, move, a source switch) makes the TV's list its own (`own`), starting from what it showed; from then
  on the default no longer applies there. **Obnovit výchozí** clears `own` (and the TV's order/hidden/sources), so
  the TV follows the household default again (or the built-in order without one). A TV that already had stored
  prefs before T11 (non-empty `order`, `hidden` or `sourcesOff`) counts as `own`. Signed in, the profile applies as
  before; signing out returns to what the TV had (its own list, or following the default). A new profile starts with
  what the TV shows. The thumbnails switch is not part of the default. **Signed in**, Obnovit výchozí sets the
  profile's list to a copy of the household default (or the built-in order without one) and sends it to the profile
  as any change; the TV's own `own`/local list is untouched. `GET /dvb/defaults` failing, answering `404` (an older
  gateway) or a malformed body (`channelPrefs` neither an object nor `null`, a bad `version`) keeps the cached
  default; the same `version` again is ignored; **Odebrat bránu** or a changed gateway address drops the cache (a different
  gateway counts its versions from its own start). Golden cases:
  `spec/fixtures/household_default_cases.json` (`migrate`; `sequences` of the events `defaults` — its `settings` =
  the gateway's `channelPrefs` — `change` and `reset`, each step with the state and the `effective` list), all three
  apps. "Newer" = a higher `version`.
- **Source icon**: every channel shows where it comes from, an internet/globe icon for CT internet channels and an
  antenna icon for Anténa (SF Symbols `globe` / `antenna.radiowaves.left.and.right`, Material `Language` / `SettingsInputAntenna`
  or similar, inline SVG on Samsung): in the settings rows (required), on the Živě cards and next to the channel name
  in the guide (small, so "ČT1" and "ČT1 HD" can be told apart).
- **Version line**: the top of Nastavení (the choice screen, always visible there) shows
  "Verze <version> (build <n> · <commit> · <d. m. HH:MM>)": app version, build number = number of git commits
  (`git rev-list --count HEAD`), short commit hash (`+` appended when built from uncommitted changes) and the build
  time (Prague), all stamped at build time (tvOS build phase into Info.plist, Gradle `BuildConfig`, Samsung
  `build.py` into a generated script; the Samsung browser demo shows "vývojová verze"). Nothing is fetched at run time.
- **Nastavení is a choice of three sections**: the Nastavení screen shows only three large items, **Kanály**
  (first), **Profil** (description "Přihlášen: <jméno>" or "Nepřihlášen") and **Server**, each with a one-line
  description. Selecting one opens that section **full screen** (no tab bar row, no
  other section): Kanály = this list; Server = the Anténa gateway (address, key, "Uložit a ověřit", "Odebrat bránu",
  discovery in the local network; "O aplikaci" at its end). The remote's **Back** (Siri Remote Menu/‹, Android
  Back, Samsung Return) returns to the choice, with focus on the item just left; Back on the choice leaves Nastavení
  as before.
- **Settings UI (Kanály)**: first the source switches `[☑ Internetová TV (ČT)]`, `[☑ Anténa]` (only with a gateway)
  and `[☑ Poslanecká sněmovna]`, then one list "Pořadí kanálů" of rows `[icon ☑ ČT1] [▲] [▼]` (left/right moves between the three
  controls; select on the name toggles "zobrazovat", hidden rows dimmed, ▲ on the first / ▼ on the last row
  disabled), then "Obnovit výchozí pořadí". Anténa channels need the gateway (unreachable → Czech message above the
  list, the CT channels still listed). Focus stays on the moved row's button after ▲/▼ and the list scrolls so the
  moved row stays visible.
- When no channel is left (all hidden or both sources off), Živě and the guide say
  "Všechny kanály jsou skryté. Zobrazíte je v Nastavení → Kanály."
- Golden cases: `spec/fixtures/channel_prefs_cases.json` (`arrange` and `move` on mixed lists; the input list is
  already filtered by the sources that are on), tested in all three apps.

## "Profil" (T08: settings in a profile on the Pi)
Nastavení → **Profil** lets a household member keep this TV's settings in a profile on the gateway
(`spec/dvb-gateway.md` "Profiles") and load them on any TV by signing in. A signed-in TV **stays signed in** (app
restarts included) until **Odhlásit**, or until the gateway says the session is gone.
- **What travels** = the settings document `{"v": 1, "channelPrefs": <"Kanály" stored JSON>, "thumbnails": <the
  switch "Náhledy anténních pořadů">}`; fields the app does not know are kept and sent back unchanged. Never the
  gateway address/key, never "Pokračovat", never the password: the TV stores only the profile name and the session
  token, both in the platform's secure storage (Keychain / encrypted prefs / Samsung: next to the gateway key).
- **Needs the gateway**: without one the section says "Profily se ukládají na bránu Raspberry. Nejdřív ji nastavte
  v Nastavení → Server." **Odebrat bránu** while signed in signs out locally first.
- **Signed out** the section offers **Přihlásit se** (Jméno, Heslo, `Přihlásit`) and **Vytvořit profil** (Jméno,
  Heslo, Heslo znovu, `Vytvořit`; the new profile gets **this TV's current settings**). Password fields are masked;
  "Hesla se neshodují", and the gateway's Czech `error` text for 400/401/409/429/507, shown under the form.
- **Signed in** it shows "Přihlášen: <jméno>", a state line ("Nastavení je uložené v profilu" / "Čeká na uložení —
  brána není dostupná") and **Odhlásit**.
- **Sync** (`spec/fixtures/profile_cases.json` "sync", a reducer over events, the same in all three apps):
  - **Sign in**: the TV's own current settings are kept aside on the device (`local`), the profile's settings
    become the active ones. **Create**: `local` = the current settings, which the profile also starts with.
  - **While signed in every change** (Kanály: hide/show, move, sources, Obnovit výchozí; the thumbnails switch) is
    applied at once on the TV and sent with `PUT /dvb/profile` after **1 s** without another change (`pending` until
    it succeeded). A failed send keeps `pending` and is retried on app start, when Nastavení opens and every 60 s.
  - **On app start and when Nastavení opens** (signed in): with `pending` the TV sends first; otherwise it reads
    `GET /dvb/profile` and applies it when its `version` differs from the last one seen (another TV changed it).
  - **Odhlásit**: `POST /dvb/profile/logout` (failure ignored), then `local` becomes active again and the token is
    deleted. **`401` with `"code": "profileSignedOut"` from any profile call** does the same (a 401 of the app key,
    without that code, is only shown as a gateway error) and says once "Profil byl odhlášen (smazaný nebo nové
    heslo). Platí zase nastavení této televize."
- **Details shared by all three apps**:
  - Before sending, the app checks name and password itself: "Jméno musí mít 1 až 32 znaků." / "Heslo musí mít 4 až
    64 znaků." (the gateway's own 400 text otherwise). Passwords are sent as typed; the gateway normalises them (NFC).
  - The state line says "Čeká na uložení — brána není dostupná" only after a send **failed** (also for a change still
    pending from before a restart), never during the 1 s wait.
  - A change made while a `PUT` is on its way is sent again right after it (the reducer's `pushed` alone would lose it).
  - **Odebrat bránu** while signed in: a best-effort `POST /dvb/profile/logout` (not awaited), then the local sign-out.
  - Signed in, the Kanály intro says "Ukládá se do profilu <jméno>." instead of the app's usual "this TV only" intro
    (which stays as it is when signed out).
  - The profile calls carry the app key as the `Authorization: Bearer` header (the gateway refuses the cookie there).
- Golden cases: `spec/fixtures/profile_cases.json` (`names`, `passwords`, `document`, `sync`), tested in all three apps
  and, for names/passwords, in the gateway.

## "Tailscale v aplikaci" (T12: the tailnet without the Tailscale app, Apple TV and Android TV)
Away from home the gateway is reached over the owner's Tailscale tailnet. Instead of the separate Tailscale app (a
VPN the viewer has to start), Apple TV and Android TV carry Tailscale inside the app: `apps/tailnet` (Go, Tailscale's
`tsnet`, userspace, no VPN profile, no system permission) runs a tailnet node and a **bridge on `127.0.0.1`** that
forwards plain HTTP to the gateway over the tailnet. With the switch on, **the app's gateway base URL is
`http://127.0.0.1:<port>`** and everything (API, playlists, segments, thumbnails, profile) goes through it unchanged:
the key is still sent on every request, the gateway sees an ordinary tailnet client. **Samsung has no Tailscale in the
app** (Tizen web apps cannot run it): it reaches the gateway on the LAN as before.
- **Nastavení → Server**, below the key: switch **"Připojovat přes Tailscale"** (default off; Android TV and Apple TV
  only), with the hint "Mimo domov: zadejte jako adresu jméno Raspberry v Tailscale (např. raspberrypi)." With the
  switch on, a status line (golden cases `spec/fixtures/tailnet_cases.json` "status"), then as the state asks:
  - **QR code** of the login URL (`loginURL`) with the line "Naskenujte QR kód telefonem a přihlaste se do Tailscale."
    and the URL as small text under it. The matrix comes from the bridge (`QRMatrix`, rows of `1`/`0` incl. the quiet
    zone), drawn by the app as black modules on white, at least 6 px per module, placed so it is fully visible without
    scrolling (it takes no focus; Apple TV puts it beside the controls).
  - **"Přihlásit klíčem"**: a masked text field for a Tailscale auth key (`tskey-auth-…`); `Přihlásit` restarts the node
    with that key. The key is used once and never stored (the node's state replaces it).
  - **"Odhlásit z Tailscale"** (`logout` in the golden cases): logs the node out, deletes the saved state, turns the
    switch off. The next switch-on needs a new login.
- **Status line** (golden cases, the same in both apps): off → "Vypnuto"; start failed →
  "Tailscale se nepodařilo spustit: <error>"; `Running` → "Připojeno jako <self> (<ip>)" (without the IP part when
  empty); `NeedsLogin` with a `loginURL` → the QR line above; `NeedsMachineAuth` → "Televize čeká na schválení v
  administraci Tailscale."; anything else → "Připojuji se k Tailscale…". `usesBridge` = switch on and the node started
  (also before `Running`: the bridge then answers 502 `{"error": "Brána není přes Tailscale dostupná."}`, shown like any
  gateway error).
- **Target**: the gateway address from Nastavení → Server as `host:port` (golden cases "target", the same parsing as
  the gateway address: default port 8098, scheme/path dropped). Changing the address or key with "Uložit a ověřit"
  restarts the bridge to the new target; the check runs through the bridge. Switch on without a gateway address: the
  node is not started, the line says "Tailscale se nepodařilo spustit: chybí adresa brány." and there is no bridge.
- **"Odhlásit z Tailscale" is offered** when the node has an identity (`info.self` non-empty), whatever its state.
- "Uložit a ověřit" with a changed address restarts the node and waits up to 20 s for `Running` before checking; a
  failed check moves the bridge back to the saved address. Anything stored per gateway (the household default list's
  cache) is keyed by the **saved** address, never by `127.0.0.1:<port>` (the port changes on every start).
- The node state is also saved when the app goes to the background (polling stops once `Running`).
- **Back in front (T18)**: tvOS and Android suspend the app in the background and the node's connections can die
  there while its state still says `Running` (seen 2026-10-24: the Apple TV node `offline` in the tailnet, every
  gateway request answered by the bridge's 502, the Anténa group gone away from home). So whenever the app becomes
  active with the switch on, and whenever a gateway request through the bridge answers the bridge's own 502, the app
  **checks the bridge**: `GET http://127.0.0.1:<port>/health` (the gateway's health, no key needed) with a 4 s
  timeout. Answered → nothing to do. Not answered → the node is **restarted** with its saved state (stop + start, no
  new login), the status polled every 2 s until `Running` (at most 20 s), checked again, and then the gateway data
  the screen needs (channels, guide, defaults, profile) are loaded again — the Anténa group comes back by itself. At
  most one check at a time, and after any node start (app start, switch on, new address, restart) the next check
  waits at least 30 s. While checking, Živě/guide show "Připojuji se k Tailscale…" instead of hiding Anténa.
  Details (T18): the restart (`Node.Restart()` / `TailnetRestart()` / gomobile `restart()`) keeps the bridge on the
  **same port**, so the app's gateway URL and a playing player stay valid; triggers are the bridge's own 502 (its
  exact error text, not the gateway's 502) and a request through the bridge that gets no answer (timeout or
  connection error, not a cancel); the health check counts only HTTP 200; after a failed restart the data are still
  reloaded, so the screens show the error instead of "Připojuji se…" for ever; thumbnails do not trigger a check.
  The bridge's dial to the gateway times out after 8 s (a tsnet dial while the node is not connected hangs, so
  without it the app saw a client timeout instead of the 502).
- **Start (T21)**: after an app start the node needs a few seconds to connect, and the first gateway requests get the
  bridge's 502 while `quietAfterStart` suppresses the T18 check (seen 2026-10-25: Apple TV switched off and on, the
  app started again, the Anténa group stayed away until the app went to the background and back). So: **every time the
  node's state changes to `Running` from any other state** (start, switch on, new address, restart, or a later
  reconnect), the app loads the gateway data the screens need again (channels, guide, defaults, profile), at most
  once per change. After any node start the status is polled **every 1 s until `Running`** (at most 20 s, then every
  30 s as before; the T18 restart's own wait polls every 1 s too), and while the node is connecting Živě and the guide
  show "Připojuji se k Tailscale…" instead of an empty Anténa group; in `NeedsLogin` / `NeedsMachineAuth` (which can
  last) they show "Tailscale čeká na přihlášení (Nastavení → Server)." instead. A double load right after a start
  (new route, then the change to `Running`) is fine. A T18 check still runs at most once at a time; a reload already done by the change to
  `Running` makes the check's own reload unnecessary (one reload, not two).
- **Connection line (T18)**: `Info()` also gives the gateway peer's path, `"gateway": {"direct": bool, "relay":
  "<DERP region code>", "latencyMs": <int> | null}` (from the node's peer status and a ping, refreshed with the
  status). Nastavení → Server shows it after "Připojeno jako …": "· k bráně přímo, 32 ms" or "· přes relé fra,
  85 ms" (relé = Tailscale's DERP relay, which is slow for video), nothing when unknown. Golden cases in
  `spec/fixtures/tailnet_cases.json` "status".
- **Lifecycle**: with the switch on the node starts at app start before the first gateway request (`Start` returns at
  once; login and connection go on in the background) and runs while the app runs; switch off stops it (the saved state
  is kept, so switching on again needs no login; "Odebrat bránu" leaves Tailscale as it is). The status is polled every 2 s while Nastavení → Server is open, else
  every 30 s until `Running`.
- **Node state**: the node's keys live **only in the platform's secure storage** (Keychain / encrypted prefs), never in
  a plain file: the app passes the saved state to `Start` and saves `SavedState()` whenever `Info().stateVersion`
  differs from the last saved one. Machine name: `televize-appletv` / `televize-androidtv` (Tailscale adds a suffix
  when it is taken).
- **No third-party traffic beyond Tailscale itself**: Tailscale's log upload is compiled out (`ts_omit_logtail`) and
  disabled at run time; the node talks only to Tailscale's coordination/DERP servers and the tailnet.
- **Access**: the tailnet's ACLs and the gateway key decide, as for any tailnet client (`docs/legal-notes.md`); the
  gateway is unchanged. The bridge listens on `127.0.0.1` only (other apps on the same TV could reach it, but still
  need the key).
- **Build**: `python3 apps/tailnet/build.py android|tvos` (Go; gomobile AAR for Android TV, a C library built with
  `GOOS=ios` against the tvOS SDKs for Apple TV; Go has no official tvOS target). The apps' builds call it and it does
  nothing when up to date. Size: the Android TV APK grows by about 42 MB (arm64 + armv7 `libgojni.so`, stored
  uncompressed), the Apple TV app by about 21 MB. The AAR's Java package is `cz.cztvhub.bridge.tailnet`, outside the
  app's own package (gomobile's keep rule covers its whole `-javapkg`).

## "Poslanecká sněmovna" (live streams of the Chamber of Deputies, T06, approved 2026-10-24)
A third source, **on by default**, with five live-only channels the Chamber publishes on psp.cz ("Živé vysílání").
Catalogue (`spec/fixtures/psp_cases.json` "streams", the same in every app; no other URLs, no mirrors):

| key | name | URL |
|---|---|---|
| `psp:stream1` | Schůze PS | `https://pspcr-ott-live.ssl.cdn.cra.cz/channels/ps-stream1/playlist/cze.m3u8` |
| `psp:stream2` | Tiskové konference | `…/channels/ps-stream2/playlist/cze.m3u8` |
| `psp:stream3` | Výbory a semináře 1 | `…/channels/ps-stream3/playlist/cze.m3u8` |
| `psp:stream4` | Výbory a semináře 2 | `…/channels/ps-stream4/playlist/cze.m3u8` |
| `psp:stream5` | Výbory a semináře 3 | `…/channels/ps-stream5/playlist/cze.m3u8` |

- **Play only, live only** (CLAUDE.md hard rules): the player opens the URL as published, no extra or faked headers,
  no recording, no "Pokračovat", no start-over or rewind beyond the player's own live window, no thumbnails.
- **"Is it on now?"** `isLive(status, body)` = HTTP 200 and the body is an HLS playlist (`#EXTM3U`) with at least one
  media segment (`#EXTINF`) or variant (`#EXT-X-STREAM-INF`); anything else (404 "channel ps-streamN is not processed
  now" outside broadcasts, seen 2026-10-24; any 4xx/5xx; an empty playlist; not a playlist) = not on. A **network
  error** (no answer) keeps the last known state; a "live" state older than 180 s without a confirming check is shown as
  unknown ("Zjišťuji…"), not as live. Next check: after a normal answer (200, or the 404 "not processed now")
  60 s; after a 5xx or a network error 120 s.
  Checked only while Živě or the guide is on screen, the five streams one after another, each at most once per
  60 s (after an error: 120 s); nothing in the background, no segment is fetched. **Right before playback** the app
  reuses a check younger than 60 s or makes one more check of the same playlist (one GET per viewer action; the
  player fetches that playlist anyway), so an off stream gets the message instead of a player error. Golden cases:
  `psp_cases.json` "live".
- **Icon**: a generic building-with-columns symbol (SF Symbols `building.columns`, Material `AccountBalance`, inline SVG
  on Samsung), never the Chamber's logo or the state emblem. Source label in settings: "Poslanecká sněmovna PČR".
- **Živě**: on → the card shows a clear red **"● ŽIVĚ"** badge and "Přímý přenos"; off → the card is dimmed with
  "Nevysílá se". Selecting an off channel shows "Poslanecká sněmovna teď nevysílá." (no player); an on channel plays
  live. Unknown state (not checked yet) = "Zjišťuji…", selecting it tries to play.
- **TV průvodce**: one row per stream with a single cell over the whole day. **Today**: "Přímý přenos" (look
  `airing`, select = play live) or "Nevysílá se" (dimmed, select = the message above). **Other days**: a dimmed
  "Jen přímé přenosy" cell; select → "Poslanecká sněmovna: jen přímé přenosy, bez záznamů." The detail shows the
  channel name and "Zdroj: Poslanecká sněmovna PČR". No programme data.
- **Labels**: the source switch may read "Poslanecká sněmovna" with a muted second line "PČR · jen přímé přenosy";
  long stream names wrap to two lines on the Živě cards (the three "Výbory a semináře N" must stay distinguishable).
- **Zapping / numbers**: the streams are ordinary channels in `visible` (numbered, ▲/▼, digits). Zapping onto a
  stream that is not on shows the overlay with "Nevysílá se" and the player's message instead of a stream error.
- **Player errors**: a 404 when opening means the broadcast just ended → "Poslanecká sněmovna teď nevysílá."
- Golden cases `psp_cases.json`: "streams" (catalogue), "live" (status/body → on or not), "arrange" (default order
  with three sources and `sourcesOff` containing `psp`). Tests never contact psp.cz or its CDN.

## Channel numbers and zapping
Every channel shown on this TV has a **number**: its 1-based position in `visible` of "Kanály" (hidden channels
and the channels of a source that is off get no number; the others close up). Reordering or hiding renumbers.
- **Shown**: in Nastavení → Kanály before the name of each visible row (`1  ČT1`; a hidden row shows no number),
  on the Živě cards and in the guide's channel column (`1 ČT1`, small and dimmed before the name).
- **Zapping (live only)**: while the player plays **live** (Živě, CT or Anténa, also when rewound in the
  timeshift buffer), ▲/▼ switches to the **previous/next** channel in `visible` (▲ = number − 1, ▼ = number + 1),
  wrapping around at both ends; the dedicated **CH+/CH−** keys do the same (CH+ = next = ▼). Start-over, archive
  and recordings keep their keys as before (no zapping there).
- **Number keys**: a remote with digits picks a channel by number, in the live player and on Živě and the guide
  (there it starts that channel live). Digits collect in a buffer shown top right (`2_`); the buffer is used
  **at once** when no further digit could give a valid number (`buffer × 10 > count`) or it has 3 digits, else
  after **2 s** without a digit or on OK. A leading `0` is ignored. A number outside 1…count shows
  "Kanál <n> není" and nothing changes. Number keys during start-over/archive/recordings are ignored.
- **Quick presses**: every press moves the target at once and shows the overlay; the new stream is resolved only
  after **0.6 s** without another zap press (▲▲▲ jumps three channels with one request). Number entry starts the
  stream right when the buffer is used.
- **Overlay**: on every zap or number switch a small box top left shows `<number>  <channel name>` and the
  programme airing now (from the guide data the app already has), for ~3 s. The player's info bar title then follows
  the new channel ("Title in the player's info bar").
- The list used is `visible` at the moment the player opened (a settings change applies next time). **Back** after
  zapping leaves the player as before; on Živě the focus goes to the card of the channel that was playing, if cheap.
- **Keys per platform**: tvOS = Siri Remote clickpad up/down through press recognizers on the view hosting
  AVPlayerViewController, only while the transport bar is hidden (with it shown AVKit keeps the arrows; AVKit's
  `skipToNextChannel` delegate never fired in the simulator), digits from `UIPress.key` when the remote/keyboard
  sends them (the Siri Remote has none). Android TV = `DPAD_UP`/`DPAD_DOWN`
  while the player controls are hidden (with the controls shown they move focus in the controls as before),
  `CHANNEL_UP`/`CHANNEL_DOWN` always, `0`–`9` and `NUMPAD_0`–`9`. Samsung = ▲/▼ and CH+/CH− (registered keys
  `ChannelUp`/`ChannelDown`), digits `0`–`9` (registered), like the TV's own tuner; the **audio track** choice
  goes to the remote's **AUDIO/MTS** key and the **green** key (registered `MTS`, `ColorF1Green`) everywhere;
  outside live ▲ keeps switching it too.
- Golden cases: `spec/fixtures/channel_numbers_cases.json` (`step` = zap neighbour, `type` = one digit into the
  buffer, `resolve` = number to channel), tested in all three apps.

## Quality bar
- Unit tests for API parsing (all fixtures) and decision rules (all golden cases); no network in tests.
- Builds from the command line (`xcodebuild` / `./gradlew` / `apps/tizen/build.py`), runs in the tvOS simulator /
  Android TV emulator / a desktop browser (`index.html?demo`, fixtures only) for the Samsung app.
- Samsung (AVPlay) has no wall-clock seek: start-over uses the computed buffer offset only.
- Small, readable modules: `api` (client + models), `domain` (decisions, day labels), `ui` (screens), `player`.
- Remote-friendly focus everywhere; large readable type (10-foot UI); dark theme.
