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

Na Raspberry (Raspberry Pi OS), v adresáři s tímto repozitářem, s připojeným tunerem a anténou:

```bash
bash server/deploy/install-dvb.sh
```

Skript:
- nainstaluje `tvheadend`, `ffmpeg` a `avahi-daemon`,
- nastaví TVHeadend tak, aby poslouchal **jen na 127.0.0.1** (z ostatních počítačů není vidět),
- vytvoří `/etc/cztvhub/cztvhub.conf` (práva 0600) s náhodným **klíčem pro aplikace** a heslem pro
  uživatele TVHeadendu `cztvhub`; obojí vypíše, poznamenejte si je,
- nainstaluje a spustí službu `cz-tv-hub` (port 8098),
- zapne ohlašování v domácí síti (mDNS `_cztvhub._tcp`) **jen na rozhraní domácí sítě**, na Tailscale ne
  (`allow-interfaces` / `deny-interfaces=tailscale0` v `/etc/avahi/avahi-daemon.conf`).

Pokud chcete dočasné soubory pro přehrávání na velkém disku, změňte `hls_dir` v konfiguraci a tutéž cestu
doplňte do `ReadWritePaths=` v `/etc/systemd/system/cztvhub.service`.

## 3. První ladění v TVHeadendu

Webové rozhraní TVHeadendu je dostupné jen přímo na Raspberry. Z počítače se k němu dostanete přes SSH
tunel:

```bash
ssh -L 9981:127.0.0.1:9981 pi@<adresa-raspberry>
```

a v prohlížeči otevřete `http://127.0.0.1:9981`. Pak:

1. Průvodce prvním spuštěním: jazyk čeština, síť **DVB-T**, předladěné multiplexy **Czech Republic**
   (nebo ruční přidání multiplexů DVB-T2 podle mapy pokrytí na digitalnitelevize.cz),
   prohledat, „Map all services“.
2. Configuration → Users → Access Entries a Passwords: vytvořte uživatele **`cztvhub`** s heslem
   z instalace, povolená síť `127.0.0.1/32`, práva *Web interface*, *Admin*, *Streaming* a
   *Video recorder* (Admin je potřeba pro stav tunerů a pro `dvb-setup`).
3. Configuration → General → Base: *HTTP authentication* nastavte na „Both plain and digest“.
4. Ověřte, že v Configuration → Channel/EPG jsou kanály a po pár minutách i programový průvodce (EPG).
5. Nic s CA/CI/descramblery nenastavujte. Kanály šifrovaných multiplexů můžete vypnout.

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
