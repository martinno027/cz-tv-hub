# Televize pro Android TV

Nativní aplikace pro **Android TV / Google TV**: sledování **oficiálních streamů České televize**
(ČT1, ČT2, ČT24, ČT sport, ČT :D, ČT art) bez Kodi.

- **Živě**: šest kanálů s právě vysílaným pořadem, časem a průběhem. Výběr spustí živé vysílání.
- **TV průvodce**: mřížka kanálů a času (7 dní zpět, 2 dny dopředu), otevře se na „teď“.
  Ovladač posouvá po pořadech, nahoře je detail pořadu.
  - pořad, který právě běží: volba **Od začátku** (Zpět v čase) nebo **Živě**,
  - odvysílaný pořad: přehraje se z archivu iVysílání, případně ze záznamu „Zpět v čase“ (do 3 h),
  - nedostupné pořady jsou ztlumené; po výběru se zobrazí důvod od ČT (např. „ČT nemá práva pro internet“).
- **Přehrávač**: celá obrazovka, ovládání Media3 (posun, titulky, zvukové stopy).

- **Anténa** (volitelné): kanály z vlastního DVB-T2 tuneru doma přes bránu cz-tv-hub (viz `spec/dvb-gateway.md`).
  Druhá skupina v Živě a další řádky v průvodci: živě, „Od začátku“ u právě nahrávaného pořadu,
  přehrání odvysílaného pořadu ze záznamu; nenahrané pořady jsou ztlumené („Pořad nebyl nahrán“).
  Bez nastavení se Anténa nezobrazuje.

Stream se vždy vyžádá až na zařízení těsně před přehráním (odkazy ČT jsou vázané na IP a platí asi hodinu).
Aplikace nic nenahrává, neukládá ani neobchází DRM či geoblokaci. Bez reklam, analytiky a účtů.

Snímky (jen testovací obraz z falešné antény, žádný obsah ČT): [živě](docs/screenshots/dvb-live.png), [záznam](docs/screenshots/dvb-recording.png),
[od začátku](docs/screenshots/dvb-startover.png).

## Požadavky

- Android TV / Google TV s Androidem 8.0 (API 26) nebo novějším, připojení k internetu v ČR.
- Pro sestavení: JDK 17 nebo novější (např. ten, který je součástí Android Studia) a Android SDK s platformou 36.

## Sestavení a spuštění

```bash
cd apps/androidtv
# JAVA_HOME musí ukazovat na JDK 17+ (případně JDK přibalené k Android Studiu)
./gradlew test             # unit testy (API, rozhodovací pravidla, sdílené fixtures)
./gradlew assembleDebug    # app/build/outputs/apk/debug/app-debug.apk
./gradlew assembleRelease  # app/build/outputs/apk/release/app-release.apk (rychlejší, doporučeno do TV)
```

Release je kvůli jednoduchosti podepsaný ladicím klíčem; pro vlastní klíč doplňte `signingConfig`
v `app/build.gradle.kts`.

V emulátoru (obraz `system-images;android-34;android-tv;arm64-v8a`):

```bash
adb install -r app/build/outputs/apk/release/app-release.apk
adb shell am start -n cz.cztvhub.tv/.MainActivity
```

## Nastavení antény

Záložka **Nastavení**: adresa brány a klíč (z `/etc/cztvhub/cztvhub.conf`, `[dvb] key`). V domácí síti se brána
najde sama (`_cztvhub._tcp`), mimo domov zadejte jméno Raspberry v Tailscale (např. `pi.tailnet.ts.net`)
nebo adresu `100.x.y.z`. Klíč je uložený šifrovaně (Android Keystore).

Brána běží na prostém HTTP, proto aplikace povoluje nešifrované spojení, ale jen na adresy domácí sítě
a tailnetu (192.168.x.x, 10.x, 172.16–31.x, 100.64–127.x, `*.local`, `*.ts.net`, jednoslovná jména);
jinou adresu nastavení odmítne. Česká televize se vždy volá přes HTTPS
(`app/src/main/res/xml/network_security_config.xml`). Port brány nikdy nevystavujte do internetu.

Vývoj bez tuneru: falešný TVHeadend a brána podle `spec/dvb-gateway.md`, v emulátoru adresa `10.0.2.2:8098`.

## Instalace do televize

**Přes adb v síti** (nejspolehlivější):

1. V TV: Nastavení → Systém → Informace → 7× klepnout na „Sestavení“ (zapne režim vývojáře).
2. Nastavení → Systém → Pro vývojáře → zapnout **Ladění USB** / **Ladění přes síť** (Google TV: „Bezdrátové ladění“).
3. Na počítači ve stejné síti:
   ```bash
   adb connect <ip-televize>:5555        # Google TV s bezdrátovým laděním: adb pair <ip>:<port>, pak adb connect
   adb install -r app-release.apk
   ```
   V TV potvrďte povolení ladění.

**Bez počítače** (sideload): nahrajte APK na web nebo do sdílené složky, v TV nainstalujte aplikaci
*Downloader* (nebo správce souborů, např. *File Commander*), povolte jí „Instalace neznámých aplikací“
a APK otevřete.

Aplikace se objeví na domovské obrazovce jako **Televize**.

## Známá omezení

- Pořady, které ČT na internetu nabízí jen s DRM, aplikace nepřehraje (zobrazí hlášku ČT nebo náhradní obraz ČT).
  Jde o záměr: DRM se neobchází.
- „Zpět v čase“ sahá jen 3 hodiny dozadu; starší pořady jen z archivu, pokud je v něm ČT má.
- ČT :D a ČT art sdílejí kanál; ČT :D proto večer nemá program.
- Budoucí dny průvodce mohou být prázdné, dokud je ČT nezveřejní.
- Časy se zobrazují v pražském čase bez ohledu na časové pásmo zařízení.
- Opětovné vyžádání streamu po vypršení odkazu (např. HTTP 403 během dlouhého sledování) je
  implementované, ale v emulátoru neověřené.
- API ČT je neoficiální a může se kdykoli změnit.

## Upozornění

Neoficiální projekt, není spojen s Českou televizí. ČT, ČT1 … a iVysílání jsou ochranné známky České televize.
