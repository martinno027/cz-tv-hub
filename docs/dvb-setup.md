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
- **Kolik tunerů**: jeden tuner = jeden multiplex v jednom okamžiku. Všechny programy ČT jsou v jednom
  multiplexu (MUX 21), komerční programy v jiných. Chcete-li nahrávat programy ze dvou multiplexů současně,
  potřebujete dva tunery, a ještě jeden navíc, pokud chcete mezitím sledovat třetí multiplex.
- **Disk**: HD program v HEVC má asi 1–1,8 GB za hodinu, tedy 70–130 GB na program za 3 dny.
  Pro ~15 programů stačí USB SSD/HDD 2 TB. Méně místa = v nastavení omezte `record_channels`.
- Kodéry/CI moduly a karty pro placené programy nejsou potřeba a nepoužívají se: šifrované multiplexy se
  nikdy nedešifrují.

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
Připojte USB disk natrvalo (např. do `/srv/tv`, záznam v `/etc/fstab` podle UUID), vytvořte
`/srv/tv/recordings` s vlastníkem `hts` (`sudo install -d -o hts -g video /srv/tv/recordings`) a v TVHeadendu
(Configuration → Recording → Digital Video Recorder Profiles → výchozí profil) nastavte *Recording system path* na
tuto složku. Jinak se nahrává na SD kartu, která se rychle zaplní a opotřebuje. Nahrávání (kapitola 4) zapínejte
až s diskem.

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
   dvou multiplexech) vypněte (*Enabled*), případně jim dejte čísla – aplikace řadí podle čísla, pak abecedy.
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

Jednou po ladění (a vždy po změně `retention_days` nebo `record_channels`):

```bash
sudo -u cztvhub env PYTHONPATH=/opt/cztvhub/src python3 -m cztvhub dvb-setup --config /etc/cztvhub/cztvhub.conf
```

Příkaz je bezpečné spouštět opakovaně. Nastaví v TVHeadendu dobu uchování nahrávek a pro každý vybraný kanál
jedno pravidlo automatického nahrávání (komentář „cztvhub“), které nahrává všechny pořady. Volba `--dry-run`
jen vypíše, co by změnil. Služba cz-tv-hub navíc každou hodinu maže nahrávky starší než `retention_days`.

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

## Řešení potíží

- `curl -H "Authorization: Bearer <klíč>" http://127.0.0.1:8098/dvb/status` na Raspberry ukáže tunery,
  volné místo a zda je TVHeadend dostupný.
- „Tuner je obsazený“: všechny tunery nahrávají nebo vysílají jiný multiplex.
- Logy: `journalctl -u cztvhub -f` a `journalctl -u tvheadend -f`.
