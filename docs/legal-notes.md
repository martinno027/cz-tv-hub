# Legal notes (not legal advice)

cz-tv-hub is an unofficial personal project. It is not affiliated with, endorsed by or connected to Česká televize
or Poslanecká sněmovna Parlamentu ČR.
"ČT", "ČT1", "ČT2", "ČT24", "ČT sport", "ČT :D", "ČT art" and "iVysílání" are trademarks of Česká televize.

## Live streams of the Chamber of Deputies (approved by the user on 2026-10-24)
- Only the public live streams that Poslanecká sněmovna Parlamentu ČR publishes for everyone on psp.cz ("Živé
  vysílání", Stream 1–5: sessions, press conferences, committees and seminars). The viewer's TV app plays them directly
  from the Chamber's CDN; there is no proxy, no server, no playlist or Kodi export.
- Play only, live only: no recording, download, caching or timeshift, now or later. They are never fed into TVHeadend.
- No circumvention: the apps request the published URL without extra or faked headers. If the streams start to need a
  token, login, referer or geo check, the apps show an error and the source is removed.
- The "live now" check reads only the playlist, only while the screen showing it is visible, at most once a minute
  per stream, plus one read of the same playlist right before the viewer starts a stream (the player fetches it
  anyway); no segments, nothing in the background.
- Not affiliated with or endorsed by the Chamber; its name only says where a stream comes from. Its logo and the state
  emblem (zákon č. 352/2001 Sb.) are not used. No specific terms of use for the streams were found on psp.cz
  (checked 2026-10-24); the basis is "play only, as offered". The archive videoarchiv.psp.cz is not used (it would
  need its own review).

## Internet streams of Česká televize
- Only the public live streams, the "Zpět v čase" buffer and the iVysílání archive that Česká televize offers to
  everyone through its own web player are used.
- Play only. CT's FAQ: "Video materiály na iVysílání lze pouze přehrát, stahování jeho obsahu není legální cestou
  možné." There is no download or record feature for CT internet streams, and there never will be.
- No DRM circumvention, no geo-block circumvention, no token or IP-check circumvention. Items CT marks as DRM-only
  (`UNSUPPORTED_DRM`) or without internet rights (`MISSING_LICENCE_*`) are shown as unavailable with CT's own message,
  plus a pointer to CT's official iVysílání app. DRM is never requested and CT's licence server is never used.
- A programme that began less than 3 hours ago is played from CT's official "Zpět v čase" buffer, which CT serves to
  everyone. This is decided by time only, never as a reaction to an archive error.
- CT's API is not documented for third parties; it can change or be restricted at any time. Respect CT's terms of use
  and keep requests polite (signed URLs are cached for at most 60 s).

## DVB-T2 (antenna)
- Only free-to-air channels received by your own tuner at home. Encrypted multiplexes are never decrypted
  (no CA/CI module or softcam circumvention).
- The channels selected in `/admin` are recorded continuously as a private copy for one household (§ 30 zákona
  č. 121/2000 Sb.; levies are included in storage media prices). The recording stays on the Raspberry Pi's local disk,
  is deleted automatically after the retention period (default 3 days, maximum 14) or earlier when space runs short,
  and cannot be downloaded or exported. Only free-to-air channels from TVHeadend on the Pi are recorded; ČT internet
  streams never.
- Playback of the recording is one programme, the live window, or a channel timeline: a window of at most 12 hours
  of one channel around a programme (the programme ± 3 h), opened one at a time as the viewer seeks (approved by the
  user on 2026-10-24). No playlist covers a channel's whole recording, and no view can be downloaded or exported.
- Remote viewing over Tailscale is meant for members of the same household only (Tailscale ACLs, no Funnel, no node
  sharing). Relaying broadcasts outside the home is a legal grey area; you decide whether to enable it and you are
  responsible for that decision. Another household needs its own tuner.
- Optional ad-break detection: a worker on a household machine (home LAN or own tailnet, never a cloud machine)
  reads ended programmes of the recording through the gateway's authenticated HLS, analyses them in memory and stores only break
  timestamps and per-channel logo masks. Skipping is a manual seek by the viewer; the recording is never cut or
  altered. Break data and masks stay in the household and are never published.
- Optional programme thumbnails (approved by the user on 2026-10-23, `spec/dvb-gateway.md` "Thumbnails"): the
  gateway makes one small still (at most 480 px wide) per recording from the household's own recording, on request
  (by an app for the focused programme or a small window around it; no bulk or batch fetching),
  in memory only (ffmpeg pipes, never written to disk on the Pi or cached on disk by the apps). It disappears with
  the recording, needs the same key, is never made from ČT internet streams, never uses a tuner and can be switched
  off for the household (`/admin`) and per TV. Thumbnails are never published, committed or shown in screenshots.

## Publishing and use
- The source code is public for personal, non-commercial use. The maintainer publishes no builds, Kodi ZIPs, APKs,
  playlists or EPG exports and runs no public server. You build and run it yourself, for your own household only.
- Do not host it for others, expose it to the public internet or share access outside your household.
- Do not add sources other than those listed above, and never pirate sources.
- The Czech TV licence fee (televizní poplatek) remains your responsibility.
