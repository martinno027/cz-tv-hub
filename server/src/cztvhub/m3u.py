"""M3U playlist generation for Kodi's PVR IPTV Simple Client."""
from .channels import CHANNELS, Channel

GROUP = "Ceska televize"


def stream_path(channel: Channel) -> str:
    return f"/ct/{channel.id}.m3u8"


def build_playlist(base_url: str, channels=CHANNELS) -> str:
    """Return an M3U whose entries point at this server's redirect endpoints.

    `base_url` has no trailing slash, e.g. ``http://192.168.1.50:8098``.
    """
    base_url = base_url.rstrip("/")
    lines = ["#EXTM3U"]
    for ch in channels:
        lines.append(
            f'#EXTINF:-1 tvg-id="{ch.tvg_id}" tvg-name="{ch.name}" '
            f'tvg-chno="{ch.number}" group-title="{GROUP}",{ch.name}'
        )
        lines.append(f"{base_url}{stream_path(ch)}")
    return "\n".join(lines) + "\n"
