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
- **Input (T29, owner 2026-11-01: "ovládat jako klasický web")**: the mouse works as on any web page, not as a
  remote: hovering only highlights (no focus jump, no scrolling of the page by hover); **one click acts** (opens a
  programme, switches a day chip or tab, presses a button, plays a card); the wheel / trackpad / touch scroll the
  lists and the guide grid natively (horizontally too) without moving a focus; the keyboard focus (arrows, Enter,
  Esc/Backspace, digits, Space) is shown only while the keyboard is used and disappears on the first mouse action, so
  a click never leaves a second highlighted item (e.g. a white day chip next to the selected "Dnes").
  **Back = the browser's Back** (history: the programme page, a Nastavení section, the Archiv sub-menu, the player
  are history entries; the browser's Back/Forward and Alt+← work); the separate black "‹ Zpět" strip is removed.
  Where a screen needs a visible way back on touch, it is a "‹ Zpět" link **inside that screen's own header** (the
  programme page, a Nastavení section, the player bar), never over content. Long press (touch) / right click = long OK.
  In the guide the detail panel shows the programme under the mouse pointer once it rests there ~0.2 s (no focus
  change, no scrolling); when the pointer leaves the grid it shows the focused (last clicked) programme again; not
  on touch (no hover).
- **Mute (T34)**: a 🔇/🔊 button in the player bar before ⛶ and the key M (spec/apps.md "Mute").
- **Fullscreen**: a **"Celá obrazovka"** button (icon ⛶) at the right end of the tab bar and in the player bar,
  the key **F**, and a double click on the video; uses the browser's Fullscreen API on the whole page (the player
  keeps its bar); "Ukončit celou obrazovku" / Esc leaves it. Hidden where the browser has no Fullscreen API (iPhone).
- **Layout (T31, owner 2026-11-01: "rámeček má být přes celý prohlížeč")**: the app fills the **whole browser
  window**, no black borders: the TV layout is scaled by the window's width (1920 px wide), and its height is the
  window's height at that scale, so lists, the guide grid and the Archiv grid simply show more or fewer rows. A phone held
  upright shows "Otočte telefon na šířku." as before; a window shorter than 600 TV px at that scale too.
  **"‹ Zpět" only where Back means something** (a programme or show page, a Nastavení section, the Archiv
  sub-menu, the player), inside that screen's own header within the window; never on Živě, the guide, Archiv or the
  Nastavení choice, never over content.
- **Nastavení choice**: the three items (Kanály, Profil, Server) are centred horizontally in the window (web only).
- **Kanály: drag and drop** (web): a channel row can be dragged with the mouse (or a long touch and drag) to a new
  place in the list; the drop position is shown as a line between rows; dropping saves the order exactly as the ▲▼
  buttons do (the same prefs, profile sync, numbers renumbered). ▲▼ and the keyboard keep working.
- **No download**: no download button, no saving or export anywhere in the web layer; media only through the
  gateway's existing HLS routes.
- **Background (T41, owner 2026-11-01: „když přepnu na jinou záložku, tak přestane hrát, to by nemělo“)**: the
  player keeps playing when the browser tab is hidden or the window is minimised (the Samsung app stops when the TV
  app goes to the background; the web does not). Nothing re-resolves on return; a ČT URL that expired meanwhile is
  handled by the player's normal error/retry. Clocks and guide refreshes may run less often while hidden (browsers
  throttle timers); they catch up when the tab is visible again.
- **Storage**: `localStorage` of that browser instead of Tizen's (same keys and shapes as the Samsung app:
  settings, channels, resume, profile session). Each browser is "one TV".
