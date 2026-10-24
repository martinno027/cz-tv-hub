# cz-tv-hub

> **Neoficiální projekt, není spojen s Českou televizí ani s Poslaneckou sněmovnou PČR.** ČT, ČT1, ČT2, ČT24, ČT sport, ČT :D, ČT art a iVysílání
> jsou ochranné známky České televize.
> *Unofficial project, not affiliated with Česká televize or the Chamber of Deputies. For personal, non-commercial use in one household.*

Domácí televize ze tří zdrojů:

- **Internetové vysílání České televize**: živě, „Zpět v čase“ (až 3 h) a archiv iVysílání, přesně tak, jak je ČT
  nabízí ve svém webovém přehrávači. Funguje doma i mimo domov v ČR.
- **Anténa (DVB-T2)**: nekódované kanály z vlastního tuneru na Raspberry Pi, živě i ze záznamu (výchozí 3 dny).
- **Poslanecká sněmovna** (jen v aplikacích Televize): pět veřejných přímých přenosů z psp.cz (schůze, tiskové
  konference, výbory a semináře). Jen živě, nic se nenahrává; aplikace ukazuje, který přenos právě vysílá.
  Vypíná se v Nastavení → Kanály.

## Části

| Adresář | Co to je |
|---|---|
| `apps/tvos` | Aplikace „Televize“ pro Apple TV (Swift, SwiftUI, AVPlayer) |
| `apps/androidtv` | Aplikace „Televize“ pro Android TV / Google TV (Kotlin, Compose for TV, Media3) |
| `apps/tizen` | Aplikace „Televize“ pro televize Samsung (Tizen 4.0+, JavaScript, AVPlay) |
| `server/` | Služba pro Raspberry Pi: brána k TVHeadend pro anténu, playlist ČT pro domácí síť (`src/`, `tests/`, `deploy/`) |
| `kodi-addon/` | Doplněk pro Kodi „Televize – živě a zpětně“ včetně TV průvodce |
| `spec/` | Společná specifikace chování (`apps.md`), API brány (`dvb-gateway.md`) a testovací data (`fixtures/`) |
| `docs/` | Návody, architektura, právní poznámky, plán |

Návody: [Kodi](docs/kodi-setup.md), [anténa a Raspberry](docs/dvb-setup.md),
[Apple TV](apps/tvos/README.md), [Android TV](apps/androidtv/README.md), [Samsung TV](apps/tizen/README.md).

## Rychlý start

```bash
cd server
PYTHONPATH=src python3 -m unittest discover -s tests   # testy serveru a doplňku (bez sítě)
PYTHONPATH=src python3 -m cztvhub check                 # ověří streamy ČT (potřebuje internet z ČR)
cd ..
python3 kodi-addon/build.py                             # ZIP doplňku pro Kodi do dist/
```

Aplikace: `apps/tvos` (`cd CTCore && swift test`, projekt přes XcodeGen) , `apps/androidtv` (`./gradlew test assembleDebug`)
a `apps/tizen` (`node --test test/*.test.js`, balení `python3 apps/tizen/build.py`).
Přispívání: [CONTRIBUTING.md](CONTRIBUTING.md).

Služba pro Raspberry potřebuje jen Python 3.11 ze standardní knihovny; pro anténu navíc TVHeadend a ffmpeg (apt).

## Důležité / Important

- **Jen přehrávání.** Žádné stahování ani nahrávání internetového vysílání ČT ani přenosů Poslanecké sněmovny,
  žádné obcházení DRM, geoblokace ani kontroly tokenů. Pořady, které ČT chrání DRM, aplikace nepřehraje a nabídne oficiální aplikaci iVysílání.
- **Nedokumentované API.** Projekt používá stejné rozhraní jako webový přehrávač ČT. Může se kdykoli změnit nebo
  přestat fungovat. Respektujte podmínky ČT.
- **Anténa:** jen nekódované kanály z vlastního tuneru. Záznamy jsou soukromá kopie pro osobní potřebu jedné
  domácnosti (§ 30 zák. č. 121/2000 Sb.), zůstávají na Raspberry, automaticky se mažou a nejdou stáhnout.
- **Vzdálený přístup** přes Tailscale jen pro vaši domácnost. Přeposílání vysílání mimo domov je právní šedá zóna,
  rozhodnutí a odpovědnost jsou vaše. Nikdy nevystavujte službu do veřejného internetu.
- **Žádné hotové buildy.** Autor nezveřejňuje ZIPy, APK, playlisty ani neprovozuje veřejný server. Postavte si
  vlastní, pro vlastní domácnost.
- Televizní poplatek je vaše povinnost.

Podrobně: [docs/legal-notes.md](docs/legal-notes.md). Licence: [MIT](LICENSE).
