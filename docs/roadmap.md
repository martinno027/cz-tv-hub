# Roadmap

1. **v0.1 (done)** playlist + redirect for 6 CT channels, `check` command, systemd unit.
2. **v0.2 (done)** Kodi add-on `plugin.video.cztvhub`: client-side resolution so it works outside the home
   network (CT URLs are IP-bound), "Zpet v case" start-over (3 h buffer), iVysilani archive by channel/day.
   To verify on a real Kodi: start-over seek (`StartOffset` with inputstream.adaptive), long sessions.
3. **v0.3 (done)** TV guide: the add-on's service writes M3U + XMLTV (7 days back, 2 ahead, descriptions,
   images) for PVR IPTV Simple Client; catch-up from the guide via plugin:// URLs. Same ZIP on Android TV,
   Apple TV (sideloaded Kodi), Raspberry, desktop. `kodi-addon/build.py --serve` for installing over the LAN.
   To verify on real devices: IPTV Simple catch-up with plugin:// source, tvOS.
4. **Archive browsing by show** (search, show → episodes) via the iVysilani GraphQL API.
5. **Resilience**: refresh logic for very long viewing sessions (token expiry ~1 h), retry/backoff,
   structured logging.
6. **Channel logos** in the guide, auto-configuration of IPTV Simple (or IPTV Manager integration).
7. **JSON API / shared core** for the custom player app (channels, now/next, archive). The app must resolve
   stream URLs on the device itself, like the add-on.
8. **DVB-T2 (gateway done, untested on real hardware)** via TVHeadend on the Pi: `/dvb/*` JSON + on-demand fMP4
   HLS (live, start-over of an airing programme, recordings kept `retention_days`), `dvb-setup`, `dvb-fake`,
   `deploy/install-dvb.sh`, apps show it as the "Anténa" group (`spec/dvb-gateway.md`). To verify on the Pi: TVHeadend
   API against a real install (no-tuner behaviour, digest auth, autorec rule matching), remux speed of long
   recordings on the Pi, AVPlayer with bearer header on segments, multi-language audio (only the first track
   is passed through today).
9. ~~Remote use via proxy mode~~: not needed, the add-on resolves on the device. Proxy stays opt-in,
   LAN/Tailscale-only and CZ-only if it is ever revisited.
10. **Samsung TV app** (`apps/tizen`, Tizen 4.0+): done, tested with fixtures and in a desktop browser only. To verify
    on a real TV: cross-origin CT API calls under the `<access>` policy, AVPlay start-over offset in the timeshift
    buffer, the gateway key cookie on HLS segments, fMP4/HEVC from the gateway, `tizen` CLI packaging.
11. **Ad-break skipping for "Anténa" recordings** (proposal, `spec/ad-breaks.md`, approved 2026-10-18): a detection worker
    on a household machine (Windows PC first, a newer Pi later) marks breaks by logo absence; apps offer
    "Přeskočit reklamu". Needs real recordings from a tuner to tune the detection.
