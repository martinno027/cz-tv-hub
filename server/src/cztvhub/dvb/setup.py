"""``cztvhub dvb-setup``: idempotently configure TVHeadend's DVR for the household.

- default DVR profile: log and file retention = ``retention_days``
- one autorec rule per recorded channel that records every EPG event (rules are tagged
  with the comment ``cztvhub``; rules for channels no longer configured are deleted)
- a channel disabled in TVHeadend (hidden in the admin page) keeps its rule, disabled, so showing it
  again resumes recording; its rule is enabled again once the channel is

TVHeadend specifics (checked in its source, src/dvr/dvr_config.c, dvr.h, dvr_autorec.c,
api/api_dvr.c): ``retention-days`` / ``removal-days`` are day counts offered as a list
(1, 3, 5, 7, 14, ...). We pick the smallest listed value >= retention_days as a backstop;
the gateway's retention loop deletes at the exact retention. ``dvr_autorec_cmp()`` accepts
a rule whose only criterion is the channel (it matches every event); the title regex "."
is set as well, which matches any titled event, in case a version rejects that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..config import DvbConfig
from .tvheadend import Channel, TvheadendClient

RULE_COMMENT = "cztvhub"
TVH_DAY_CHOICES = (1, 3, 5, 7, 14)  # DVR_RET_REM_1DAY .. DVR_RET_REM_2WEEK


def tvh_days(retention_days: int) -> int:
    return next((d for d in TVH_DAY_CHOICES if d >= retention_days), TVH_DAY_CHOICES[-1])


@dataclass
class SetupPlan:
    config_uuid: str = ""
    config_update: dict = field(default_factory=dict)
    create: list[dict] = field(default_factory=list)
    toggle: list[dict] = field(default_factory=list)  # {"uuid": rule, "enabled": bool}
    delete: list[str] = field(default_factory=list)
    unknown_channels: list[str] = field(default_factory=list)


def select_channels(channels: list[Channel], wanted: list[str] | None) -> tuple[list[Channel], list[str]]:
    if wanted is None:
        return channels, []
    # an enabled channel wins over a disabled duplicate of the same name
    by_name = {c.name.casefold(): c for c in sorted(channels, key=lambda c: c.enabled)}
    selected = [by_name[n.casefold()] for n in wanted if n.casefold() in by_name]
    unknown = [n for n in wanted if n.casefold() not in by_name]
    return selected, unknown


def default_config(configs: list[dict]) -> dict | None:
    return next((c for c in configs if not c.get("name")), configs[0] if configs else None)


def autorec_conf(channel: Channel, config_uuid: str) -> dict:
    conf = {
        "enabled": channel.enabled,
        "name": f"cztvhub: {channel.name}",
        "title": ".",
        "fulltext": False,
        "channel": channel.uuid,
        "comment": RULE_COMMENT,
    }
    if config_uuid:
        conf["config_name"] = config_uuid
    return conf


def plan_setup(client: TvheadendClient, dvb: DvbConfig) -> SetupPlan:
    plan = SetupPlan()
    config = default_config(client.dvr_configs())
    if config is None:
        raise RuntimeError("TVHeadend has no DVR profile")
    plan.config_uuid = str(config.get("uuid", ""))
    days = tvh_days(dvb.retention_days)
    if config.get("retention-days") != days or config.get("removal-days") != days:
        plan.config_update = {"uuid": plan.config_uuid, "retention-days": days, "removal-days": days}

    selected, plan.unknown_channels = select_channels(client.channels(include_disabled=True), dvb.record_channels)
    ours = [r for r in client.autorecs() if r.comment == RULE_COMMENT]
    covered = {r.channel for r in ours}
    wanted = {c.uuid: c for c in selected}
    plan.create = [autorec_conf(c, plan.config_uuid) for c in selected if c.uuid not in covered and c.enabled]
    plan.delete = [r.uuid for r in ours if r.channel not in wanted]
    # duplicates (e.g. after a manual copy): keep one rule per channel
    seen: set[str] = set()
    for rule in ours:
        if rule.channel in wanted:
            if rule.channel in seen:
                plan.delete.append(rule.uuid)
            elif rule.enabled != wanted[rule.channel].enabled:
                plan.toggle.append({"uuid": rule.uuid, "enabled": wanted[rule.channel].enabled})
            seen.add(rule.channel)
    return plan


def recording_set_up(client: TvheadendClient) -> bool:
    """``dvb-setup`` has run: at least one of our autorec rules exists (enabled or not)."""
    return any(r.comment == RULE_COMMENT for r in client.autorecs())


def apply_setup(client: TvheadendClient, plan: SetupPlan) -> None:
    if plan.config_update:
        client.save_node(plan.config_update)
    for conf in plan.create:
        client.create_autorec(conf)
    if plan.toggle:
        client.save_node(plan.toggle)
    client.delete_nodes(plan.delete)


def run_setup(client: TvheadendClient, dvb: DvbConfig, dry_run: bool = False,
              out: Callable[[str], None] = print) -> SetupPlan:
    plan = plan_setup(client, dvb)
    for name in plan.unknown_channels:
        out(f"WARN channel not found in TVHeadend: {name}")
    days = tvh_days(dvb.retention_days)
    if plan.config_update:
        out(f"DVR profile: info and file retention -> {days} days (gateway deletes after {dvb.retention_days})")
    else:
        out(f"DVR profile: retention already {days} days")
    for conf in plan.create:
        out(f"autorec: add rule for {conf['name'][len('cztvhub: '):]}")
    for change in plan.toggle:
        out(f"autorec: {'enable' if change['enabled'] else 'disable'} rule {change['uuid']} (channel hidden/shown)")
    for uuid in plan.delete:
        out(f"autorec: delete rule {uuid}")
    if not plan.create and not plan.delete and not plan.toggle:
        out("autorec: rules up to date")
    if not dry_run:
        apply_setup(client, plan)
    return plan
