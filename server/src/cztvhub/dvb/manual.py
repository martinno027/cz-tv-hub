"""The connection manual from ``/admin`` (spec/dvb-gateway.md "Connection manual from /admin", T49).

``gather()`` collects what the manual needs into a small ``ManualInfo`` (addresses, the app key, Tailscale, HTTPS,
version); ``manual_text()`` writes the Czech plain text from it and does nothing else, so the tests check the text
without a network, a tailnet or a certificate. Only the app key ``[dvb] key`` is ever put into ``ManualInfo``:
never the admin password, the worker key, a TMDb key or the TVHeadend password.
"""
from __future__ import annotations

import ipaddress
import json
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import tls
from ..config import MIN_KEY_LENGTH, Config
from .epg import PRAGUE

FILENAME = "televize-pripojeni.txt"
TAILSCALE_TIMEOUT = 5.0
TAILSCALE_MAX_BYTES = 4 * 1024 * 1024
# the pattern cert.sh accepts for the MagicDNS name (lowercase labels, at least two)
DNS_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
HOST_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
# a documentation address: connect() on a UDP socket only picks the route's source address, it sends nothing
ROUTE_PROBE = ("192.0.2.1", 9)


@dataclass
class Tailnet:
    dns_name: str  # cztvhub.tail1234.ts.net (no trailing dot)
    ips: list[str] = field(default_factory=list)


@dataclass
class ManualInfo:
    generated: str  # "2. 11. 2026 14:05" (Prague)
    version: str | None  # short commit of the last install, None = unknown
    key: str  # [dvb] key, the app key
    port: int
    lan_name: str | None  # cztvhub.local
    lan_ips: list[str]
    tailnet: Tailnet | None
    tailnet_reason: str | None  # why Tailscale was not found (Czech), when tailnet is None
    https: bool  # the certificate pair of tls.py is there
    https_port: int = tls.HTTPS_PORT


# -- gathering ---------------------------------------------------------------------------------------

def mdns_name(hostname: str) -> str | None:
    """``<hostname>.local`` as avahi advertises it; None for a name avahi would not use as is."""
    label = hostname.strip().split(".")[0].lower()
    return f"{label}.local" if HOST_LABEL_RE.match(label) else None


def _lan_v4(value: str) -> str | None:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return None
    # is_private leaves out Tailscale's 100.64.0.0/10 (shared address space, not private; the test checks it)
    if ip.version != 4 or ip.is_loopback or ip.is_link_local or not ip.is_private:
        return None
    return str(ip)


def lan_addresses(hostname: str) -> list[str]:
    """Private IPv4 addresses of this machine on the LAN (stdlib only, no packet sent)."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(ROUTE_PROBE)
            found.append(sock.getsockname()[0])
    except OSError:
        pass
    try:
        found += [info[4][0] for info in socket.getaddrinfo(hostname, None, socket.AF_INET)]
    except (OSError, UnicodeError):
        pass
    out: list[str] = []
    for value in found:
        ip = _lan_v4(value)
        if ip is not None and ip not in out:
            out.append(ip)
    return out


def parse_tailscale_status(raw: bytes) -> tuple[Tailnet | None, str | None]:
    """``tailscale status --json`` → (Tailnet, None) or (None, Czech reason). Only BackendState, Self.DNSName and
    Self.TailscaleIPs are read; anything else in the answer is ignored, an unexpected value stays unknown."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None, "tailscale status neodpověděl ve formátu JSON"
    if not isinstance(data, dict):
        return None, "tailscale status neodpověděl ve formátu JSON"
    state = data.get("BackendState")
    if state not in (None, "Running"):
        shown = state if isinstance(state, str) and state.isalnum() and len(state) <= 32 else "neznámý"
        return None, f"Tailscale neběží (stav {shown}); na Raspberry: sudo tailscale up"
    me = data.get("Self")
    name = me.get("DNSName") if isinstance(me, dict) else None
    name = name.rstrip(".") if isinstance(name, str) else ""
    if not DNS_NAME_RE.match(name):
        return None, "Raspberry nemá jméno MagicDNS (v administraci Tailscale zapněte DNS → MagicDNS)"
    ips: list[str] = []
    raw_ips = me.get("TailscaleIPs") if isinstance(me, dict) else None
    for value in raw_ips if isinstance(raw_ips, list) else []:
        try:
            ip = ipaddress.ip_address(value) if isinstance(value, str) else None
        except ValueError:
            ip = None
        if ip is not None and str(ip) not in ips:
            ips.append(str(ip))
    return Tailnet(name, ips), None


