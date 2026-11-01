# Web (T27, draft for the owner's review)

"Televize" in a web browser on a computer, tablet or phone, served by the household's own gateway, **1:1 with the
Samsung app**: the same screens, rules and texts (`spec/apps.md`), only the platform parts differ. The person
reaches the gateway as the apps do: on the home network, or from outside through the household's tailnet with the
**Tailscale app running on that computer/phone** (the web has no Tailscale of its own, unlike the Apple TV and
Android TV apps, T12). Owner's words and decisions: `notes/TASKS.md` T27 (private).

## Where it lives
- **Code**: the Samsung app is a plain ES2017 web app; the web serves **the same files** (`apps/tizen/app`) plus a
  thin web layer `apps/web/` that replaces the Samsung-only parts. A feature built for Samsung is on the web at once;
  a feature that touches a replaced part (player, keys, CT transport, storage) also changes its web adapter, in the
  same change ("TV apps stay in step").
- **Served by the gateway** at `http://<gateway>:8098/web/` (`spec/dvb-gateway.md` "Web"), only when the gateway is
  on (`[dvb]`; no gateway = no web, "a feature with a missing value is off"). Static files from the install
  (`/opt/cztvhub/web`, copied by `install.sh`), never fetched at run time, no CDN, no analytics.
- **Browsers**: current Safari (macOS, iPadOS, iOS), Chrome and Edge (desktop and Android). Firefox: everything but
  Anténa video where it cannot decode H.265 (below).

## Web layer (`apps/web/`)
- **Player** (replaces AVPlay): an HTML5 `<video>`. HLS natively only where the browser has it **and** an audio
  track API (Safari), or where hls.js cannot run (MSE missing); else **hls.js** (Chrome 156 answers `canPlayType`
  "maybe" for HLS but has no `audioTracks`: native there would lose the audio choice and the key header)
  (owner approved 2026-11-01; Apache-2.0: vendored in `apps/web/vendor/` with its `LICENSE` (and `NOTICE` when the
  release has one), its licence header kept, the pinned version and SHA-256 in `apps/web/README.md`, a
  "Third-party" line there and on the version line's "O aplikaci"; updated only on purpose). The same API the Samsung screens use (open, play, pause, seek,
  position, duration, audio tracks, events), so `player.js` / `timeline.js` logic stays shared. Audio tracks from the
  HLS manifest (hls.js `audioTracks`, Safari `audioTracks`); subtitles: as the Samsung app.
- **Anténa video is H.265 (HEVC)**: before playing the app checks `MediaSource.isTypeSupported('video/mp4;
  codecs="hvc1.1.6.L120.90"')` (hls.js) or `video.canPlayType(...)` (native). When the browser cannot decode it, the
  player shows "Tento prohlížeč neumí přehrát vysílání z antény (H.265). Zkuste Safari, nebo Chrome či Edge na
  počítači s podporou HEVC." instead of a black picture. ČT and Poslanecká sněmovna are H.264 and not affected.
- **Input**: keyboard as the TV remote (arrows, Enter = OK, Esc/Backspace = Back, digits = channel numbers,
  Space = play/pause, long Enter = long OK) **and** mouse/touch: pointing at a focusable element focuses it, a
  click/tap is OK on it, a long press is long OK, the mouse wheel / swipe scrolls lists and the guide grid, a
  visible **‹ Zpět** button top left does Back (needed on touch screens). The player shows its bar on mouse move /
  tap; dragging the bar seeks.
- **Layout**: the TV layout (1920 × 1080) scaled to the window, kept 16:9, centred (letterboxed). On a phone held
  upright: "Otočte telefon na šířku." over the page (v1; a separate narrow layout is not part of T27).
- **No download**: no download button, no saving or export anywhere in the web layer; media only through the
  gateway's existing HLS routes.
- **Storage**: `localStorage` of that browser instead of Tizen's (same keys and shapes as the Samsung app:
  settings, channels, resume, profile session). Each browser is "one TV".
