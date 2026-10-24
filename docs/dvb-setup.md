# Anténa (DVB-T2) na Raspberry Pi

Raspberry Pi s tunerem přijímá **volně šířené** (nekódované) programy z vaší antény. Aplikace „Televize“
pak v sekci „Anténa“ nabízí živé vysílání, sledování od začátku a nahrávky posledních 3 dnů
(nastavitelné, nejvýš 14 dnů). Nahrávky zůstávají jen na disku Raspberry, po uplynutí doby se samy smažou
a nedají se stáhnout ani exportovat. Jen pro vaši domácnost (viz [legal-notes.md](legal-notes.md)).

## 1. Co koupit

- **Tuner DVB-T2 s podporou HEVC a Linuxu**, jedna z možností:
  - USB tuner s ovladačem v jádře Linuxu (např. s čipy Silicon Labs Si2168/Si2157, Sony CXD2837/CXD2843);
    před nákupem si ověřte model na [LinuxTV wiki](https://www.linuxtv.org/wiki/) a že umí **DVB-T2**,
    nejen DVB-T.
  - Síťový tuner **HDHomeRun** (verze s DVB-T2): připojí se kabelem do routeru, TVHeadend ho najde sám,
    nepotřebuje ovladače. Dražší, ale nejbezproblémovější, a bývá dvoutunerový.
- **Kolik tunerů**: jeden tuner = jeden multiplex v jednom okamžiku, ale **se všemi jeho kanály**. Všechny
  programy ČT jsou v jednom multiplexu (MUX 21), komerční programy v jiných (Prima, Nova, další). Potřebujete tolik
  tunerů, z kolika multiplexů chcete nahrávat. Nahrávané kanály se „Živě“ pouštějí ze záznamu, takže na sledování
  další tuner nepotřebují; navíc jen tehdy, když chcete sledovat i nenahrávaný kanál z jiného multiplexu.
- **Disk**: změřeno 0,9–1,1 GB za hodinu na kanál (HD v HEVC, všechny zvukové stopy), tedy ~25 GB na kanál za den.
  Kolik dní zpětně se vejde, záleží na disku a počtu kanálů (tabulka níže); když místo dojde, brána maže
  nejstarší záznam rovnoměrně ze všech kanálů.
- Kodéry/CI moduly a karty pro placené programy nejsou potřeba a nepoužívají se: šifrované multiplexy se
  nikdy nedešifrují.

### Hardware podle počtu nahrávaných kanálů

Naměřeno na Raspberry Pi 4 (2 GB) s Raspberry Pi OS 64-bit a tunerem Hauppauge WinTV-dualHD, 2026-10-23. Jeden
nahrávaný kanál stojí:
- **procesor**: průměrně ~12 % jednoho jádra Pi 4 (kanál s jednou zvukovou stopou 6–8 %, kanál se dvěma stopami
  HE-AAC ~22 %). Zvuk DVB-T2 (HE-AAC v LATM) se musí pro přehrávače převést, video se jen kopíruje;
- **paměť**: ~67 MB;
- **disk**: ~1 GB za hodinu.

K tomu TVHeadend (~2 % CPU, ~80 MB) a služba cz-tv-hub (~5 % CPU, ~80 MB). Raspberry má zůstat chladné
(`vcgencmd get_throttled` = `0x0`) a zátěž (`uptime`) pod počtem jader.

| Kanálů | Tunery | Počítač | Disk pro 3 dny zpětně |
|---|---|---|---|
| 1–3 | 1–2 (podle multiplexů) | Raspberry Pi 4, 2 GB | 120 GB ≈ 1,3 dne (3 kanály), 250 GB ≈ 3 dny |
| 4–12 | 2 (dva multiplexy) | Raspberry Pi 4, 4 GB (2 GB na hraně) | 0,3–1 TB (~75 GB na kanál) |
| 12–16 | 2–3 | Raspberry Pi 4 na hraně výkonu → raději Pi 5 | 1–2 TB |
| ~27 (4 multiplexy) | 4 (např. 2× dvojitý USB tuner nebo síťový HDHomeRun se 4 tunery) | Raspberry Pi 5, 8 GB, s aktivním chladičem a zdrojem 27 W, nebo mini PC (např. Intel N100, 16 GB) | 2 TB ≈ 2,7 dne, **4 TB** pro 3 dny |

Odhady pro větší počty kanálů jsou přepočtené z měření tří kanálů; před nákupem je dobré přidávat kanály postupně a
sledovat zátěž. Více USB tunerů a disk připojte přes **USB hub s vlastním napájením**. Na disk stačí i klasický HDD
(zápis 27 kanálů je ~7,5 MB/s). Počet nahrávaných kanálů není omezený; volitelný strop je `max_recordings` v `[dvb]`.

## 2. Instalace

Postup ověřený na Raspberry Pi 4 (2 GB) s Raspberry Pi OS Lite 64-bit (Trixie) a tunerem Hauppauge WinTV-dualHD.

### 2.1 Systém
1. **Raspberry Pi Imager** → zařízení Raspberry Pi 4/5 → *Raspberry Pi OS (other) → Raspberry Pi OS Lite (64-bit)*.
2. *Upravit nastavení*: hostname (např. `cztvhub`), vlastní uživatel a heslo, časové pásmo `Europe/Prague`,
   na kartě Služby zapnout **SSH**. Raspberry připojte do routeru **kabelem**.
3. Z počítače: `ssh <uživatel>@cztvhub.local`, pak `sudo apt update && sudo apt full-upgrade -y && sudo reboot`.
4. Volitelně `sudo dpkg-reconfigure locales` (zaškrtněte `cs_CZ.UTF-8`), jinak SSH z Macu vypisuje varování
   `setlocale: LC_CTYPE`. Neškodná.

### 2.2 Tuner
Zapojte tuner s anténou a ověřte, že ho Linux vidí:

```bash
ls /dev/dvb                                   # adapter0 (u dvojitého tuneru i adapter1)
dmesg | grep -i -E "dvb|si21|firmware" | tail -20
```

**Tunery s čipem Silicon Labs Si2168-D60** (Hauppauge WinTV-soloHD/dualHD, PCTV 292e, MyGica T230C…) potřebují
pro DVB-T2 soubor firmwaru `dvb-demod-si2168-d60-01.fw`, který Debian ani Raspberry Pi OS nemají. Bez něj TVHeadend
multiplexy najde, ale všechny skončí `FAIL` a v `dmesg` je `firmware file 'dvb-demod-si2168-d60-01.fw' not found`
(objeví se až při prvním ladění). Instalace z repozitáře LibreELEC s kontrolou součtu:

```bash
cd /tmp
curl -fsSLO https://raw.githubusercontent.com/LibreELEC/dvb-firmware/master/firmware/dvb-demod-si2168-d60-01.fw
echo "5e1c860e95a8d92b2716b0075dd00e55f99c2c368e35340f6b527c366ff27ee7  dvb-demod-si2168-d60-01.fw" | sha256sum -c - \
  && sudo install -m 0644 dvb-demod-si2168-d60-01.fw /lib/firmware/ && sudo systemctl restart tvheadend
```

Po dalším ladění má `dmesg | grep si2168` hlásit `downloading firmware from file …` a `firmware version: D 6.0.13`.
Firmware se nahrává jen do paměti čipu při každém zapnutí; tuner se trvale nemění.

### 2.3 cz-tv-hub a TVHeadend

```bash
sudo apt install -y git
git clone https://github.com/martinno027/cz-tv-hub.git
cd cz-tv-hub
bash server/deploy/install-dvb.sh
```

Skript:
- nainstaluje `ffmpeg` a `avahi-daemon` a **TVHeadend z oficiálního repozitáře projektu TVHeadend** (Cloudsmith),
  protože Debian ani Raspberry Pi OS ho v balíčcích nemají; podporované jsou Raspberry Pi OS 64-bit Bookworm a Trixie.
  Při instalaci se TVHeadend zeptá na jméno a heslo **správce** – zvolte si je a poznamenejte (slouží pro webové
  rozhraní, nejsou to údaje uživatele `cztvhub`),
- nastaví TVHeadend tak, aby poslouchal **jen na 127.0.0.1** (`--bindaddr 127.0.0.1` v proměnné `OPTIONS` souboru
  `/etc/default/tvheadend`) a ověří to,
- vytvoří `/etc/cztvhub/cztvhub.conf` (práva 0600) s náhodným **klíčem pro aplikace** a heslem pro
  uživatele TVHeadendu `cztvhub`; obojí vypíše, poznamenejte si je,
- nainstaluje a spustí službu `cz-tv-hub` (port 8098),
- zapne ohlašování v domácí síti (mDNS `_cztvhub._tcp`) **jen na rozhraní domácí sítě**, na Tailscale ne
  (`allow-interfaces` / `deny-interfaces=tailscale0` v `/etc/avahi/avahi-daemon.conf`).

Kontrola: `sudo ss -ltnp | grep 998` musí ukázat `127.0.0.1:9981` a `127.0.0.1:9982`, ne `*:9981`.

### 2.4 Disk na nahrávky
Nahrává se na USB disk (SSD), ne na SD kartu (ta by se rychle zaplnila a opotřebovala). Disk naformátujte
a připojte natrvalo do `/srv/tv` (pozor, **formátování smaže vše, co na disku je**; `sda` ověřte příkazem `lsblk`):

```bash
lsblk                                            # najděte USB disk, např. sda
sudo mkfs.ext4 -L tv /dev/sda1                   # oddíl disku (případně nejdřív vytvořte: sudo fdisk /dev/sda)
sudo mkdir -p /srv/tv
echo 'LABEL=tv /srv/tv ext4 defaults,noatime,nofail 0 2' | sudo tee -a /etc/fstab
sudo mount /srv/tv && df -h /srv/tv
sudo install -d -o cztvhub -g cztvhub /srv/tv/buffer
```

V `/etc/cztvhub/cztvhub.conf` v sekci `[dvb]` nastavte `buffer_dir = /srv/tv/buffer` a restartujte službu
(`sudo systemctl restart cztvhub`). Služba smí zapisovat pod `/srv/tv` (je to v `cztvhub.service`).

Pokud chcete dočasné soubory pro přehrávání na velkém disku, změňte `hls_dir` v konfiguraci a tutéž cestu
doplňte do `ReadWritePaths=` v `/etc/systemd/system/cztvhub.service`.

## 3. První ladění v TVHeadendu

Webové rozhraní TVHeadendu je dostupné jen přímo na Raspberry. Z počítače se k němu dostanete přes SSH
tunel (okno nechte otevřené):

```bash
ssh -L 9981:127.0.0.1:9981 <uživatel>@cztvhub.local
```

a v prohlížeči otevřete `http://127.0.0.1:9981`. Po restartu Raspberry je potřeba tunel otevřít znovu.

**Průvodce prvním spuštěním** (jde spustit znovu v Configuration → General → Base → *Start wizard*, ale přidal by
síť a multiplexy podruhé):
1. Jazyk čeština.
2. *Access Control*: **Allowed network `127.0.0.1/32`** (prázdné pole = přístup odkudkoli), jméno a heslo
   správce, a jako běžného uživatele **`cztvhub`** s heslem z instalace.
3. *Tuner and Network*: u tunerů typu **DVB-T** zvolte *DVB-T Network* (DVB-T pokrývá i DVB-T2; u více tunerů
   všechny do stejné sítě). Řádky **IPTV** a **DVB-C** nechte bez sítě – kabel ani IPTV nepotřebujete a stejný
   fyzický tuner by se o ně přetahoval.
4. *Predefined Muxes*: **Czech Republic: cz-All** (prohledá všechny kanály, trvá déle).
5. *Scanning*: počkejte na konec. Výsledek „0 services“ u všech multiplexů = obvykle chybějící firmware (2.2),
   ne anténa. Po doplnění firmwaru v Configuration → DVB Inputs → Muxes označte vše → *Edit* → *Scan status*
   `PEND`. Funkční multiplexy mají *Scan result* `OK` (typicky 4 celoplošné + regionální).
6. *Service Mapping*: když jsou služby, **Map all services**; jinak později v Configuration → DVB Inputs →
   Services → *Map services → Map all services*.

**Po průvodci:**
1. Configuration → Users → Access Entries → `cztvhub`: povolená síť `127.0.0.1/32`, práva *Web interface*,
   **Admin**, *Streaming* a *Video recorder*. Bez *Admin* brána vidí kanály, ale `status` hlásí
   `"tuners": []` a `"tvheadendReachable": false`.
2. Configuration → General → Base: *HTTP authentication* nastavte na „Both plain and digest“.
3. Configuration → Channel / EPG → Channels: zkontrolujte kanály, duplicitní (např. Nova a Nova Cinema jsou ve
   dvou multiplexech) vypněte (*Enabled*, nebo pohodlněji ve správě kanálů, kapitola 4a), případně jim dejte čísla – aplikace řadí podle čísla, pak abecedy.
4. Nic s CA/CI/descramblery nenastavujte. Kanály šifrovaných multiplexů můžete vypnout.

**Kontrola brány** na Raspberry (klíč se načte z konfigurace a nevypíše):

```bash
KEY=$(sudo sed -n 's/^key *= *\([^ ;]*\).*/\1/p' /etc/cztvhub/cztvhub.conf | head -1)
curl -s -H "Authorization: Bearer $KEY" http://127.0.0.1:8098/dvb/status; echo     # tunery, "tvheadendReachable": true
curl -s -H "Authorization: Bearer $KEY" http://127.0.0.1:8098/dvb/channels | head -c 600; echo
```

Hned po startu může být každý tuner `"inUse": true` – TVHeadend stahuje programový průvodce (EPG); za pár minut
se uvolní.

**Ladění signálu bez TVHeadendu** (když ani po firmwaru nic nechytá): zastavte TVHeadend
(`sudo systemctl stop tvheadend`), `sudo apt install -y dvb-tools` a projděte kanály 21–48 v DVB-T2:

```bash
for ch in $(seq 21 48); do
  printf '[CH%s]\n\tDELIVERY_SYSTEM = DVBT2\n\tFREQUENCY = %s\n\tBANDWIDTH_HZ = 8000000\n\tSTREAM_ID = 0\n\n' \
    "$ch" "$(( (306 + 8*ch) * 1000000 ))"
done > /tmp/cz-t2.conf
dvbv5-scan -a 0 /tmp/cz-t2.conf 2>&1 | grep -E "Lock|Service" | head -60
sudo systemctl start tvheadend
```

## 4. Nahrávání

Nahrávání se zapíná ve **webové administraci** (kapitola 4a), u každého kanálu přepínačem **Nahrávat**. Zapnutý
kanál se nahrává **nepřetržitě** na disk Raspberry (po 10minutových blocích); pořady v aplikacích jsou úseky tohoto
záznamu, takže nic nechybí ani na hranicích pořadů a „Živě“ se u nahrávaného kanálu pouští přímo ze záznamu
(o pár vteřin později, bez dalšího tuneru). Ukládají se všechny zvukové stopy (čeština, původní znění, komentář
pro nevidomé); v aplikacích je výběr zvuku v přehrávači. Titulky se zatím neukládají.

Nahraný pořad se v aplikacích přehrává **v okně záznamu kanálu**: od 3 hodin před začátkem pořadu do 3 hodin po jeho
konci (nejvýš 12 hodin). Jde tak dojet před začátek podle programu (časy v programu často nesedí) i do sousedních
pořadů; u kraje okna aplikace sama otevře další, takže se dá projít celý uložený záznam. Na časové ose je skutečné
datum a čas vysílání. Jeden playlist přes celý záznam kanálu brána nenabízí a nic z toho nejde stáhnout.

- **Uchovávat nahrávky nejvýše** 1–14 dní (výchozí `retention_days` z konfigurace). Starší záznam se smaže vždy.
- **Když dochází místo**: na disku zůstává rezerva 10 % (nejméně 5 GB). Když by volné místo kleslo pod ni, smaže se
  nejstarších 10 minut z kanálu, který má uloženo nejvíc hodin – každý nahrávaný kanál tak drží zhruba stejný
  počet posledních hodin (na malém disku a s mnoha kanály třeba jen posledních 5 hodin z každého). Když rezervu
  nejde udržet, nahrávání se pozastaví a v administraci je to vidět.
- **Přehled nahrávek** (záložka v administraci): pro každý kanál kolik hodin je uloženo, jak daleko zpět, velikost,
  skutečný datový tok (GB/h, změřený z nahrávek) a výpadky; nahoře disk, rezerva a odhad, kolik hodin na kanál se
  vejde.
- Vypnutím „Nahrávat“ nebo skrytím kanálu se jeho záznam **hned smaže**. Kódované kanály nahrávat nejde.
- Dva tunery = kanály nejvýš ze dvou multiplexů najednou (všechny kanály jednoho multiplexu nahraje jeden tuner).
- **Kolik kanálů najednou**: zvuk ve vysílání DVB-T2 (HE-AAC v LATM) se musí pro přehrávače převést, což stojí výkon
  procesoru u každé zvukové stopy; na Raspberry Pi 4 asi 20 % jednoho jádra na kanál. Počet není omezený, hlídejte
  zátěž (`uptime`): při přetížení přestane odpovídat i TVHeadend. Volitelný strop je `max_recordings` v `[dvb]`.
- Nahrávání nejde zapnout, dokud `buffer_dir` leží na systémové SD kartě – nejdřív disk podle kapitoly 2.4.

Nahrávání je soukromá kopie jen pro vaši domácnost: záznam zůstává na disku Raspberry, maže se automaticky a nedá
se stáhnout ani sdílet (viz [legal-notes.md](legal-notes.md)).

Starší způsob – TVHeadend nahrává po jednotlivých pořadech podle pravidel z `dvb-setup` – dál funguje pro instalace,
které ho nastavily; s nepřetržitým nahráváním ho nekombinujte (`dvb-setup` nespouštějte).

## 4a. Správa kanálů (webová administrace)

Na adrese `http://cztvhub.local:8098/admin` (z domácí sítě, mimo domov přes Tailscale) je stránka, kde vypínáte
kanály, které nechcete. Přihlášení: jméno **admin**, heslo `password` ze sekce `[admin]` v
`/etc/cztvhub/cztvhub.conf` (instalace ho vygeneruje a vypíše). Starší instalace sekci nemají – doplňte ji ručně
(heslo aspoň 12 znaků) a restartujte službu:

```bash
printf '\n[admin]\npassword = %s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')" \
  | sudo tee -a /etc/cztvhub/cztvhub.conf && sudo systemctl restart cztvhub
sudo grep -A1 '^\[admin\]' /etc/cztvhub/cztvhub.conf     # heslo
```

Vypnutý kanál:
- aplikace „Televize“ ho nenabízejí (ani v programu, ani jeho nahrávky),
- nenahrává se: plánované nahrávky se zruší a dosavadní nahrávky kanálu se smažou,
- tuner ho neladí: v TVHeadendu se vypne kanál i jeho služba, a když v multiplexu nezbude žádný zapnutý kanál,
  vypne se celý multiplex (tuner na něj vůbec neladí, nestahuje ani jeho program).

Opětovné zapnutí vše vrátí; nahrává se od dalšího pořadu a program se doplní, až tuner multiplex znovu naladí.
Více kanálů najednou vypnete nebo zapnete zaškrtnutím a tlačítkem nahoře (jedno potvrzení).

Nad seznamem jsou **filtry**: hledání v názvu (najde i původní název, nezáleží na velikosti písmen ani diakritice –
„ct“ najde „ČT1“), jen zapnuté / vypnuté, jen nahrávané / nenahrávané a multiplex. Filtry se kombinují, stránka ukazuje
„N z M kanálů“ a filtr zůstane i po obnovení stránky (je v adrese). Hromadné akce i „Zkrátit názvy“ se týkají jen
zobrazených kanálů.

**Názvy kanálů**: TVHeadend přebírá názvy z vysílání („CT 1 PHA HD T2“). Tlačítko **Zkrátit názvy** navrhne
televizní názvy („ČT1“, „ČT24“, „Nova“) a po potvrzení je uloží; jednotlivě je přejmenujete odkazem *Přejmenovat*.
Názvy se uloží v TVHeadendu a aplikace je ukážou po obnovení. U přejmenovaného kanálu je pod názvem drobně
vidět původní název z vysílání. Máte-li v konfiguraci `record_channels` nebo
`[ads] channels` vypsané názvy kanálů, upravte je na nové.
Stejně se chová kanál vypnutý ručně v TVHeadendu (*Enabled*).

## 5. Vzdálený přístup: jen vaše domácnost přes Tailscale

- Port 8098 **nikdy nepřesměrovávejte na routeru** a nepoužívejte Tailscale Funnel ani sdílení zařízení
  s jinými účty. Jiná domácnost (např. příbuzní na chalupě) potřebuje vlastní tuner.
- V Tailscale (Access controls) povolte přístup k Raspberry jen členům domácnosti, např.:

```json
{
  "tagOwners": {"tag:tv": ["autogroup:admin"]},
  "acls": [
    {"action": "accept", "src": ["group:domacnost"], "dst": ["tag:tv:8098"]}
  ],
  "groups": {"group:domacnost": ["clen1@example.com", "clen2@example.com"]}
}
```

## 6. Nastavení aplikace „Televize“

V nastavení aplikace zadejte:
- **Adresu**: v domácí síti ji aplikace najde sama (Bonjour), jinak `http://<jméno-raspberry>:8098`
  (mimo domov jméno z Tailscale MagicDNS, např. `http://raspberrypi:8098`),
- **Klíč**: hodnotu `key` z `/etc/cztvhub/cztvhub.conf` (`sudo grep ^key /etc/cztvhub/cztvhub.conf`).

Bez nastavení je skupina „Anténa“ skrytá.

**Profily** (Nastavení → Profil): každý člen domácnosti si může na Raspberry uložit své nastavení kanálů (pořadí,
skryté kanály, zapnuté zdroje) a přepínač náhledů pod jménem a heslem a načíst ho na kterékoli televizi
přihlášením. Přihlášená televize ukládá každou změnu hned do profilu a zůstane přihlášená, dokud ji někdo
neodhlásí; po odhlášení platí zase vlastní nastavení televize. Pozice „Pokračovat“ zůstávají v televizi. V
administraci je záložka **Profily**: seznam, nový profil, nové heslo a smazání (obojí odhlásí všechny televize
s tím profilem). Heslo aspoň 4 znaky; na Raspberry je uložené jen jako otisk (PBKDF2), televize si ho neukládá.

**Výchozí kanály domácnosti**: v administraci na záložce **Výchozí kanály** nastavíte pořadí, skryté kanály
a zapnuté zdroje, které se ukážou na každé televizi bez přihlášeného profilu, kde nikdo kanály neměnil. Nejrychleji
u svého profilu v záložce **Profily** tlačítkem **Použít jako výchozí**: zkopíruje se jeho současný seznam kanálů
(jednou; pozdější změny profilu výchozí nezmění). Televize, na které si někdo kanály přeházel, si svůj seznam
nechá; **Obnovit výchozí pořadí** v Nastavení → Kanály ji vrátí na výchozí domácnosti. **Zrušit výchozí** vrátí
původní pořadí aplikace.

## Řešení potíží

- `curl -H "Authorization: Bearer <klíč>" http://127.0.0.1:8098/dvb/status` na Raspberry ukáže tunery,
  volné místo a zda je TVHeadend dostupný.
- „Tuner je obsazený“: všechny tunery nahrávají nebo vysílají jiný multiplex.
- Logy: `journalctl -u cztvhub -f` a `journalctl -u tvheadend -f`.
- Sekání nebo kolečko načítání u Antény: během sledování kanálu na televizi spusťte na Raspberry
  `sudo -u cztvhub env PYTHONPATH=/opt/cztvhub/src python3 -m cztvhub dvb-diag --config /etc/cztvhub/cztvhub.conf --channel "ČT1"`
  (název kanálu jako v aplikaci). Za 2 minuty vypíše chyby příjmu z tuneru, pravidelnost úseků a kolikrát by se
  přehrávač zasekl.