- **Nastavení → Server**: the address is the page's own origin (no address field, no discovery); the **key** field
  as on Samsung (masked, "Zobrazit"/"Skrýt", T25), `Uložit a ověřit`. Below the status the sign-in/connection note of T37. The key goes in `localStorage` and in the
  cookie `cztvhub_key` (`Path=/dvb; SameSite=Strict`, set by the page) for media requests the browser makes
  itself (Safari's native HLS cannot add a header; hls.js sends the `Authorization` header). "Odebrat bránu" deletes
  both. Without a key every tab shows "Zadejte klíč brány v Nastavení → Server." (the archive's no-gateway text
  pattern).
- **Version line**: "Verze … (web)" from the build info the gateway install writes.

## Install as an app (T35, owner 2026-11-01: „zobrazí jen tu zobrazovací část … a tlačítko zase přejít na klasické
zobrazení")
- **Full screen** (⛶, T29) is the in-page answer: it hides the browser's bars, ⛶ / Esc / F bring them back.
- **Own window**: a web app manifest (`manifest.webmanifest`: name „Televize", short name, `start_url` `/web/`,
  `display: standalone`, dark theme colour, icons 192/512 px and a maskable one, from the app's icon) and
  `<meta name="apple-mobile-web-app-capable">` + `apple-touch-icon`, so Chrome/Edge "Vytvořit zástupce → Otevřít jako
  okno" and Safari "Přidat do Docku" (macOS) / "Přidat na plochu" (iOS) open Televize in its own window without the
  address bar. Over plain HTTP (port 8098) browsers do not offer a real "install"; over HTTPS on 8443 with the
  Pi's Tailscale certificate (T47, `spec/dvb-gateway.md`) they do (Chrome/Edge "Nainstalovat"), alongside the T46
  own window (⧉), which stays. No
  service worker, no offline cache (nothing to cache; video never cached). Nastavení → Server gets one line on how
  ("Vlastní okno bez lišty: v Chrome ⋮ → Uložit a sdílet → Vytvořit zástupce → Otevřít jako okno; v Safari Soubor →
  Přidat do Docku."). In such a window, the app's own ⛶ still toggles full screen; leaving the window = closing it.
- The manifest and icons are static files under `/web/` (CSP: `manifest-src 'self'`).

## Own window (T46, owner 2026-11-02: „tlačítko na zobrazení jen samotného okna")
- A button **„Samostatné okno"** (icon ⧉) next to ⛶ in the tab bar and in the player bar opens Televize in a **popup window**
  (`window.open(location, 'televize', 'popup,width=…,height=…')` sized like the current window): browsers show such a
  window without tabs, toolbar or bookmarks (Chrome/Edge: only a slim title/URL strip; Safari: the title bar). The
  original tab stops its player and shows „Televize běží v samostatném okně." with **„Vrátit sem"**.
- In the popup the same button reads **„Zpět do prohlížeče"** (icon ⧈): it closes the popup and the original tab takes
  over again (focus, the „běží v okně" line gone; playback is not restarted automatically). Closing the popup by the
  window's own ✕ does the same.
- Where a popup is refused (blocker, iPhone/iPad which open a tab instead): the button is hidden on phones and
  tablets; elsewhere a refused popup says „Prohlížeč samostatné okno zablokoval – povolte vyskakovací okna pro tuto
  stránku." The popup keeps ⛶. Opening it from the player moves playback: the popup starts the same channel/programme
  (at the same position for recordings/archive; from its start when that position can no longer be opened), the
  tab stops. For Anténa live the tuner is released when the tab stops, so the popup may briefly see „Tuner je
  obsazený" (not checked with a real tuner). Every normal tab of the page shows the „běží v okně"
  line (nothing plays twice). Coordination via `BroadcastChannel('televize')` (same origin), no storage needed.

## Always on top (T48, owner 2026-11-02: „tlačítko always on top, které bude držet okno vždy nahoře")
- A page cannot pin a browser window; the web way is **Picture-in-Picture**, whose window stays above all others. A
  button **„📌 Vždy navrchu"** in the player bar (before ⧉; not in the tab bar: there is no picture to pin there) and
  the key **P** (unused by the remote table; not while typing, not with Ctrl/Cmd/Alt). Works the same in a tab and in
  the T46 popup.
- **Document Picture-in-Picture** where the browser has it (Chrome/Edge 116+, only in a secure context: the gateway's
  `https://…:8443/web/` and `localhost`): a small always-on-top window (480 px wide, height by the video's aspect,
  16 : 9 before it is known) into which the playing `<video>` element is **moved** (never a second stream: the same
  element, hls.js stays attached; checked in Chrome 156: it keeps playing on the move and back, and a re-resolve
  inside the window attaches and plays). In that window: the picture, the player's message (e.g. „Načítání…", an
  error) over it, and a bar shown on hover: ⏸/▶ (as Space), 🔊/🔇 (as M), **„Zpět do okna"**; keys Space, M,
  P/Esc. The page meanwhile shows **„Přehrává se v okně navrchu."** with **„Vrátit sem"** (keys: only Enter/Space,
  as the T46 line). „Zpět do okna", „Vrátit sem", P and closing the window (✕, the browser's „back to tab") put the
  element back into the player and it keeps playing. The player closing for any reason (end, T46 popup taking
  over) closes the window first.
- **Video Picture-in-Picture** where Document PiP is missing but `video.requestPictureInPicture` exists (Safari,
  iPad/iPhone, Chrome/Edge over plain HTTP `:8098`): only the picture goes on top (the browser's own window and
  controls); the page keeps its player, the button then reads **„Zpět do okna"** and ends it. In a Chromium browser
  over plain HTTP the first use says „Navrchu je jen obraz. Okno s ovládáním navrchu má Chrome a Edge na adrese
  https://…:8443/web/."
- **Neither** (Firefox, which offers its own PiP toggle over videos): the button is hidden, no hint (a dead button
  is worse than none; as ⛶ without the Fullscreen API). A refused request (no user gesture, a policy) says
  „Okno navrchu se nepodařilo otevřít." and the video stays in the page.
- Unverified on real devices: Safari/iPad video PiP, Edge, Windows; whether Firefox's own toggle appears over the
  app's video (it has no `controls`).

## ČT (internet channels) on the web
- The browser cannot ask ČT itself: ČT's API answers browsers only for pages from `ceskatelevize.cz` (CORS,
  checked 2026-11-01: no `Access-Control-Allow-Origin` for any other origin). So the web asks **the gateway**, which
  calls the same ČT API the apps call and passes ČT's answer through unchanged (`spec/dvb-gateway.md` "ČT for the
  web"). The schedule (guide) works everywhere.
- **Playing**: **always, at home and away** (owner 2026-11-02: „brána dá ČT vždycky“, replacing the T27
  home-only rule): the gateway hands ČT's stream answer to every web client with the key; the browser plays the
  signed CDN URL itself (ČT's CDN allows any origin). If ČT refuses an IP-bound URL, the player shows ČT's own
  error with **Otevřít v iVysílání**. Video is never relayed through the gateway (hard rule).
- **Gateway errors**: the gateway's own `{"error": …}` answers (401 wrong key, 403 worker key, 502 ČT down) are
  turned by the web transport into ČT's error shape with the gateway's text; ČT's own answers pass unchanged.
- **Otevřít v iVysílání**: on ČT programme pages by the shared rule (programmes that have started) and in the ČT
  player's error, as on Apple TV/Android TV (the Archiv show page: not in T27);
  opens `https://www.ceskatelevize.cz/ivysilani/` in a new tab (the apps open the iVysílání app's start, too).

## Poslanecká sněmovna on the web
**Approved by the owner on 2026-11-01 (T28)**: played straight from the Chamber's CDN as in the apps (no proxy,
live only, the same live-check limits as the shared code; the source and its switch as on Samsung). The CDN answers
any origin (`Access-Control-Allow-Origin: *`, checked 2026-11-01), so hls.js can play it; its host is in the
gateway's CSP. Never a workaround through the gateway.

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
