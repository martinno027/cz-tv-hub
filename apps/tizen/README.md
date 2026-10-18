# Televize pro Samsung TV (Tizen)

Aplikace pro **televize Samsung s Tizenem 4.0 a novějším (modely 2018+)**: oficiální streamy České
televize a volitelně vlastní anténa přes bránu cz-tv-hub. Chování odpovídá [`spec/apps.md`](../../spec/apps.md)
a [`spec/dvb-gateway.md`](../../spec/dvb-gateway.md), stejně jako aplikace pro Apple TV a Android TV.

- **Živě**: šest kanálů ČT s právě vysílaným pořadem, časem a průběhem; pod nimi skupina **Anténa**, je-li nastavená.
- **TV průvodce**: mřížka kanálů a času (7 dní zpět, 2 dny dopředu), otevře se na „teď“, nahoře detail pořadu.
  Běžící pořad: **Od začátku** (Zpět v čase / rostoucí záznam antény) nebo **Živě**. Odvysílaný pořad:
  archiv iVysílání, do 3 h od začátku vždy ze „Zpět v čase“, u antény ze záznamu. Nedostupné pořady jsou ztlumené,
  po výběru se ukáže důvod od ČT („ČT nemá práva pro internet“, u DRM odkaz na oficiální aplikaci iVysílání).
- **Přeskočit reklamu** (volitelné, zapíná se na bráně, viz [`spec/ad-breaks.md`](../../spec/ad-breaks.md)): u hotových
  nahrávek z antény se během reklamního bloku ukáže vpravo dole tlačítko; OK ho stiskne.
- **Přehrávač**: Samsung AVPlay, OK = pauza, ◀ ▶ posun o 10 s, ⏪ ⏩ o minutu, Zpět = konec.
  Když odkaz během sledování vyprší, aplikace si vyžádá nový a pokračuje.

Stream se vždy vyžádá až na televizi těsně před přehráním (odkazy ČT jsou vázané na IP a platí asi hodinu).
Aplikace nic nenahrává, neukládá ani neobchází DRM či geoblokaci. Bez reklam, analytiky a účtů.

> Neoficiální projekt, není spojen s Českou televizí. ČT a iVysílání jsou ochranné známky České televize.

## Stav

Jádro (API ČT, pravidla výběru pořadu, průvodce, anténa) je otestované nad sdílenými fixturami a celé UI
je vyzkoušené v prohlížeči s ukázkovými daty. **Na skutečné televizi zatím ověřené není**, hlavně:

- volání API ČT z aplikace (Tizen povoluje požadavky na jiné domény přes `<access origin="*">` v `config.xml`;
  ČT neposílá CORS hlavičky),
- skok „Od začátku“ v bufferu Zpět v čase (AVPlay neumí hledat podle `#EXT-X-PROGRAM-DATE-TIME`, aplikace proto
  skáče o spočítaný posun od začátku 3h bufferu),
- anténa: AVPlay neumí poslat hlavičku `Authorization`, proto posílá klíč brány jako cookie `cztvhub_key`
  (brána ji přijímá) a fMP4/HEVC HLS z brány,
- balení přes `tizen` CLI (`build.py`).

## Struktura

- `app/` – samotná aplikace (Tizen web app, čisté JavaScript bez závislostí a bez překladu, ES2017 kvůli
  Chromiu 56 v Tizenu 4.0):
  - `js/core/` – logika bez UI (API ČT, dny, pravidla, rozložení průvodce, anténa, plánovač přehrání),
  - `js/ui/` – obrazovky, ovládání ovladačem, přehrávač, ukázkový režim,
  - `config.xml` – manifest (oprávnění: internet, AVPlay, klávesy ovladače).
- `test/` – testy v Node (`node:test`, bez npm balíčků), včetně hlídače syntaxe pro Chromium 56.
- `build.py` – zkopíruje aplikaci do `dist/tizen/`, zabalí ji do `Televize.wgt` a případně nainstaluje do TV.
- `tools/make_icon.py` – zástupná ikona (bez loga ČT).

## Testy a vývoj v prohlížeči

```sh
cd apps/tizen
node --test test/*.test.js                 # Node 18+
cd ../.. && python3 -m http.server 8000    # z kořene repozitáře
# v Chrome: http://localhost:8000/apps/tizen/app/index.html?demo
```

Ukázkový režim (`?demo`) odpovídá na všechny požadavky z testovacích fixtur (`spec/fixtures`) posunutých na
zvolený den; nic se nepřehrává. Ovládání: šipky, Enter, Esc/Backspace = Zpět, PageUp/PageDown = o stránku řádků.

## Instalace do televize (bez obchodu Samsung)

Jednorázová příprava:

1. Nainstalujte [Tizen Studio](https://developer.tizen.org/development/tizen-studio/download) a v Package
   Manageru doplňte **Samsung Certificate Extension** a **TV Extensions** (stačí verze pro Tizen 4.0+).
2. V Tizen Studiu → *Tools → Certificate Manager* vytvořte profil **Samsung** (přihlášení účtem Samsung)
   a do certifikátu distributora přidejte **DUID** televize (Tizen Studio ho načte z připojené TV přes *Device Manager*).
   Certifikát platí jen pro televize, jejichž DUID obsahuje.
3. Na televizi zapněte **Developer Mode**: *Aplikace (Apps)* → na ovladači zadejte `1 2 3 4 5` → zapnout
   a zadat IP adresu počítače → televizi vypnout a zapnout.

Sestavení a instalace (počítač i TV ve stejné domácí síti):

```sh
python3 apps/tizen/build.py --install 192.168.1.50          # IP televize
# s předvyplněnou anténou (klíč ze souboru, nedostane se do gitu ani do historie shellu):
python3 apps/tizen/build.py --gateway cztvhub.local --key-file ~/cztvhub.key --install 192.168.1.50
```

Soubor `Televize.wgt` s předvyplněnou anténou obsahuje klíč brány: nikomu mimo domácnost ho nedávejte
(stejně jako sestavení bez předvyplnění – aplikace je jen pro vlastní domácnost, viz `docs/legal-notes.md`).

Bez `tizen` CLI skript skončí po zkopírování do `dist/tizen/Televize`; tu složku jde také otevřít v Tizen Studiu
(*Import → Tizen → Tizen Project*) a spustit přes *Run As → Tizen Web Application*. Pokud CLI není v PATH,
nastavte `TIZEN_STUDIO` (výchozí `~/tizen-studio`).

Aplikace nainstalovaná vývojářským certifikátem na televizi zůstane; po vypršení certifikátu (podle typu
několik měsíců až let) je potřeba ji podepsat a nainstalovat znovu.

## Nastavení antény

Záložka **Nastavení**: adresa brány a klíč (z `/etc/cztvhub/cztvhub.conf`, `[dvb] key`). Doma IP adresa Raspberry
nebo `cztvhub.local`. Tailscale pro televize Samsung neexistuje, takže mimo domácí síť anténa na Samsungu
nepojede (vysílání ČT přes internet ano, to funguje kdekoli v ČR). Jiné než domácí adresy nastavení odmítne.
Psaní 43znakového klíče ovladačem je otravné, proto ho jde předvyplnit při sestavení (`--gateway`, `--key-file`).

Klíč je uložený v soukromém úložišti aplikace (localStorage aplikace na televizi), ne šifrovaně jako na
Androidu / Apple TV – Tizen webovým aplikacím nic jako Klíčenku nenabízí.

Port brány nikdy nevystavujte do internetu.
