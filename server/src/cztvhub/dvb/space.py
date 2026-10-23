"""What to delete from the continuous recording (spec/dvb-gateway.md "Retention of the recording").

Pure decisions, no I/O, so the rules are unit-tested:
1. Age always wins: every block that ended more than ``max_days`` (1..14) ago goes, whatever the free space.
2. Free space: while less than the reserve (10 % of the disk, at least 5 GB) would be free, the oldest block of
   the channel that currently holds the most hours goes, so every recorded channel keeps about the same last
   N hours. A channel's newest block (the one being written) is never touched.
3. If the reserve still cannot be reached, recording pauses (the gateway's hard stop; TVHeadend's own free-space
   setting does not cover these files).
"""
from __future__ import annotations

from dataclasses import dataclass

from .buffer import BlockInfo

DAY = 86400
MIN_DAYS, MAX_DAYS = 1, 14
RESERVE_SHARE = 0.10
RESERVE_MIN = 5 * 1000 ** 3
RESUME_MARGIN = 1000 ** 3  # resume recording once 1 GB more than the reserve is free


def clamp_days(days: int) -> int:
    return max(MIN_DAYS, min(MAX_DAYS, int(days)))


def reserve_bytes(total: int) -> int:
    return max(RESERVE_MIN, int(total * RESERVE_SHARE))


@dataclass
class Plan:
    delete: list[BlockInfo]
    shortfall: int  # bytes still missing to the reserve after all deletions (> 0 → pause recording)
    reasons: dict[str, str]  # channel/block → "age" | "space"


def plan(blocks: dict[str, list[BlockInfo]], now: float, max_days: int, free: int, total: int) -> Plan:
    days = clamp_days(max_days)
    cutoff = now - days * DAY
    delete: list[BlockInfo] = []
    reasons: dict[str, str] = {}
    left: dict[str, list[BlockInfo]] = {}
    gained = 0
    for channel, items in blocks.items():
        ordered = sorted(items, key=lambda b: b.start)
        keep = []
        for block in ordered:
            if block.end <= cutoff:  # age wins, whatever the space
                delete.append(block)
                reasons[f"{channel}/{block.name}"] = "age"
                gained += block.bytes
            else:
                keep.append(block)
        left[channel] = keep
    need = reserve_bytes(total) - (free + gained)
    while need > 0:
        # the channel with the most hours that still has a block besides the one being written
        candidates = [(sum(b.seconds for b in items), channel) for channel, items in left.items() if len(items) > 1]
        if not candidates:
            break
        _, channel = max(candidates)
        block = left[channel].pop(0)
        delete.append(block)
        reasons[f"{channel}/{block.name}"] = "space"
        need -= block.bytes
    return Plan(delete, max(0, need), reasons)


def hours_per_channel(blocks: dict[str, list[BlockInfo]], free: int, total: int, max_days: int) -> float | None:
    """Rough capacity at the measured bitrates: hours of each recorded channel the disk holds with the reserve
    kept (capped by the day limit)."""
    rates = []
    used = 0
    for items in blocks.values():
        seconds = sum(b.seconds for b in items)
        size = sum(b.bytes for b in items)
        used += size
        if seconds >= 600 and size > 0:
            rates.append(size / seconds)
    if not rates:
        return None
    usable = max(0, free + used - reserve_bytes(total))
    hours = usable / sum(rates) / 3600
    return round(min(hours, clamp_days(max_days) * 24), 1)
