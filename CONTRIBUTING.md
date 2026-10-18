# Contributing

Thanks for helping! cz-tv-hub is an unofficial, personal, non-commercial project (see [README](README.md) and
[docs/legal-notes.md](docs/legal-notes.md)). Pull requests are welcome if they respect the rules below.

## Hard rules (a PR that breaks one of them will be closed)
- **Sources:** only Česká televize's own official internet streams, and free-to-air channels from the user's own
  DVB-T2 tuner. No third-party, pirate or leaked sources. Never decrypt encrypted multiplexes.
- **No circumvention:** no DRM, geo-blocking, token or IP-check circumvention. Never request DRM streams or use CT's
  licence server. If CT reports `isBlocked` or DRM-only, show CT's message.
- **CT internet streams:** play only. Never record, download, cache or re-serve video; the Pi only redirects.
- **DVB-T2:** recordings stay on the Pi's local disk, are deleted after the retention period (default 3 days, max 14)
  and cannot be downloaded or exported. Playback uses temporary HLS deleted when viewing stops.
- **Access:** home LAN or the household's own Tailscale tailnet only, always authenticated. Never document exposing a
  port to the public internet.
- **No builds or hosted services:** no committed ZIPs/APKs/IPAs, no playlists or EPG exports, no public servers.
- **No tracking:** no analytics, ads SDKs or accounts.

## Technical conventions
- Server and Kodi add-on: Python 3.11+, **standard library only** (the Pi needs no pip installs). Modules bundled into
  the Kodi add-on (`api.py`, `channels.py`, `guide.py`, `plugin.py`) must stay Python 3.8 compatible.
- Apps: native per platform (Swift/SwiftUI/AVPlayer, Kotlin/Compose for TV/Media3), no third-party SDKs beyond
  AndroidX/Media3.
- Code, comments and commit messages in English; user-facing text and user guides in Czech.
- Every behaviour change gets a unit test. Tests never hit the network; shared test data in `spec/fixtures` is
  synthetic. A change to the programme decision rules updates `spec/fixtures/decisions.json` and must pass in the
  server, the tvOS app and the Android TV app.
- The CT API is undocumented: parse defensively.

## Checks
```bash
(cd server && PYTHONPATH=src python3 -m unittest discover -s tests)
(cd apps/tvos/CTCore && swift test)
(cd apps/androidtv && ./gradlew test)
python3 tools/check_public.py
```