- **Nastavení → Server**: the address is the page's own origin (no address field, no discovery); the **key** field
  as on Samsung (masked, "Zobrazit"/"Skrýt", T25), `Uložit a ověřit`. The key goes in `localStorage` and in the
  cookie `cztvhub_key` (`Path=/dvb; SameSite=Strict`, set by the page) for media requests the browser makes
  itself (Safari's native HLS cannot add a header; hls.js sends the `Authorization` header). "Odebrat bránu" deletes
  both. Without a key every tab shows "Zadejte klíč brány v Nastavení → Server." (the archive's no-gateway text
  pattern).
- **Version line**: "Verze … (web)" from the build info the gateway install writes.

## ČT (internet channels) on the web
- The browser cannot ask ČT itself: ČT's API answers browsers only for pages from `ceskatelevize.cz` (CORS,
  checked 2026-11-01: no `Access-Control-Allow-Origin` for any other origin). So the web asks **the gateway**, which
  calls the same ČT API the apps call and passes ČT's answer through unchanged (`spec/dvb-gateway.md` "ČT for the
  web"). The schedule (guide) works everywhere.
- **Playing**: **only on the home network, enforced by the gateway** (legal-guard 2026-11-01): it hands out ČT's
  stream answers only to browsers on the home LAN, never over the tailnet. ČT's signed URLs are meant for the IP
  that asked (`skipIpAddressCheck=false`, never tested from a second IP), so the project does not rely on ČT to
  enforce it. ČT's CDN allows any origin (`Access-Control-Allow-Origin: *`, checked 2026-11-01). Away from home (or
  at home through the tailnet address) the player shows "ČT jde v prohlížeči přehrát jen doma. Mimo domov použijte
  aplikaci Televize nebo iVysílání." with **Otevřít v iVysílání**. Video is never relayed through the gateway (hard
  rule). Using the Pi as a Tailscale exit node is **not** part of T27 and would need its own legal review: it would
  route ČT's video through the household's line, make ČT's IP-bound URLs work away from home and lift ČT's
  Czech-only limit abroad.
- **Gateway errors**: the gateway's own `{"error": …}` answers (401 wrong key, 403 not at home, 502 ČT down) are
  turned by the web transport into ČT's error shape with the gateway's text; ČT's own answers pass unchanged.
- **Otevřít v iVysílání**: on ČT programme pages by the shared rule (programmes that have started) and in the ČT
  player's error, as on Apple TV/Android TV (the Archiv show page: not in T27);
  opens `https://www.ceskatelevize.cz/ivysilani/` in a new tab (the apps open the iVysílání app's start, too).

## Poslanecká sněmovna on the web
**Needs the owner's dated approval first** (the T06 rule names the apps only; wording proposed by legal-guard in
`notes/TASKS.md` T27). If approved: played straight from the Chamber's CDN as in the apps (no proxy, live only,
the same live-check limits as the shared code); if not, the web leaves the source out. Where the browser refuses it (the
CDN's CORS headers for hls.js: to be checked when building; Safari's native HLS does not need them), the player says
"Přenos Sněmovny tento prohlížeč nepřehraje. Zkuste Safari nebo psp.cz." Never a workaround through the gateway.

## Security (walk `docs/SECURITY-REVIEW.md` as part of "done")
- The cookie `cztvhub_key` becomes a browser-wide credential for `/dvb/*`: state-changing `/dvb/*` routes keep
  needing the key as a header (profiles already do), so a cookie alone never changes anything (CSRF); a test per
  route. `SameSite=Strict`, `Path=/dvb`.
- `/admin` is the same origin: the web never calls it, and the admin's own guards (Basic auth, `X-Cztvhub-Admin`,
  same-origin) stay; CSP (no inline scripts) is the guard against script injection, tested.
- No new media or file routes; the static route cannot reach recordings (start check above).

## Not in T27
A separate phone layout, Tailscale inside the page, a Pi exit node for ČT, offline use, installing as a PWA.

## Tests
- `node:test` for the web layer: player adapter against a fake `<video>`/hls.js (events, seek, audio tracks, the
  HEVC check and its message), the CT transport (which ČT URL becomes which gateway path, bodies passed through),
  input mapping (keys and pointer → the same actions as the Samsung remote), storage and cookie (set, cleared on
  "Odebrat bránu"), scaling (window sizes → scale and offsets; upright phone → the rotate text). The Samsung tests
  keep running on the shared code. Golden cases where a rule is shared (`spec/fixtures/web_cases.json`).
- Gateway: routes in `server/tests` (below). A headless Chrome smoke run of `/web/` against `dvb-fake` (own ports,
  never 9981/8098) in the verify skill.
