"""Server configuration (INI file, see spec/dvb-gateway.md). Without a file the server runs CT-only."""
from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path

MIN_KEY_LENGTH = 32
MIN_ADMIN_PASSWORD_LENGTH = 12
RETENTION_MIN, RETENTION_MAX, RETENTION_DEFAULT = 1, 14, 3


class ConfigError(Exception):
    pass


@dataclass
class TvheadendConfig:
    url: str = "http://127.0.0.1:9981"
    user: str = ""
    password: str = ""


@dataclass
class DvbConfig:
    enabled: bool = False
    key: str = ""
    retention_days: int = RETENTION_DEFAULT
    record_channels: list[str] | None = None  # None = all channels
    hls_dir: Path = Path("/var/lib/cztvhub/hls")
    state_dir: Path = Path("/var/lib/cztvhub")  # settings.json of the /admin page (service StateDirectory)
    buffer_dir: Path = Path("/var/lib/cztvhub/buffer")  # continuous recording; put it on the recordings disk
    max_recordings: int = 0  # optional cap on channels recorded at once; 0 = no limit (the household watches the load)
    buffer_on_system_disk: bool = False  # allow recording onto the system disk (SD card); development only
    idle_seconds: int = 30
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"


@dataclass
class AdsConfig:
    """Ad-break detection for "Anténa" recordings (spec/ad-breaks.md); off by default."""
    enabled: bool = False
    channels: list[str] | None = None  # None = all channels
    worker_key: str = ""
    worker_hosts: list[str] = field(default_factory=list)  # empty = any source address
    breaks_dir: Path = Path("/var/lib/cztvhub/breaks")


@dataclass
class AdminConfig:
    """Web administration at ``/admin`` (HTTP Basic, user ``admin``); off while the password is empty."""
    password: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.password)


@dataclass
class TmdbConfig:
    """Programme details from TMDb (spec/dvb-gateway.md "Programme details"); off while the key is empty."""
    key: str = field(default="", repr=False)  # own TMDb API key (v3) or read access token (v4); never logged

    @property
    def enabled(self) -> bool:
        return bool(self.key)


@dataclass
class Config:
    host: str = "0.0.0.0"
    port: int = 8098
    dvb: DvbConfig = field(default_factory=DvbConfig)
    ads: AdsConfig = field(default_factory=AdsConfig)
    admin: AdminConfig = field(default_factory=AdminConfig)
    tmdb: TmdbConfig = field(default_factory=TmdbConfig)
    tvheadend: TvheadendConfig = field(default_factory=TvheadendConfig)


def clamp_retention(days: int) -> int:
    return max(RETENTION_MIN, min(RETENTION_MAX, days))


def parse_record_channels(value: str) -> list[str] | None:
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names or (len(names) == 1 and names[0].lower() == "all"):
        return None
    return names


def _int(section: configparser.SectionProxy, key: str, default: int) -> int:
    try:
        return section.getint(key, default)
    except ValueError as exc:
        raise ConfigError(f"[{section.name}] {key}: not a number") from exc


def parse_config(text: str) -> Config:
    parser = configparser.ConfigParser(inline_comment_prefixes=(";", "#"), interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise ConfigError(str(exc)) from exc

    cfg = Config()
    if parser.has_section("server"):
        server = parser["server"]
        cfg.host = server.get("host", cfg.host).strip()
        cfg.port = _int(server, "port", cfg.port)

    if parser.has_section("dvb"):
        section = parser["dvb"]
        dvb = cfg.dvb
        try:
            dvb.enabled = section.getboolean("enabled", False)
        except ValueError as exc:
            raise ConfigError("[dvb] enabled: expected true/false") from exc
        dvb.key = section.get("key", "").strip()
        dvb.retention_days = clamp_retention(_int(section, "retention_days", RETENTION_DEFAULT))
        dvb.record_channels = parse_record_channels(section.get("record_channels", "all"))
        dvb.hls_dir = Path(section.get("hls_dir", str(dvb.hls_dir)).strip())
        dvb.state_dir = Path(section.get("state_dir", str(dvb.state_dir)).strip())
        dvb.buffer_dir = Path(section.get("buffer_dir", str(dvb.buffer_dir)).strip())
        dvb.max_recordings = max(0, _int(section, "max_recordings", dvb.max_recordings))
        try:
            dvb.buffer_on_system_disk = section.getboolean("buffer_on_system_disk", False)
        except ValueError as exc:
            raise ConfigError("[dvb] buffer_on_system_disk: expected true/false") from exc
        dvb.idle_seconds = max(1, _int(section, "idle_seconds", dvb.idle_seconds))
        dvb.ffmpeg = section.get("ffmpeg", dvb.ffmpeg).strip()
        dvb.ffprobe = section.get("ffprobe", dvb.ffprobe).strip()

    if parser.has_section("tvheadend"):
        section = parser["tvheadend"]
        tvh = cfg.tvheadend
        tvh.url = section.get("url", tvh.url).strip().rstrip("/")
        tvh.user = section.get("user", "").strip()
        tvh.password = section.get("password", "").strip()

    if parser.has_section("ads"):
        section = parser["ads"]
        ads = cfg.ads
        try:
            ads.enabled = section.getboolean("enabled", False)
        except ValueError as exc:
            raise ConfigError("[ads] enabled: expected true/false") from exc
        ads.channels = parse_record_channels(section.get("channels", "all"))
        ads.worker_key = section.get("worker_key", "").strip()
        ads.worker_hosts = [h.strip() for h in section.get("worker_hosts", "").split(",") if h.strip()]
        ads.breaks_dir = Path(section.get("breaks_dir", str(ads.breaks_dir)).strip())

    if parser.has_section("admin"):
        cfg.admin.password = parser["admin"].get("password", "").strip()

    if parser.has_section("tmdb"):
        cfg.tmdb.key = parser["tmdb"].get("key", "").strip()

    if cfg.dvb.enabled and len(cfg.dvb.key) < MIN_KEY_LENGTH:
        raise ConfigError(f"[dvb] key must be at least {MIN_KEY_LENGTH} characters when DVB is enabled")
    if cfg.ads.enabled:
        if not cfg.dvb.enabled:
            raise ConfigError("[ads] needs [dvb] enabled = true")
        if len(cfg.ads.worker_key) < MIN_KEY_LENGTH:
            raise ConfigError(f"[ads] worker_key must be at least {MIN_KEY_LENGTH} characters when enabled")
        if cfg.ads.worker_key == cfg.dvb.key:
            raise ConfigError("[ads] worker_key must differ from [dvb] key")
    if cfg.admin.enabled:
        if not cfg.dvb.enabled:
            raise ConfigError("[admin] needs [dvb] enabled = true")
        if len(cfg.admin.password) < MIN_ADMIN_PASSWORD_LENGTH:
            raise ConfigError(f"[admin] password must be at least {MIN_ADMIN_PASSWORD_LENGTH} characters")
        if cfg.admin.password in (cfg.dvb.key, cfg.ads.worker_key):
            raise ConfigError("[admin] password must differ from the app and worker keys")
    return cfg


def load_config(path: str | Path | None) -> Config:
    """Read the config file; ``None`` gives the defaults (CT only, DVB disabled)."""
    if path is None:
        return Config()
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    return parse_config(text)
