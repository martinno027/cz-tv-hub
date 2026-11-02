# Televize v prohlížeči (web)

Aplikace Televize pro televize Samsung, jen v prohlížeči počítače, tabletu nebo telefonu: stejné obrazovky,
pravidla i texty ([`spec/web.md`](../../spec/web.md), [`spec/apps.md`](../../spec/apps.md)). Nabízí ji domácí
brána cz-tv-hub na `http://<brána>:8098/web/` (jen se zapnutou anténou, `[dvb]`). Mimo domov přes tailnet jen
s aplikací Tailscale v tom zařízení.

- **Stejné soubory jako Samsung**: brána servíruje `apps/tizen/app` na `/web/app/` a tenhle adresář na `/web/`.
  `js/boot.js` načte značky z `app/index.html` a skripty Samsungu ve stejném pořadí; hned za
  `app/js/ui/platform.js` (výchozí hodnoty Samsungu) vloží webovou vrstvu. Co přibude v aplikaci pro Samsung,
  je na webu hned.
- **Webová vrstva** (`js/`): přehrávač `<video>` (HLS přímo v Safari, jinde hls.js; klíč brány jako hlavička jen
  pro `/dvb/…` brány), ČT přes bránu (`/dvb/ct/live|vod|schedule`, prohlížeč se ČT zeptat nesmí), klávesnice
  jako ovladač, myš a dotyk (najetí = výběr, klik = OK, podržení = dlouhé OK, kolečko a tah = šipky,
  **‹ Zpět** vlevo nahoře, lišta přehrávače: tah = posun), zmenšení plochy 1920 × 1080 do okna, „Otočte telefon
  na šířku.“, klíč v `localStorage` a v cookie `cztvhub_key` (`Path=/dvb; SameSite=Strict`).
- **Rozdíly proti Samsungu**: v Nastavení → Server není adresa (je to adresa stránky), tlačítka „Uložit a
  ověřit“ a „Odebrat bránu“; bez klíče všechny záložky říkají „Zadejte klíč brány v Nastavení → Server.“;
  na stránce pořadu ČT je „Otevřít v iVysílání“; Poslanecká sněmovna je na webu stejně jako na Samsungu (schváleno
  1. 11. 2026, T28); anténa (H.265) jen v prohlížečích, které HEVC umí, jinak to přehrávač řekne česky. ČT jde přehrát jen
  doma (to hlídá brána; mimo domov text a „Otevřít v iVysílání“).
- **Vlastní okno** (T35): `manifest.webmanifest` a značky pro Safari v `index.html`, takže Chrome/Edge („Otevřít
  jako okno“) i Safari („Přidat do Docku“) otevřou Televizi bez adresního řádku. Žádný service worker ani offline
  mezipaměť. Ikony v `icons/` kreslí `python3 apps/web/tools/make_icons.py` (jen standardní knihovna) ze stejné
  kresby jako ikona Samsungu (`apps/tizen/tools/make_icon.py`); maskovatelná je zmenšená na 80 % do bezpečné zóny.
- **Samostatné okno** (T46, `js/web-window.js`): tlačítko ⧉ vedle ⛶ otevře Televizi ve vyskakovacím okně bez
  záložek a lišt (velikost jako současné okno; ⧉ je i v liště přehrávače a přehrávání se pak přesune do okna:
  stejný kanál, záznam nebo pořad na stejném místě); původní záložka zastaví přehrávač a ukáže „Televize běží
  v samostatném okně.“ s „Vrátit sem“. V okně je místo něj ⧈ „Zpět do prohlížeče“ (zavře okno), zavření okna
  křížkem udělá totéž. Okna se domlouvají přes `BroadcastChannel('televize')`, záložka navíc hlídá své okno. Na
  telefonech a tabletech tlačítko není; zablokované okno ohlásí „Prohlížeč samostatné okno zablokoval…“.
- Nic se nestahuje ani neexportuje, žádné CDN ani analytika; vše jen z brány.

## Testy a vývoj

```sh
node --test apps/web/test/*.test.js      # webová vrstva (spolu s jádrem Samsungu)
node --test apps/tizen/test/*.test.js    # Samsung zůstává zelený
```

Ukázka v prohlížeči (jen fixtury, nikdy skutečná ČT ani brána): server, který mapuje `/web/` → `apps/web/`,
`/web/app/` → `apps/tizen/app/` a `/web/fixtures/` → `spec/fixtures/`, pak `http://localhost:<port>/web/?demo`.
Parametry ukázky jsou ty ze Samsungu (`apps/tizen/README.md`). `js/build-info.js` (řádek verze) zapisuje instalace
brány; bez něj „vývojová verze (web)“.

## Third-party

Third-party: hls.js 1.7.4 (Apache-2.0)

- Soubor: `vendor/hls.min.js` = `package/dist/hls.min.js` z oficiálního balíčku npm, beze změny (plná verze:
  odlehčená `hls.light` nemá alternativní zvukové stopy, které anténa potřebuje).
- Zdroj: https://registry.npmjs.org/hls.js/-/hls.js-1.7.4.tgz (npm `integrity`
  `sha512-3GlxGdPvT2YHvQYOt+yOzT/DEior/qJVNIylHA77kgeoBTdQQqi70GwefNEUmHQeNGyjC8U62+bruqW/Tpm+tg==`, ověřeno při
  stažení 2026-11-01).
- SHA-256 `vendor/hls.min.js`: `cfc70cc9e9fc63d071919e087670abc760c8fd1bc889b88588026e4d22193bbe`
- SHA-256 balíčku `.tgz`: `e4ab3d5d13f7cca9ef241d098f39686e19fd451831ba07dc9943482fd5ff75c7`
- Licence: Apache-2.0 (`package.json` verze 1.7.4: `"license": "Apache-2.0"`), text v `vendor/LICENSE`
  (= `package/LICENSE` z balíčku; NOTICE balíček nemá). Minifikovaný soubor nemá vlastní licenční hlavičku, proto je
  licence vedle něj.
- Aktualizuje se jen záměrně: nový tarball, ověřit `integrity`, zkopírovat `dist/hls.min.js` a `LICENSE`, přepsat
  verzi a SHA-256 zde, v `js/platform-web.js` (`HLS_JS`) a pustit testy (`test/vendor.test.js` hlídá shodu).
