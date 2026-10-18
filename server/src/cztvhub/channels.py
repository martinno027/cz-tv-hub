"""Channel catalogue. Ids are the ones used by the CT live-stream API."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Channel:
    id: str          # API id, e.g. "CH_1"
    name: str        # display name
    tvg_id: str      # EPG id used in the M3U
    number: int      # preferred channel number
    slug: str        # channel name in CT's programme (GraphQL) API, e.g. "ct1"


# CH_7 ("CT 3") exists in the API but its stream URL returned 404 on 2026-10-17;
# re-check with `python -m cztvhub check` before adding it back.
CHANNELS = [
    Channel("CH_1", "ČT1", "CT1.cz", 1, "ct1"),
    Channel("CH_2", "ČT2", "CT2.cz", 2, "ct2"),
    Channel("CH_24", "ČT24", "CT24.cz", 24, "ct24"),
    Channel("CH_4", "ČT sport", "CTSport.cz", 4, "sport"),
    Channel("CH_5", "ČT :D", "CTDecko.cz", 5, "decko"),
    Channel("CH_6", "ČT art", "CTArt.cz", 6, "art"),
]

BY_ID = {c.id: c for c in CHANNELS}
