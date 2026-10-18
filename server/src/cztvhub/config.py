"""Server configuration (INI file, see spec/dvb-gateway.md). Without a file the server runs CT-only."""
from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path

MIN_KEY_LENGTH = 32
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
    idle_seconds: int = 30
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"


@dataclass
class Config:
    host: str = "0.0.0.0"
    port: int = 8098
    dvb: DvbConfig = field(default_factory=DvbConfig)
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
        dvb.idle_seconds = max(1, _int(section, "idle_seconds", dvb.idle_seconds))
        dvb.ffmpeg = section.get("ffmpeg", dvb.ffmpeg).strip()
        dvb.ffprobe = section.get("ffprobe", dvb.ffprobe).strip()

    if parser.has_section("tvheadend"):
        section = parser["tvheadend"]
        tvh = cfg.tvheadend
        tvh.url = section.get("url", tvh.url).strip().rstrip("/")
        tvh.user = section.get("user", "").strip()
        tvh.password = section.get("password", "").strip()

    if cfg.dvb.enabled and len(cfg.dvb.key) < MIN_KEY_LENGTH:
        raise ConfigError(f"[dvb] key must be at least {MIN_KEY_LENGTH} characters when DVB is enabled")
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
