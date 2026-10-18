# Nastavení Kodi

Stejný ZIP doplňku „Televize – živě a zpětně“ funguje v Kodi na Android TV, Apple TV, Raspberry, Windows, Macu i Linuxu.
Raspberry ani žádný server k němu potřeba není: každé Kodi se na ČT připojuje samo, proto doplněk funguje
i mimo domov v ČR a na více zařízeních najednou.

Co doplněk umí:

- **Živě, Zpětně (archiv)**: v nabídce doplňku (Doplňky → Video doplňky → Televize – živě a zpětně).
- **TV průvodce**: klasická mřížka programu v sekci **TV** v Kodi. Týden zpět a 2 dny dopředu,
  pořady z minulosti jdou pustit přímo z průvodce. Potřebuje k tomu doplněk PVR IPTV Simple Client (krok 3).

## 1. Kodi na zařízení

- **Android TV / Google TV** (Nvidia Shield, Chromecast s Google TV, televize s Android TV):
  Kodi nainstalujte z **Google Play**.
- **Apple TV (tvOS)**: Kodi v App Store **není**. Instaluje se tzv. sideloadem z počítače (Mac nebo Windows,
  např. nástrojem Sideloadly a vaším Apple ID) podle návodu na kodi.wiki („HOW-TO:Install Kodi on Apple TV“).
  S bezplatným Apple ID je potřeba Kodi **každých 7 dní znovu podepsat**, s placeným vývojářským účtem jednou za rok.
- **Raspberry / Windows / Mac / Linux**: Kodi z kodi.tv, na Raspberry Pi OS `sudo apt install kodi`.

Doporučeno všude: **InputStream Adaptive** (Doplňky → Instalovat z repozitáře → Doplňky videopřehrávače →
InputStream Adaptive; na Raspberry Pi OS `sudo apt install kodi-inputstream-adaptive`). Bez něj nejde přetáčení.

## 2. Instalace doplňku ze ZIPu

Apple TV ani většina Android TV nemá USB, ZIP se proto nejsnáz přenese přes domácí síť:

1. Na počítači s tímto repozitářem: `python3 kodi-addon/build.py --serve`
   (vytvoří `dist/plugin.video.cztvhub-<verze>.zip` a nabídne složku v domácí síti na portu 8099).
2. Kodi → Nastavení → Systém → Doplňky → zapněte **Neznámé zdroje**.
3. Kodi → Nastavení → **Správce souborů** → **Přidat zdroj** → `http://<IP počítače>:8099/` → pojmenujte např. „ct“.
4. Kodi → Doplňky → ikona otevřené krabice → **Instalovat ze ZIP souboru** → „ct“ → vyberte ZIP.
5. Na počítači ukončete `--serve` (Ctrl+C).

Na zařízeních s USB nebo přístupem k souborům můžete ZIP nahrát i přímo (`python3 kodi-addon/build.py` bez `--serve`).
Aktualizace: postavte nový ZIP a nainstalujte ho stejně, přepíše starou verzi. Zdroj „ct“ v Kodi zůstane.

## 3. TV průvodce (sekce TV)

Doplněk si sám, na pozadí, připravuje seznam kanálů a program a obnovuje je každých 6 hodin.
PVR IPTV Simple Client je jen zobrazí.

1. Otevřete doplněk Televize – živě a zpětně a zvolte **Aktualizovat TV průvodce** (poprvé, ať soubory hned existují).
2. Doplňky → Instalovat z repozitáře → **Klienti PVR** → **PVR IPTV Simple Client** → Instalovat → **Konfigurovat**.
3. **Obecné** (*General*):
   - Umístění (*Location*): **Místní cesta** (*Local path*)
   - Cesta k M3U (*M3U play list path*): procházet → **Profilový adresář** (*Profile directory*) →
     `addon_data` → `plugin.video.cztvhub` → `cztvhub.m3u`
     (tj. `special://profile/addon_data/plugin.video.cztvhub/cztvhub.m3u`)
   - Režim obnovení (*Auto refresh mode*): **Opakované obnovení** (*Repeated refresh*), interval **60** minut
4. **EPG**:
   - Umístění: **Místní cesta**
   - Cesta k XMLTV: stejná složka, soubor `cztvhub-epg.xml`
5. **Catchup**: **Zapnout catchup** (*Enable catchup*) = ano.
6. Kodi restartujte. Kanály a program najdete v **TV → Kanály** a **TV → Průvodce**.

V průvodci:
- pořad, který **běží**, se pustí živě (od začátku ho pustíte v nabídce doplňku: Zpětně → kanál → Dnes),
- pořad, který začal před **méně než 3 hodinami**, se pustí ze záznamu „Zpět v čase“,
- **starší** pořad se pustí z archivu iVysílání,
- pořad, který ČT na internetu nenabízí (licence, DRM), ukáže hlášku ČT, např. „ČT nemá práva pro internet“.
  Pořady, které ČT v archivu chrání DRM (např. StarDance), jdou po 3 hodinách pustit jen v oficiální aplikaci iVysílání.

## Ovládání v nabídce doplňku

- **Živě**: seznam kanálů s právě vysílaným pořadem.
- **Zpětně**: kanál → den (až 14 dní zpět) → pořad. *(právě běží)* pustí pořad od začátku,
  *(nedostupné)* ukáže důvod od ČT, *(zatím nevysíláno)* ještě nezačalo.

Jak dlouho jsou pořady v archivu, určuje ČT podle licence (některé jen 7 dní, některé jen 3 hodiny po vysílání).
Ze zahraničí ČT většinu pořadů blokuje. To je v pořádku a neobcházíme to.

## Raspberry server (volitelné, jen doma)

Starší cesta bez doplňku: Raspberry nabízí `http://<ip-raspberry>:8098/playlist.m3u` pro PVR IPTV Simple Client
(Umístění: Vzdálená cesta). Umí jen živé vysílání a funguje jen v domácí síti, protože odkaz od ČT je vázaný
na IP Raspberry. S doplňkem ho nepotřebujete. Port 8098 nevystavujte do internetu.

## Když něco nejde

- Ve složce `server/`: `PYTHONPATH=src python3 -m cztvhub check` ukáže, který kanál, „Zpět v čase“ nebo archiv selhává.
- Průvodce je prázdný: v doplňku zvolte Aktualizovat TV průvodce, zkontrolujte cesty v IPTV Simple, restartujte Kodi.
- V Kodi zapněte ladicí protokol a hledejte `cztvhub`, `inputstream.adaptive`, `pvr.iptvsimple`
  a `ffmpeg` (skill `kodi-log-analysis`).
