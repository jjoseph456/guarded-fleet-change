"""Build a staged rollout plan: canary, staging waves, then production waves."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from .models import format_time, plan_digest
from .readiness import host_blockers


def select_hosts(fleet: list[dict[str, Any]], selector: dict[str, Any]) -> list[dict[str, Any]]:
    roles = set(selector.get("roles") or [])
    sites = set(selector.get("sites") or [])
    return [
        host
        for host in fleet
        if (not roles or host["role"] in roles) and (not sites or host["site"] in sites)
    ]


def max_wave_size(eligible_count: int, percent: int) -> int:
    return max(1, math.floor(eligible_count * percent / 100))


def _pack_waves(hosts: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    """Greedy packing that never places both members of an HA pair in one wave."""
    waves: list[list[dict[str, Any]]] = []
    for host in hosts:
        pair = host.get("ha_pair")
        for wave in waves:
            if len(wave) < size and not (pair and any(h.get("ha_pair") == pair for h in wave)):
                wave.append(host)
                break
        else:
            waves.append([host])
    return waves


def build_plan(
    change: dict[str, Any],
    fleet: list[dict[str, Any]],
    *,
    requested_by: str,
    start_at: datetime,
) -> dict[str, Any]:
    selected = select_hosts(fleet, change["selector"])
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for host in selected:
        reasons = host_blockers(host)
        if reasons:
            excluded.append({"host": host["name"], "reasons": reasons})
        else:
            eligible.append(host)

    order = lambda h: (h["environment"] != "staging", h["site"], h["role"], h["name"])
    eligible.sort(key=order)

    rollout = change["rollout"]
    canary_count = rollout.get("canary_count", 1)
    canary_pool = [h for h in eligible if h["environment"] == "staging" and not h.get("ha_pair")]
    canary = canary_pool[:canary_count]
    remaining = [h for h in eligible if h not in canary]

    size = max_wave_size(len(eligible), rollout["max_wave_percent"])
    staging = [h for h in remaining if h["environment"] == "staging"]
    production = [h for h in remaining if h["environment"] == "production"]

    waves: list[dict[str, Any]] = []
    if canary:
        waves.append({"kind": "canary", "environment": "staging", "hosts": [h["name"] for h in canary]})
    for env, group in (("staging", staging), ("production", production)):
        for packed in _pack_waves(group, size):
            waves.append({"kind": "wave", "environment": env, "hosts": [h["name"] for h in packed]})
    for index, wave in enumerate(waves, start=1):
        wave["index"] = index

    plan: dict[str, Any] = {
        "schema": "fleetguard.plan/v1",
        "change_id": change["id"],
        "title": change["title"],
        "playbook": change["playbook"],
        "requested_by": requested_by,
        "start_at": format_time(start_at),
        "max_wave_size": size,
        "pause_minutes_between_waves": rollout.get("pause_minutes_between_waves", 0),
        "health_checks": change.get("health_checks", []),
        "freeze_windows": change.get("freeze_windows", []) or [],
        "host_facts": {
            h["name"]: {
                "environment": h["environment"],
                "ha_pair": h.get("ha_pair"),
                "blockers": host_blockers(h),
            }
            for h in selected
        },
        "waves": waves,
        "excluded": excluded,
    }
    plan["plan_sha256"] = plan_digest(plan)
    return plan