def tailscale_status(run: Callable = subprocess.run, which: Callable = shutil.which) -> tuple[Tailnet | None, str | None]:
    """Run ``tailscale status --json`` (timeout, output cap); never raises."""
    binary = which("tailscale")
    if binary is None:
        return None, "na Raspberry není nainstalovaný Tailscale (docs/dvb-setup.md, kapitola 5)"
    try:
        done = run([binary, "status", "--json"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                   stderr=subprocess.DEVNULL, timeout=TAILSCALE_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return None, "tailscale status neodpověděl do 5 s"
    except OSError as exc:
        return None, f"tailscale status nejde spustit ({exc.strerror or type(exc).__name__})"
    out = done.stdout or b""
    if len(out) > TAILSCALE_MAX_BYTES:
        return None, "tailscale status odpověděl příliš dlouhým výstupem"
    if done.returncode != 0:
        # a logged-out node may still answer JSON with its BackendState: that reason is the useful one
        _, reason = parse_tailscale_status(out)
        if reason is not None and reason.startswith("Tailscale neběží"):
            return None, reason
        return None, f"tailscale status skončil chybou {done.returncode}; na Raspberry: tailscale status"
    return parse_tailscale_status(out)


def gather(cfg: Config, version: dict | None, now: Callable[[], float] = time.time,
           hostname: Callable[[], str] = socket.gethostname, lan: Callable[[str], list] = lan_addresses,
           tailscale: Callable[[], tuple] = tailscale_status, tls_dir: Path = tls.TLS_DIR,
           https_port: int = tls.HTTPS_PORT) -> ManualInfo:
    """What the manual needs; ``version`` is ``Updater.version()``. Only ``cfg.dvb.key`` is taken from the secrets."""
    host = hostname()
    tailnet, reason = tailscale()
    commit = (version or {}).get("commit")
    return ManualInfo(
        generated=_when(now()), version=commit[:12] if isinstance(commit, str) else None, key=cfg.dvb.key,
        port=cfg.port, lan_name=mdns_name(host), lan_ips=lan(host), tailnet=tailnet,
        tailnet_reason=None if tailnet is not None else reason,
        https=tls.TlsFiles(tls_dir, log=lambda m: None).present(), https_port=https_port)


def _when(moment: float) -> str:
    t = datetime.fromtimestamp(moment, PRAGUE)
    return f"{t.day}. {t.month}. {t.year} {t.hour:02d}:{t.minute:02d}"


# -- the text ------------------------------------------------------------------------------------------

def _url(host: str, port: int, path: str = "", scheme: str = "http") -> str:
    return f"{scheme}://{host}:{port}{path}"


def manual_text(info: ManualInfo) -> str:
    """The manual (Czech, LF line ends, ends with a newline). Pure: everything comes from ``info``."""
    lan_hosts = ([info.lan_name] if info.lan_name else []) + info.lan_ips
    home = lan_hosts[0] if lan_hosts else None
    tn = info.tailnet
    lines: list[str] = []
    add = lines.append

    add("POZOR: tento soubor obsahuje klíč brány. Dejte ho jen tomu, kdo má mít přístup k televizi z Raspberry.")
    add("")
    add("Televize – návod pro připojení k bráně cz-tv-hub")
    add("=" * 48)
    add(f"Vytvořeno: {info.generated}")
    add(f"Verze brány: {info.version or 'neznámá'}")
    add("")

    add("1. Adresy a klíč")
    add("-" * 16)
    add("Doma (domácí síť):")
    if lan_hosts:
        for host in lan_hosts:
            add(f"  {_url(host, info.port)}")
        if info.lan_name and info.lan_ips:
            add(f"  (když jméno {info.lan_name} nefunguje, použijte IP adresu)")
    else:
        add(f"  adresu se nepodařilo zjistit; použijte IP adresu Raspberry v domácí síti a port {info.port}")
    add("")
    add("Mimo domov (Tailscale):")
    if tn is not None:
        add(f"  jméno: {tn.dns_name}  ->  {_url(tn.dns_name, info.port)}")
        if tn.ips:
            add(f"  IP v Tailscale: {', '.join(tn.ips)}")
        add("  Funguje jen na zařízení, které je přihlášené do stejného tailnetu (viz níže).")
    else:
        add(f"  Tailscale na Raspberry nebylo zjištěno ({info.tailnet_reason or 'důvod neznámý'}).")
        add("  Varianty „mimo domov“ níže fungují, až správce Tailscale na Raspberry nastaví (docs/dvb-setup.md, kap. 5).")
    add("")
    add("Web jako aplikace (HTTPS):")
    if info.https and tn is not None:
        add(f"  {_url(tn.dns_name, info.https_port, '/web/', 'https')}")
    elif info.https:
        add(f"  HTTPS je zapnuté na portu {info.https_port}; celé jméno Raspberry ukáže na Raspberry `tailscale status`:")
        add(f"  https://<jméno>.<tailnet>.ts.net:{info.https_port}/web/")
    else:
        add("  HTTPS je vypnuté. Zapne ho správce: v administraci Tailscale DNS → HTTPS Certificates (jednou),")
        add("  pak na Raspberry `sudo systemctl start cztvhub-cert.service`. Do té doby web běží na http:// níže.")
    add("")
    add("Klíč brány (stejný pro všechny televize a web):")
    add(f"  {info.key}")
    add("")

    def address_hint() -> None:
        if home:
            add(f"     - doma: {home}" + (f" (nebo {lan_hosts[1]})" if len(lan_hosts) > 1 else ""))
        else:
            add(f"     - doma: IP adresa Raspberry v domácí síti (port {info.port} se doplní sám)")

    add("2. Apple TV a Android TV – aplikace Televize")
    add("-" * 44)
    add("  1. Otevřete Nastavení → Server.")
    add("  2. Adresa brány (doma ji aplikace obvykle najde sama, „Nalezeno v domácí síti“):")
    address_hint()
    if tn is not None:
        add(f"     - mimo domov: {tn.dns_name}")
    add("  3. Klíč brány (na Android TV jen „Klíč“): klíč z kapitoly 1. Tlačítko Zobrazit ukáže, co jste napsali.")
    add("  4. Uložit a ověřit. Objeví se „Připojeno …“ a v aplikaci přibude skupina Anténa.")
    add("  Mimo domov – Tailscale přímo v aplikaci (bez aplikace Tailscale):")
    add("  5. Jako adresu brány zadejte jméno z Tailscale" + (f" ({tn.dns_name})." if tn is not None else "."))
    add("  6. Zapněte „Připojovat přes Tailscale“. Naskenujte QR kód telefonem a přihlaste se účtem, který je")
    add("     v tailnetu správce (správce vás pozve). Hotovo, když se ukáže „Připojeno jako televize-…“.")
    add("     Bez telefonu: správce v administraci Tailscale (Settings → Keys) vytvoří jednorázový auth key")
    add("     (tskey-auth-…); zadejte ho v „Přihlásit klíčem“. Televize si ho neukládá.")
    add("     „Televize čeká na schválení v administraci Tailscale“: správce ji schválí v Machines.")
    add("  7. Uložit a ověřit.")
    add("")

    add("3. Samsung TV – aplikace Televize")
    add("-" * 33)
    add("  Jen doma (Samsung Tailscale v aplikaci neumí).")
    add("  1. Nastavení → Server.")
    add("  2. Adresa brány:")
    address_hint()
    add("  3. Klíč brány: klíč z kapitoly 1.")
    add("  4. Uložit a ověřit.")
    add("")

    add("4. Web v prohlížeči (počítač, tablet, telefon)")
    add("-" * 46)
    add("  Doma:")
    if home:
        add(f"    {_url(home, info.port, '/web/')}")
    else:
        add(f"    http://<IP Raspberry>:{info.port}/web/")
    add("  Mimo domov (přes Tailscale, funguje i doma):")
    add("    Na tom zařízení nainstalujte aplikaci Tailscale a přihlaste se účtem z tailnetu správce, pak otevřete")
    add(f"    {_url(tn.dns_name, info.port, '/web/')}" if tn is not None else "    adresu se jménem z Tailscale (zatím nezjištěno, viz kapitola 1)")
    add("  Pak v Nastavení → Server zadejte Klíč brány a dejte Uložit a ověřit. Adresa se nezadává.")
    add("  Prohlížeč si klíč pamatuje pro každou adresu zvlášť.")
    add("")
    add("  Jako aplikace ve vlastním okně (HTTPS):")
    if info.https:
        add("    S puštěným Tailscale otevřete " + (_url(tn.dns_name, info.https_port, "/web/", "https") if tn is not None
                                                  else f"https://<jméno>.<tailnet>.ts.net:{info.https_port}/web/"))
        add("    Chrome nebo Edge: ⋮ → Nainstalovat (nebo ikona instalace v adresním řádku).")
        add("    Safari (Mac): Soubor → Přidat do Docku; iPhone/iPad: Sdílet → Přidat na plochu.")
        add("    Na této adrese zadejte klíč ještě jednou.")
    else:
        add("    Zatím ne: HTTPS je vypnuté (kapitola 1). Vlastní okno bez lišty jde i na http://:")
        add("    Chrome ⋮ → Uložit a sdílet → Vytvořit zástupce → Otevřít jako okno; Safari Soubor → Přidat do Docku.")
    add("")

    add("5. Kodi")
    add("-" * 7)
    add("  Doplněk „Televize – živě a zpětně“ (ČT živě, zpětně a TV průvodce) bránu ani klíč nepotřebuje:")
    add("  připojuje se k ČT sám, i mimo domov. ZIP doplňku připraví správce (návod docs/kodi-setup.md).")
    add("  Starší cesta jen doma, jen živé ČT, bez klíče – seznam kanálů pro PVR IPTV Simple Client")
    add("  (Umístění: Vzdálená cesta):")
    playlist_host = info.lan_ips[0] if info.lan_ips else home  # the IP, as docs/kodi-setup.md has it
    add(f"    {_url(playlist_host, info.port, '/playlist.m3u')}" if playlist_host
        else f"    http://<IP Raspberry>:{info.port}/playlist.m3u")
    add("")
    add(f"Když to nejde: zkontrolujte adresu a klíč ({MIN_KEY_LENGTH} a více znaků, bez mezer) a že jste doma")
    add("nebo v Tailscale.")
    return "\n".join(lines) + "\n"
