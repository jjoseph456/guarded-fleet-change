"""Loading and hashing helpers for fleets, change requests, and plans."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REQUIRED_HOST_FIELDS = (
    "name",
    "site",
    "role",
    "environment",
    "os",
    "monitoring",
    "backup_age_hours",
)
ENVIRONMENTS = {"staging", "production"}


def parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO 8601 timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO 8601 timestamp.") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone.")
    return parsed.astimezone(timezone.utc)


def format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_fleet(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    hosts = data.get("hosts") if isinstance(data, dict) else None
    if not isinstance(hosts, list) or not hosts:
        raise ValueError("Fleet file must contain a non-empty hosts array.")
    seen: set[str] = set()
    for host in hosts:
        if not isinstance(host, dict):
            raise ValueError("Each host must be an object.")
        for field in REQUIRED_HOST_FIELDS:
            if field not in host:
                raise ValueError(f"{host.get('name', 'host')}: {field} is required.")
        if host["environment"] not in ENVIRONMENTS:
            raise ValueError(
                f"{host['name']}: environment must be one of {sorted(ENVIRONMENTS)}."
            )
        if host["name"] in seen:
            raise ValueError(f"Duplicate host name: {host['name']}.")
        seen.add(host["name"])
    return hosts


def load_change(path: Path) -> dict[str, Any]:
    change = yaml.safe_load(path.read_text())
    if not isinstance(change, dict):
        raise ValueError("Change request must be a mapping.")
    for field in ("id", "title", "playbook", "selector", "rollout"):
        if field not in change:
            raise ValueError(f"Change request is missing {field}.")
    rollout = change["rollout"]
    canary = rollout.get("canary_count", 1)
    percent = rollout.get("max_wave_percent")
    if not isinstance(canary, int) or canary < 1:
        raise ValueError("rollout.canary_count must be a positive integer.")
    if not isinstance(percent, int) or not 1 <= percent <= 100:
        raise ValueError("rollout.max_wave_percent must be an integer from 1 to 100.")
    for window in change.get("freeze_windows", []) or []:
        start = parse_time(window.get("start"), "freeze_windows.start")
        end = parse_time(window.get("end"), "freeze_windows.end")
        if end <= start:
            raise ValueError("freeze window end must be after its start.")
    return change


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def plan_digest(plan: dict[str, Any]) -> str:
    body = {key: value for key, value in plan.items() if key != "plan_sha256"}
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def load_plan(path: Path) -> dict[str, Any]:
    plan = json.loads(path.read_text())
    if not isinstance(plan, dict) or "plan_sha256" not in plan:
        raise ValueError("Plan file is not a fleetguard plan.")
    if plan_digest(plan) != plan["plan_sha256"]:
        raise ValueError("Plan content does not match its recorded digest; it was modified.")
    return plan


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")
